from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import config.constants as constants
import emx.api as emx_api
import simulation.api as simulation_api
from rendering.runtime_handles import field_runtime


Getter = Callable[[], float]
Setter = Callable[[float], None]

WORLDS: list | None = None
ANCHORS: dict | None = None


def configure_runtime(*, worlds: list | None = None, anchors: dict | None = None) -> None:
    global WORLDS, ANCHORS
    if worlds is not None:
        WORLDS = worlds
    if anchors is not None:
        ANCHORS = anchors


@dataclass
class TuningKnob:
    key: str
    label: str
    category: str
    minimum: float
    maximum: float
    step: float
    getter: Getter
    setter: Setter
    note: str = "live"
    value_type: type = float

    def value(self) -> float:
        return float(self.getter())

    def amplitude(self) -> float:
        span = self.maximum - self.minimum
        if abs(span) < 1e-12:
            return 0.0
        return max(0.0, min(1.0, (self.value() - self.minimum) / span))

    def set_value(self, value: float) -> None:
        value = max(self.minimum, min(self.maximum, value))
        if self.value_type is int:
            value = int(round(value))
        self.setter(value)

    def adjust(self, direction: int, fast: bool = False) -> None:
        self.set_value(self.value() + self.step * (10 if fast else 1) * direction)


def _set_attrs(names: list[tuple[object, str]], value: float) -> None:
    for module, attr in names:
        setattr(module, attr, value)


def _module_knob(
    key: str,
    label: str,
    category: str,
    minimum: float,
    maximum: float,
    step: float,
    targets: list[tuple[object, str]],
    note: str = "live",
) -> TuningKnob:
    module, attr = targets[0]
    return TuningKnob(
        key=key,
        label=label,
        category=category,
        minimum=minimum,
        maximum=maximum,
        step=step,
        getter=lambda module=module, attr=attr: getattr(module, attr),
        setter=lambda value, targets=targets: _set_attrs(targets, value),
        note=note,
    )


def _dict_knob(
    key: str,
    label: str,
    category: str,
    minimum: float,
    maximum: float,
    step: float,
    mapping: dict,
    path: tuple[str, ...],
    note: str = "live",
    value_type: type = float,
) -> TuningKnob:
    def read() -> float:
        current = mapping
        for part in path:
            current = current[part]
        return float(current)

    def write(value: float) -> None:
        current = mapping
        for part in path[:-1]:
            current = current[part]
        current[path[-1]] = value

    return TuningKnob(key, label, category, minimum, maximum, step, read, write, note, value_type)


def _zone_delta_knob(zone_name: str, delta_name: str) -> TuningKnob:
    key = f"init_{zone_name.lower()}_{delta_name}"
    label = f"{zone_name}_{delta_name.upper()}"

    def read() -> float:
        return float(constants.ZONE_FACTOR_PROFILES[zone_name]["deltas"][delta_name])

    def write(value: float) -> None:
        constants.ZONE_FACTOR_PROFILES[zone_name]["deltas"][delta_name] = value
        constants.config["zones"][zone_name][delta_name] = value

    return TuningKnob(
        key,
        label,
        "Init YAML",
        -0.05,
        0.05,
        0.001,
        read,
        write,
        note="live init5.yaml profile",
    )


def _zone_factor_knob(zone_name: str, factor_name: str) -> TuningKnob:
    key = f"init_{zone_name.lower()}_{factor_name.lower()}_eff"
    label = f"{zone_name}_{factor_name}_EFF"

    def read() -> float:
        return float(constants.ZONE_FACTOR_PROFILES[zone_name][factor_name]["efficiency"])

    def write(value: float) -> None:
        constants.ZONE_FACTOR_PROFILES[zone_name][factor_name]["efficiency"] = value
        work_modes = constants.config["zones"][zone_name].setdefault("work_output_modes", {})
        factor_cfg = work_modes.setdefault(factor_name, {})
        factor_cfg["efficiency"] = value

    return TuningKnob(
        key,
        label,
        "Init YAML",
        -3.0,
        3.0,
        0.05,
        read,
        write,
        note="live init5.yaml factor",
    )


