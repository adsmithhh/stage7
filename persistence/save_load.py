from __future__ import annotations

import itertools
import json
import random
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import config.constants as constants
import emx.api as emx_system
import rendering.api as rendering
import simulation.api as simulation
import tuning.api as tuning


SAVE_DIR = constants.BASE_DIR / "data" / "saves"
SAVE_FORMAT_VERSION = 1


def get_save_path(slot: int) -> Path:
    return SAVE_DIR / f"simulation_slot_{slot}.json"


def get_slot_status(slot: int) -> str:
    path = get_save_path(slot)
    if not path.exists():
        return "EMPTY"
    try:
        with path.open("r", encoding="utf-8") as handle:
            # Efficiently read just the start of the file for the tick
            data = json.load(handle)
            return f"Tick: {data.get('global_tick', 0)}"
    except Exception:
        return "CORRUPT"


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, tuple):
        return {"__type__": "tuple", "items": [_encode(item) for item in value]}
    if isinstance(value, set):
        return {"__type__": "set", "items": [_encode(item) for item in sorted(value, key=repr)]}
    if isinstance(value, deque):
        return {"__type__": "deque", "items": [_encode(item) for item in value], "maxlen": value.maxlen}
    if isinstance(value, defaultdict):
        factory_name = None
        if value.default_factory is int:
            factory_name = "int"
        return {
            "__type__": "defaultdict",
            "factory": factory_name,
            "items": {str(key): _encode(item) for key, item in value.items()},
        }
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, simulation.Commitment):
        return {"__type__": "Commitment", "state": _encode(value.__dict__)}
    if isinstance(value, simulation.GameEvent):
        return {"__type__": "GameEvent", "state": _encode(value.__dict__)}
    if isinstance(value, simulation.ZoneStats):
        return {"__type__": "ZoneStats", "state": _encode(value.__dict__)}
    if isinstance(value, simulation.WorkBasin):
        return {"__type__": "WorkBasin", "state": _encode(value.__dict__)}
    if isinstance(value, simulation.CentralDoctrineState):
        return {"__type__": "CentralDoctrineState", "state": _encode(value.__dict__)}
    if isinstance(value, simulation.SimulationState):
        return {"__type__": "SimulationState", "state": _encode(value.__dict__)}
    if isinstance(value, simulation.EnvironmentState):
        return {"__type__": "EnvironmentState", "state": _encode(value.__dict__)}
    if isinstance(value, simulation.WorldRuntimeState):
        return {
            "__type__": "WorldRuntimeState",
            "sim_state": _encode(value.sim_state),
            "env_state": _encode(value.env_state),
        }
    # Note: RenderState is currently transient (caches) and not serialized to keep saves lean.
    raise TypeError(f"Unsupported snapshot value: {type(value)!r}")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value

    marker = value.get("__type__")
    if marker == "tuple":
        return tuple(_decode(item) for item in value["items"])
    if marker == "set":
        return set(_decode(item) for item in value["items"])
    if marker == "deque":
        return deque((_decode(item) for item in value["items"]), maxlen=value.get("maxlen"))
    if marker == "defaultdict":
        factory = int if value.get("factory") == "int" else None
        restored = defaultdict(factory)
        restored.update({key: _decode(item) for key, item in value["items"].items()})
        return restored
    if marker == "Commitment":
        return simulation.Commitment(**_decode(value["state"]))
    if marker == "GameEvent":
        return simulation.GameEvent(**_decode(value["state"]))
    if marker == "ZoneStats":
        state = _decode(value["state"])
        restored = simulation.ZoneStats(name=state["name"])
        restored.__dict__.update(state)
        return restored
    if marker == "WorkBasin":
        state = _decode(value["state"])
        restored = simulation.WorkBasin(zone_name=state["zone_name"])
        restored.__dict__.update(state)
        return restored
    if marker == "CentralDoctrineState":
        state = _decode(value["state"])
        restored = simulation.CentralDoctrineState()
        restored.__dict__.update(state)
        return restored
    if marker == "SimulationState":
        state = _decode(value["state"])
        restored = simulation.SimulationState()
        restored.__dict__.update(state)
        return restored
    if marker == "EnvironmentState":
        state = _decode(value["state"])
        restored = simulation.EnvironmentState()
        restored.__dict__.update(state)
        return restored
    if marker == "WorldRuntimeState":
        restored = simulation.WorldRuntimeState()
        restored.sim_state = _decode(value["sim_state"])
        restored.env_state = _decode(value["env_state"])
        restored.render_state.clear()
        return restored
    return {key: _decode(item) for key, item in value.items()}