def _init_yaml_knobs() -> list[TuningKnob]:
    knobs: list[TuningKnob] = []
    for zone_name, zone_cfg in constants.config.get("zones", {}).items():
        if zone_name not in constants.ZONE_FACTOR_PROFILES:
            continue
        for delta_name in ("money_delta", "stress_delta", "energy_delta"):
            if delta_name in constants.ZONE_FACTOR_PROFILES[zone_name].get("deltas", {}):
                knobs.append(_zone_delta_knob(zone_name, delta_name))
        for factor_name in (zone_cfg.get("work_output_modes") or {}):
            if factor_name in constants.FACTOR_NAMES:
                knobs.append(_zone_factor_knob(zone_name, factor_name))
    return knobs


def _initial_resources(zone_spawn_cfg: dict) -> dict:
    raw_resources = zone_spawn_cfg.get("initial_resources", {})
    stability = float(raw_resources.get("STABILITY", 50.0))
    return {
        "money": float(raw_resources.get("MATERIAL", 100.0)),
        "energy": float(raw_resources.get("ENERGY", 75.0)),
        "stress": max(0.0, 100.0 - stability),
    }


def _world_for_territory(territory_key: str):
    if WORLDS is None:
        return None
    territory_keys = list(constants.config.get("territories", {}).keys())
    if territory_key not in territory_keys:
        return None
    territory_idx = territory_keys.index(territory_key)
    if territory_idx >= len(WORLDS):
        return None
    return WORLDS[territory_idx]


def _resize_zone_population(territory_key: str, zone_name: str, desired_count: int) -> None:
    world = _world_for_territory(territory_key)
    if world is None or ANCHORS is None:
        return

    current = [npc for npc in world.npcs if npc.zone == zone_name or npc.target == zone_name]
    delta = desired_count - len(current)
    if delta > 0:
        home_x, home_y = ANCHORS["HOME"]
        zone_spawn_cfg = constants.config["territories"][territory_key]["zones"][zone_name]
        initial_resources = _initial_resources(zone_spawn_cfg)
        for _ in range(delta):
            world.npcs.append(simulation_api.spawn_npc(zone_name, initial_resources, home_x, home_y))
    elif delta < 0:
        remove_count = -delta
        candidates = [npc for npc in world.npcs if npc.zone == zone_name and npc.state != "TRAVELING"]
        candidate_ids = {npc.id for npc in candidates}
        candidates.extend(
            npc for npc in world.npcs
            if npc.target == zone_name and npc.id not in candidate_ids
        )
        remove_ids = {npc.id for npc in candidates[:remove_count]}
        world.npcs = [npc for npc in world.npcs if npc.id not in remove_ids]


def _population_knob(territory_key: str, territory_cfg: dict, zone_name: str, zone_spawn_cfg: dict) -> TuningKnob:
    label_name = str(territory_cfg.get("name", territory_key)).replace("ZONE ", "Z")
    key = f"pop_{territory_key.lower()}_{zone_name.lower()}"
    label = f"{label_name}_{zone_name}_NPCS"

    def read() -> float:
        world = _world_for_territory(territory_key)
        if world is not None:
            return float(sum(1 for npc in world.npcs if npc.zone == zone_name or npc.target == zone_name))
        return float(zone_spawn_cfg.get("npc_count", 0))

    def write(value: float) -> None:
        desired_count = max(0, int(round(value)))
        zone_spawn_cfg["npc_count"] = desired_count
        _resize_zone_population(territory_key, zone_name, desired_count)

    return TuningKnob(
        key,
        label,
        "Population",
        0.0,
        60.0,
        1.0,
        read,
        write,
        note="live population",
        value_type=int,
    )