def _serialize_npc(npc: simulation.NPC) -> dict[str, Any]:
    return {"state": _encode(npc.__dict__)}


def _deserialize_npc(data: dict[str, Any]) -> simulation.NPC:
    state = _decode(data["state"])
    npc = simulation.NPC(x=float(state["x"]), y=float(state["y"]))
    npc.__dict__.update(state)
    return npc


def _serialize_world(world: simulation.World) -> dict[str, Any]:
    return {
        "name": world.name,
        "dissonance_threshold": world.dissonance_threshold,
        "zone_modifiers": _encode(world.zone_modifiers),
        "npcs": [_serialize_npc(npc) for npc in world.npcs],
        "zone_stats": {zone: _encode(stats) for zone, stats in world.zone_stats.items()},
        "active_events": [_encode(event) for event in world.active_events],
        "runtime_state": _encode(world.runtime_state),
    }


def _deserialize_world(data: dict[str, Any]) -> simulation.World:
    world = simulation.World(name=str(data["name"]))
    world.dissonance_threshold = float(data["dissonance_threshold"])
    world.zone_modifiers = _decode(data["zone_modifiers"])
    world.npcs = [_deserialize_npc(npc_data) for npc_data in data["npcs"]]
    world.zone_stats = {zone: _decode(stats) for zone, stats in data["zone_stats"].items()}
    world.active_events = [_decode(event) for event in data["active_events"]]
    if "runtime_state" in data:
        world.runtime_state = _decode(data["runtime_state"])
    return world


def _serialize_ui_state(ui_state: simulation.UIState) -> dict[str, Any]:
    payload = dict(ui_state.__dict__)
    payload.pop("startup_messages", None)
    payload.pop("startup_until_ms", None)
    return _encode(payload)


def _restore_ui_state(ui_state: simulation.UIState, data: dict[str, Any]) -> None:
    restored = _decode(data)
    restored.pop("startup_messages", None)
    restored.pop("startup_until_ms", None)
    ui_state.__dict__.update(restored)
    ui_state.startup_messages = []
    ui_state.startup_until_ms = 0


def _serialize_pantheon() -> dict[str, Any]:
    pantheon = constants.PANTHEON
    return {
        "tier": pantheon.tier,
        "material_banked": pantheon.material_banked,
        "energy_banked": pantheon.energy_banked,
        "total_contributors": pantheon.total_contributors,
        "total_material_ever": pantheon.total_material_ever,
        "last_tier_tick": pantheon.last_tier_tick,
    }


def _restore_pantheon(data: dict[str, Any]) -> None:
    pantheon = constants.PANTHEON
    pantheon.tier = int(data["tier"])
    pantheon.material_banked = float(data["material_banked"])
    pantheon.energy_banked = float(data["energy_banked"])
    pantheon.total_contributors = int(data["total_contributors"])
    pantheon.total_material_ever = float(data["total_material_ever"])
    pantheon.last_tier_tick = int(data["last_tier_tick"])


def _serialize_weather() -> dict[str, Any]:
    return {
        "current_preset": constants.EMX_WEATHER_CURRENT_PRESET,
        "decay": constants.EMX_WEATHER_DECAY,
        "emit_rate": constants.EMX_WEATHER_EMIT_RATE,
        "cancel_ratio": constants.EMX_WEATHER_CANCEL_RATIO,
        "absorb_rate": constants.EMX_WEATHER_ABSORB_RATE,
    }