def _population_knobs() -> list[TuningKnob]:
    knobs: list[TuningKnob] = []
    for territory_key, territory_cfg in constants.config.get("territories", {}).items():
        for zone_name, zone_spawn_cfg in territory_cfg.get("zones", {}).items():
            if zone_name in constants.WORK_ZONES:
                knobs.append(_population_knob(territory_key, territory_cfg, zone_name, zone_spawn_cfg))
    return knobs


def _travel_cost_knob(zone_name: str, resource: str) -> TuningKnob:
    key = f"travel_{zone_name.lower()}_{resource.lower()}"
    label = f"{zone_name}_TRAVEL_{resource.upper()}"
    config_key = "ENERGY_cost" if resource.upper() == "ENERGY" else "MATERIAL_cost"
    internal_key = "energy_cost_per_tick" if resource.upper() == "ENERGY" else "money_cost_per_tick"

    def read() -> float:
        return float(constants.ZONE_TRAVEL_COSTS[zone_name][internal_key])

    def write(value: float) -> None:
        constants.ZONE_TRAVEL_COSTS[zone_name][internal_key] = value
        # Update original config dict for future persistence if needed
        tc = constants.config.setdefault("zone_travel_costs", {}).setdefault(zone_name, {})
        tc[config_key] = value

    return TuningKnob(
        key,
        label,
        "Travel Costs",
        0.0,
        0.5,
        0.005,
        read,
        write,
        note="travel cost per tick",
    )


def _travel_cost_knobs() -> list[TuningKnob]:
    knobs: list[TuningKnob] = []
    for zone_name in constants.ZONE_TRAVEL_COSTS:
        knobs.append(_travel_cost_knob(zone_name, "ENERGY"))
        knobs.append(_travel_cost_knob(zone_name, "MATERIAL"))
    return knobs


def _weather_preset_knob(preset_name: str, param: str) -> TuningKnob:
    key = f"preset_{preset_name.lower()}_{param.lower()}"
    label = f"PRESET_{preset_name[:6]}_{param.upper()}"
    
    # Mapping for YAML keys
    param_map = {
        "decay": "decay",
        "emit": "emit_rate",
        "cancel": "cancel_ratio",
        "absorb": "absorb_rate"
    }
    yaml_key = param_map.get(param.lower(), param.lower())

    def read() -> float:
        return float(constants.EMX_WEATHER_PRESETS[preset_name].get(yaml_key, 0.0))

    def write(value: float) -> None:
        constants.EMX_WEATHER_PRESETS[preset_name][yaml_key] = value
        # If this is the active preset, we need to re-apply it
        if constants.EMX_WEATHER_CURRENT_PRESET == preset_name:
            constants.apply_weather_preset(preset_name)

    # Dynamic limits based on parameter
    limits = {
        "decay": (0.9, 1.0, 0.001),
        "emit": (0.0, 0.01, 0.0001),
        "cancel": (0.0, 1.0, 0.01),
        "absorb": (0.0, 0.01, 0.0001)
    }
    min_v, max_v, step_v = limits.get(param.lower(), (0.0, 1.0, 0.01))

    return TuningKnob(
        key,
        label,
        "Weather Presets",
        min_v,
        max_v,
        step_v,
        read,
        write,
        note=f"preset {preset_name} parameter",
    )


def _weather_preset_knobs() -> list[TuningKnob]:
    knobs: list[TuningKnob] = []
    for preset in constants.EMX_WEATHER_PRESETS:
        for param in ["decay", "emit", "cancel", "absorb"]:
            knobs.append(_weather_preset_knob(preset, param))
    return knobs