def _restore_weather(data: dict[str, Any]) -> None:
    preset_name = str(data["current_preset"])
    if preset_name in constants.EMX_WEATHER_PRESETS:
        constants.EMX_WEATHER_CURRENT_PRESET = preset_name
        constants.apply_weather_preset(preset_name)
    constants.EMX_WEATHER_DECAY = float(data["decay"])
    constants.EMX_WEATHER_EMIT_RATE = float(data["emit_rate"])
    constants.EMX_WEATHER_CANCEL_RATIO = float(data["cancel_ratio"])
    constants.EMX_WEATHER_ABSORB_RATE = float(data["absorb_rate"])
    emx_system.emx_runtime.EMX_WEATHER_DECAY = constants.EMX_WEATHER_DECAY
    emx_system.emx_runtime.EMX_WEATHER_EMIT_RATE = constants.EMX_WEATHER_EMIT_RATE
    emx_system.emx_runtime.EMX_WEATHER_CANCEL_RATIO = constants.EMX_WEATHER_CANCEL_RATIO
    emx_system.emx_runtime.EMX_WEATHER_ABSORB_RATE = constants.EMX_WEATHER_ABSORB_RATE


def _serialize_random_state() -> Any:
    return _encode(random.getstate())


def _restore_random_state(data: Any) -> None:
    random.setstate(_decode(data))


def save_snapshot(context, slot: int = 1) -> Path:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    save_path = get_save_path(slot)
    payload = {
        "format_version": SAVE_FORMAT_VERSION,
        "global_tick": context.global_tick,
        "worlds": [_serialize_world(world) for world in context.worlds],
        "ui_state": _serialize_ui_state(context.ui_state),
        "pantheon": _serialize_pantheon(),
        "weather": _serialize_weather(),
        "random_state": _serialize_random_state(),
    }
    with save_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return save_path


def load_snapshot(context, slot: int = 1, *, create_validator) -> Path:
    save_path = get_save_path(slot)
    if not save_path.exists():
        raise FileNotFoundError(f"Snapshot not found: {save_path}")

    with save_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if int(payload.get("format_version", 0)) != SAVE_FORMAT_VERSION:
        raise ValueError(f"Unsupported snapshot format: {payload.get('format_version')}")

    # Rebuild everything into temporary structures first so restore is atomic.
    restored_global_tick = int(payload["global_tick"])
    restored_worlds = [_deserialize_world(world_data) for world_data in payload["worlds"]]
    restored_ui_state = simulation.UIState()
    _restore_ui_state(restored_ui_state, payload["ui_state"])
    restored_ui_state.paused = True
    restored_ui_state.current_world_idx = max(
        0,
        min(len(restored_worlds) - 1, restored_ui_state.current_world_idx),
    )
    restored_validator = create_validator()
    restored_pantheon = payload["pantheon"]
    restored_weather = payload["weather"]
    restored_random_state = payload["random_state"]
    restored_next_npc_id = max((npc.id for world in restored_worlds for npc in world.npcs), default=0) + 1

    previous_global_tick = context.global_tick
    previous_worlds = context.worlds
    previous_ui_state = context.ui_state
    previous_validator = context.validator
    previous_pantheon = _serialize_pantheon()
    previous_weather = _serialize_weather()
    previous_random_state = _serialize_random_state()
    previous_next_npc_id = max((npc.id for world in previous_worlds for npc in world.npcs), default=0) + 1

    try:
        context.global_tick = restored_global_tick
        context.worlds = restored_worlds
        context.ui_state = restored_ui_state
        context.validator = restored_validator

        _restore_pantheon(restored_pantheon)
        _restore_weather(restored_weather)
        _restore_random_state(restored_random_state)
        simulation.reset_npc_id_gen(restored_next_npc_id)
        tuning.configure_runtime(worlds=context.worlds, anchors=context.anchors)

        # Reset transient rendering caches on load
        for world in context.worlds:
            world.runtime_state.render_state.clear()
    except Exception:
        context.global_tick = previous_global_tick
        context.worlds = previous_worlds
        context.ui_state = previous_ui_state
        context.validator = previous_validator
        _restore_pantheon(previous_pantheon)
        _restore_weather(previous_weather)
        _restore_random_state(previous_random_state)
        simulation.reset_npc_id_gen(previous_next_npc_id)
        tuning.configure_runtime(worlds=context.worlds, anchors=context.anchors)
        raise

    return save_path