KNOBS: list[TuningKnob] = [
    _module_knob(
        "doctrine_decay",
        "DOCTRINE_DECAY",
        "Doctrine",
        0.0,
        0.08,
        0.001,
        [(constants, "DOCTRINE_DECAY"), (simulation_api.doctrine_runtime, "DOCTRINE_DECAY")],
    ),
    _module_knob(
        "doctrine_vote",
        "DOCTRINE_VOTE_RATE",
        "Doctrine",
        0.0,
        0.05,
        0.001,
        [(constants, "DOCTRINE_VOTE_RATE"), (simulation_api.doctrine_runtime, "DOCTRINE_VOTE_RATE")],
    ),
    _module_knob(
        "doctrine_deepen",
        "DOCTRINE_DEEPEN",
        "Doctrine",
        0.0,
        0.03,
        0.001,
        [(constants, "DOCTRINE_DEEPEN"), (simulation_api.doctrine_runtime, "DOCTRINE_DEEPEN")],
    ),
    _module_knob(
        "doctrine_threshold",
        "DOCTRINE_THRESHOLD",
        "Doctrine",
        20.0,
        500.0,
        5.0,
        [(constants, "DOCTRINE_THRESHOLD"), (simulation_api.doctrine_runtime, "DOCTRINE_THRESHOLD"), (field_runtime, "DOCTRINE_THRESHOLD")],
    ),
    _module_knob(
        "weather_decay",
        "EMX_WEATHER_DECAY",
        "EMX Weather",
        0.94,
        1.0,
        0.001,
        [(constants, "EMX_WEATHER_DECAY"), (emx_api.emx_runtime, "EMX_WEATHER_DECAY")],
    ),
    _module_knob(
        "weather_emit",
        "EMX_WEATHER_EMIT_RATE",
        "EMX Weather",
        0.0,
        0.005,
        0.0001,
        [(constants, "EMX_WEATHER_EMIT_RATE"), (emx_api.emx_runtime, "EMX_WEATHER_EMIT_RATE")],
    ),
    _module_knob(
        "weather_cancel",
        "EMX_WEATHER_CANCEL_RATIO",
        "EMX Weather",
        0.0,
        1.0,
        0.01,
        [(constants, "EMX_WEATHER_CANCEL_RATIO"), (emx_api.emx_runtime, "EMX_WEATHER_CANCEL_RATIO")],
    ),
    _module_knob(
        "weather_absorb",
        "EMX_WEATHER_ABSORB_RATE",
        "EMX Weather",
        0.0,
        0.003,
        0.0001,
        [(constants, "EMX_WEATHER_ABSORB_RATE"), (emx_api.emx_runtime, "EMX_WEATHER_ABSORB_RATE")],
    ),
    _module_knob(
        "cell_decay",
        "EMX_CELL_BASE_DECAY",
        "EMX Cell Grid",
        0.0,
        0.12,
        0.002,
        [(constants, "EMX_CELL_BASE_DECAY"), (emx_api.emx_runtime, "EMX_CELL_BASE_DECAY")],
    ),
    _module_knob(
        "cell_emit",
        "EMX_CELL_EMIT_RATE",
        "EMX Cell Grid",
        0.0,
        0.1,
        0.002,
        [(constants, "EMX_CELL_EMIT_RATE"), (emx_api.emx_runtime, "EMX_CELL_EMIT_RATE")],
    ),
    _module_knob(
        "cell_mix",
        "EMX_CELL_NEIGHBOR_MIX",
        "EMX Cell Grid",
        0.0,
        0.35,
        0.005,
        [(constants, "EMX_CELL_NEIGHBOR_MIX"), (emx_api.emx_runtime, "EMX_CELL_NEIGHBOR_MIX")],
    ),
    _module_knob(
        "cell_cancel",
        "EMX_CELL_CANCEL_RATIO",
        "EMX Cell Grid",
        0.0,
        1.0,
        0.01,
        [(constants, "EMX_CELL_CANCEL_RATIO"), (emx_api.emx_runtime, "EMX_CELL_CANCEL_RATIO")],
    ),
    _module_knob(
        "cell_absorb",
        "EMX_CELL_ABSORB_RATE",
        "EMX Cell Grid",
        0.0,
        0.005,
        0.0001,
        [(constants, "EMX_CELL_ABSORB_RATE"), (emx_api.emx_runtime, "EMX_CELL_ABSORB_RATE")],
    ),
    _module_knob(
        "overdrive_threshold",
        "OVERDRIVE_THRESHOLD",
        "Collapse",
        0.5,
        1.0,
        0.01,
        [(constants, "OVERDRIVE_THRESHOLD"), (emx_api.emx_runtime, "OVERDRIVE_THRESHOLD")],
    ),
    _module_knob(
        "overuse_backlash_start",
        "EMX_OVERUSE_BACKLASH_START",
        "Collapse",
        5.0,
        80.0,
        1.0,
        [(constants, "EMX_OVERUSE_BACKLASH_START"), (emx_api.emx_runtime, "EMX_OVERUSE_BACKLASH_START")],
    ),
    _module_knob(
        "overuse_backlash_drain",
        "EMX_OVERUSE_BACKLASH_DRAIN",
        "Collapse",
        0.0,
        0.08,
        0.002,
        [(constants, "EMX_OVERUSE_BACKLASH_DRAIN"), (emx_api.emx_runtime, "EMX_OVERUSE_BACKLASH_DRAIN")],
    ),
    _module_knob(
        "overuse_backlash_gain",
        "EMX_OVERUSE_BACKLASH_GAIN",
        "Collapse",
        0.0,
        0.08,
        0.002,
        [(constants, "EMX_OVERUSE_BACKLASH_GAIN"), (emx_api.emx_runtime, "EMX_OVERUSE_BACKLASH_GAIN")],
    ),
    _module_knob(
        "degen_decay",
        "DEGEN_DECAY_RATE",
        "Collapse",
        0.0,
        0.01,
        0.0001,
        [(constants, "DEGEN_DECAY_RATE"), (emx_api.emx_runtime, "DEGEN_DECAY_RATE")],
    ),
    _module_knob(
        "decision_shame",
        "DECISION_SHAME_PENALTY",
        "Decision",
        0.0,
        5.0,
        0.05,
        [(constants, "DECISION_DISGUST_PENALTY_MULTIPLIER"), (simulation_api.simulation_runtime, "DECISION_DISGUST_PENALTY_MULTIPLIER")],
    ),
    _module_knob(
        "work_energy_loss",
        "WORK_ENERGY_LOSS_MULT",
        "Decision",
        0.0,
        5.0,
        0.05,
        [(constants, "DECISION_ENERGY_LOSS_MULTIPLIER"), (simulation_api.simulation_runtime, "DECISION_ENERGY_LOSS_MULTIPLIER")],
    ),
    _module_knob(
        "home_recovery",
        "HOME_RECOVERY_BONUS",
        "Decision",
        0.0,
        2.0,
        0.025,
        [(constants, "DECISION_HOME_RECOVERY_BONUS"), (simulation_api.simulation_runtime, "DECISION_HOME_RECOVERY_BONUS")],
    ),
    _module_knob(
        "zone_lock",
        "ZONE_LOCK_THRESHOLD",
        "Decision",
        50.0,
        1500.0,
        25.0,
        [(constants, "ZONE_LOCK_THRESHOLD"), (simulation_api.simulation_runtime, "ZONE_LOCK_THRESHOLD")],
    ),
    _module_knob(
        "pantheon_material",
        "PANTHEON_CONTRIB_MATERIAL",
        "Pantheon",
        0.0,
        1.0,
        0.01,
        [(constants, "PANTHEON_CONTRIB_MATERIAL"), (simulation_api.simulation_runtime, "PANTHEON_CONTRIB_MATERIAL")],
    ),
    _module_knob(
        "pantheon_energy",
        "PANTHEON_CONTRIB_ENERGY",
        "Pantheon",
        0.0,
        0.5,
        0.005,
        [(constants, "PANTHEON_CONTRIB_ENERGY"), (simulation_api.simulation_runtime, "PANTHEON_CONTRIB_ENERGY")],
    ),
    _dict_knob("central_visit_min", "CENTRAL_MIN_VISIT", "Shared Zones", 1.0, 400.0, 5.0, constants.SHARED_ZONE_CONFIG, ("CENTRAL", "min_visit_ticks"), value_type=int),
    _dict_knob("central_visit_max", "CENTRAL_MAX_VISIT", "Shared Zones", 1.0, 600.0, 5.0, constants.SHARED_ZONE_CONFIG, ("CENTRAL", "max_visit_ticks"), value_type=int),
    _dict_knob("central_social", "CENTRAL_SOCIAL_THRESHOLD", "Shared Zones", 0.0, 1.0, 0.01, constants.SHARED_ZONE_CONFIG, ("CENTRAL", "social_threshold")),
    _dict_knob("central_energy", "CENTRAL_ENERGY_GAIN", "Shared Zones", 0.0, 0.05, 0.001, constants.SHARED_ZONE_CONFIG, ("CENTRAL", "energy_gain_per_tick")),
    _dict_knob("central_stress", "CENTRAL_STRESS_LOSS", "Shared Zones", 0.0, 0.05, 0.001, constants.SHARED_ZONE_CONFIG, ("CENTRAL", "stress_loss_per_tick")),
    _dict_knob("pantheon_stress", "PANTHEON_STRESS_GAIN", "Shared Zones", 0.0, 0.05, 0.001, constants.SHARED_ZONE_CONFIG, ("PANTHEON", "stress_gain_tick")),
    _module_knob(
        "crystal_threshold",
        "C_CRYSTAL_THRESHOLD",
        "C Panel",
        100.0,
        5000.0,
        50.0,
        [(field_runtime, "_CRYSTAL_THRESHOLD")],
    ),
    _module_knob(
        "sat_threshold",
        "C_SAT_THRESHOLD",
        "C Panel",
        100.0,
        8000.0,
        50.0,
        [(field_runtime, "_SAT_THRESHOLD")],
    ),
    _module_knob(
        "anchor_duration",
        "C_ANCHOR_DURATION",
        "C Panel",
        5.0,
        300.0,
        5.0,
        [(field_runtime, "ANCHOR_DURATION")],
    ),
    _module_knob(
        "anchor_radius",
        "C_ANCHOR_RADIUS",
        "C Panel",
        0.5,
        10.0,
        0.25,
        [(field_runtime, "ANCHOR_RADIUS")],
    ),
    _module_knob(
        "anchor_emit",
        "C_ANCHOR_BASE_EMIT",
        "C Panel",
        0.0,
        0.3,
        0.005,
        [(field_runtime, "ANCHOR_BASE_EMIT")],
    ),
    _module_knob(
        "anchor_scale",
        "C_ANCHOR_EMIT_SCALE",
        "C Panel",
        0.0,
        10.0,
        0.1,
        [(field_runtime, "ANCHOR_EMIT_SCALE")],
    ),
] + _init_yaml_knobs() + _population_knobs() + _travel_cost_knobs() + _weather_preset_knobs()


def categories() -> list[str]:
    out: list[str] = []
    for knob in KNOBS:
        if knob.category not in out:
            out.append(knob.category)
    return out


def page_for(category_index: int) -> list[TuningKnob]:
    cats = categories()
    if not cats:
        return KNOBS
    category = cats[category_index % len(cats)]
    return [knob for knob in KNOBS if knob.category == category]


def category_name(category_index: int) -> str:
    cats = categories()
    if not cats:
        return "All"
    return cats[category_index % len(cats)]


def normalize_indices(category_index: int, item_index: int) -> tuple[int, int]:
    cats = categories()
    if not cats:
        return 0, 0
    category_index = max(0, min(len(cats) - 1, category_index))
    page = page_for(category_index)
    if not page:
        return category_index, 0
    return category_index, max(0, min(len(page) - 1, item_index))
