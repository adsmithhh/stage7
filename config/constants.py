from __future__ import annotations
import os
from typing import Dict, List, Tuple
from pathlib import Path

import yaml

from .emotion_runtime import load_emotion_schema

# --- Standardized Base Directory ---
BASE_DIR = Path(__file__).parent.resolve()

# --- Load YAML config ---
CONFIG_PATH = BASE_DIR / "int5.yaml"
if not CONFIG_PATH.exists():
    # Fallback to current directory only if not found in script dir
    CONFIG_PATH = Path("int5.yaml")
if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"int5.yaml not found at {BASE_DIR / 'int5.yaml'}")

try:
    with open(CONFIG_PATH, "r", encoding='utf-8-sig') as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict) or not config:
        raise ValueError("Config empty or invalid")
except yaml.YAMLError as e:
    raise ValueError(f"Invalid YAML: {e}") from e
except RuntimeError:
    raise
except Exception as e:
    raise RuntimeError(f"Failed to load config from {CONFIG_PATH}: {e}") from e

EMX_WEATHER_CONFIG = config.get("emx_weather") or {}
EMX_WEATHER_DECAY = float(EMX_WEATHER_CONFIG.get("decay", 0.995))
EMX_WEATHER_EMIT_RATE = float(EMX_WEATHER_CONFIG.get("emit_rate", 0.0008))
EMX_WEATHER_CANCEL_RATIO = float(EMX_WEATHER_CONFIG.get("cancel_ratio", 0.4))
EMX_WEATHER_ABSORB_RATE = float(EMX_WEATHER_CONFIG.get("absorb_rate", 0.0003))
EMX_WEATHER_HISTORY_LENGTH = int(EMX_WEATHER_CONFIG.get("history_length", 240))
EMX_WEATHER_PANEL_CONFIG = config.get("emx_weather_panel") or {}
EMX_WEATHER_PANEL_SECTORS = max(4, min(16, int(EMX_WEATHER_PANEL_CONFIG.get("sector_count", 8))))
EMX_CELL_GRID_CONFIG = config.get("emx_cell_grid") or {}
EMX_CELL_GRID_ENABLED = bool(EMX_CELL_GRID_CONFIG.get("enabled", True))
EMX_CELL_GRID_COLS = max(4, min(40, int(EMX_CELL_GRID_CONFIG.get("cols", 16))))
EMX_CELL_GRID_ROWS = max(3, min(24, int(EMX_CELL_GRID_CONFIG.get("rows", 9))))
EMX_CELL_BASE_DECAY = float(EMX_CELL_GRID_CONFIG.get("base_decay", 0.030))
EMX_CELL_EMPTY_EXTRA_DECAY = float(EMX_CELL_GRID_CONFIG.get("empty_extra_decay", 0.012))
EMX_CELL_EMIT_RATE = float(EMX_CELL_GRID_CONFIG.get("emit_rate", 0.02))
EMX_CELL_NEIGHBOR_MIX = float(EMX_CELL_GRID_CONFIG.get("neighbor_mix", 0.08))
EMX_CELL_CANCEL_RATIO = float(EMX_CELL_GRID_CONFIG.get("cancel_ratio", 0.35))
EMX_CELL_OCCUPANCY_REF = max(1.0, float(EMX_CELL_GRID_CONFIG.get("occupancy_reference", 3.0)))
EMX_CELL_ABSORB_RATE = float(EMX_CELL_GRID_CONFIG.get("absorb_rate", 0.0009))
EMX_CELL_OVERLAY_ALPHA = max(0, min(120, int(EMX_CELL_GRID_CONFIG.get("overlay_alpha", 38))))

NPC_STATE_EXPORT_CONFIG = config.get("npc_state_export") or {}
NPC_STATE_EXPORT_ENABLED = bool(NPC_STATE_EXPORT_CONFIG.get("enabled", True))
NPC_STATE_START_SNAPSHOT_TICK = int(NPC_STATE_EXPORT_CONFIG.get("start_snapshot_tick", 100))
NPC_STATE_EXPORT_DIR = str(NPC_STATE_EXPORT_CONFIG.get("output_dir", "data/npc_states"))

EMX_COMPLEX_CONFIG = config.get("emx_complexes") or {}
EMX_COMPLEX_TOP_THRESHOLD = float(EMX_COMPLEX_CONFIG.get("top_threshold", 0.3))
EMX_COMPLEX_SECOND_THRESHOLD = float(EMX_COMPLEX_CONFIG.get("second_threshold", 0.2))
EMX_COMPLEX_ZONE_THRESHOLD_SCALE = {
    zone: float(scale)
    for zone, scale in (EMX_COMPLEX_CONFIG.get("zone_threshold_scale") or {}).items()
}

DECISION_CONFIG = config.get("decision") or {}
DECISION_DISGUST_PENALTY_MULTIPLIER = float(DECISION_CONFIG.get("disgust_penalty_multiplier", 1.5))
DECISION_ENERGY_LOSS_MULTIPLIER = float(DECISION_CONFIG.get("energy_loss_rate_work_multiplier", 3.0))
DECISION_HOME_RECOVERY_BONUS = float(DECISION_CONFIG.get("home_recovery_bonus", 2.0))

EMX_WEATHER_PRESETS = config.get("emx_weather_presets") or {
    "balanced": {
        "decay": EMX_WEATHER_DECAY,
        "emit_rate": EMX_WEATHER_EMIT_RATE,
        "cancel_ratio": EMX_WEATHER_CANCEL_RATIO,
        "absorb_rate": EMX_WEATHER_ABSORB_RATE,
    }
}
EMX_WEATHER_PRESET_ORDER = list(EMX_WEATHER_PRESETS.keys()) or ["balanced"]
EMX_WEATHER_CURRENT_PRESET = str(config.get("emx_weather_default_preset", EMX_WEATHER_PRESET_ORDER[0]))
if EMX_WEATHER_CURRENT_PRESET not in EMX_WEATHER_PRESETS:
    EMX_WEATHER_CURRENT_PRESET = EMX_WEATHER_PRESET_ORDER[0]

RENDERING_CONFIG = config.get("rendering") or {}
RENDER_BACKEND = str(os.environ.get("STAGE7_RENDER_BACKEND", RENDERING_CONFIG.get("backend", "software"))).strip().lower()
if RENDER_BACKEND not in {"software", "auto", "opengl"}:
    RENDER_BACKEND = "software"


def apply_weather_preset(preset_name: str):
    global EMX_WEATHER_DECAY, EMX_WEATHER_EMIT_RATE, EMX_WEATHER_CANCEL_RATIO, EMX_WEATHER_ABSORB_RATE
    preset = EMX_WEATHER_PRESETS.get(preset_name)
    if not isinstance(preset, dict):
        return

    EMX_WEATHER_DECAY = float(preset.get("decay", EMX_WEATHER_DECAY))
    EMX_WEATHER_EMIT_RATE = float(preset.get("emit_rate", EMX_WEATHER_EMIT_RATE))
    EMX_WEATHER_CANCEL_RATIO = float(preset.get("cancel_ratio", EMX_WEATHER_CANCEL_RATIO))
    EMX_WEATHER_ABSORB_RATE = float(preset.get("absorb_rate", EMX_WEATHER_ABSORB_RATE))


apply_weather_preset(EMX_WEATHER_CURRENT_PRESET)


def describe_weather_preset() -> str:
    return (
        f"🌦️ Weather preset: {EMX_WEATHER_CURRENT_PRESET} "
        f"(decay={EMX_WEATHER_DECAY}, emit={EMX_WEATHER_EMIT_RATE}, "
        f"cancel={EMX_WEATHER_CANCEL_RATIO}, absorb={EMX_WEATHER_ABSORB_RATE})"
    )

FACTOR_NAMES = ["MATERIAL", "ENERGY", "SOCIAL", "KNOWLEDGE", "STABILITY"]

# Zone constants (single source of truth for zone names)
WORK_ZONES = ["SCIENCE", "TRADE", "DEVELOPMENT", "FLEX", "CENTRAL", "PANTHEON"]
SHARED_ZONES = ["CENTRAL", "PANTHEON"]  # cross-territory buildings all NPCs can visit
ALL_ZONES = WORK_ZONES + ["HOME"]

# ── PANTHEON PYRAMID CONSTRUCTION ────────────────────────────────────────────
# Global monument: all worlds contribute. Super-slow accumulation by design.
PANTHEON_TIERS = [
    {"name": "Foundation",   "material_req": 600.0,   "energy_req": 300.0},
    {"name": "Base Level",   "material_req": 2000.0,  "energy_req": 900.0},
    {"name": "Mid Chamber",  "material_req": 7000.0,  "energy_req": 3000.0},
    {"name": "Upper Hall",   "material_req": 22000.0, "energy_req": 9000.0},
    {"name": "Apex",         "material_req": 70000.0, "energy_req": 28000.0},
]
# Per-NPC contribution per tick while AT_WORK at PANTHEON
PANTHEON_CONTRIB_MATERIAL = 0.18   # drained from npc.money
PANTHEON_CONTRIB_ENERGY   = 0.06   # drained from npc.energy

class PantheonConstruction:
    def __init__(self):
        self.tier: int           = 0        # current tier index
        self.material_banked: float = 0.0   # progress toward current tier
        self.energy_banked: float   = 0.0
        self.total_contributors: int = 0    # cumulative NPC visits that contributed
        self.total_material_ever: float = 0.0
        self.last_tier_tick: int = 0        # tick when last tier completed

    @property
    def current_tier_cfg(self):
        if self.tier < len(PANTHEON_TIERS):
            return PANTHEON_TIERS[self.tier]
        return PANTHEON_TIERS[-1]  # beyond apex — keeps accumulating

    @property
    def material_progress(self) -> float:
        """0.0 – 1.0 within the current tier."""
        req = self.current_tier_cfg["material_req"]
        return min(1.0, self.material_banked / req) if req > 0 else 1.0

    @property
    def energy_progress(self) -> float:
        req = self.current_tier_cfg["energy_req"]
        return min(1.0, self.energy_banked / req) if req > 0 else 1.0

    def contribute(self, material: float, energy: float, tick: int):
        self.material_banked      += material
        self.energy_banked        += energy
        self.total_material_ever  += material
        self.total_contributors   += 1
        # Advance tier when both thresholds met
        if self.tier < len(PANTHEON_TIERS):
            cfg = self.current_tier_cfg
            if (self.material_banked >= cfg["material_req"]
                    and self.energy_banked >= cfg["energy_req"]):
                self.material_banked -= cfg["material_req"]
                self.energy_banked   -= cfg["energy_req"]
                self.tier            += 1
                self.last_tier_tick   = tick
                print(f"  🏛 PANTHEON tier {self.tier} reached: "
                      f"{PANTHEON_TIERS[min(self.tier, len(PANTHEON_TIERS)-1)]['name']}  (tick {tick})")

PANTHEON = PantheonConstruction()
# ─────────────────────────────────────────────────────────────────────────────

# Emotional signature each anchor radiates into the local cell atmosphere.
# (primary_emotion, primary_weight, secondary_emotion, secondary_weight)
ANCHOR_EMX_SIGNATURE: Dict[str, list] = {
    "SCIENCE":     [("Pride", 0.70), ("Panic", 0.30)],
    "TRADE":       [("Anger", 0.60), ("Pride", 0.40)],
    "DEVELOPMENT": [("Calm", 0.65), ("Sadness", 0.35)],
    "FLEX":        [("Joy", 0.65), ("Calm", 0.35)],
    "HOME":        [("Calm", 0.60), ("Love", 0.40)],
    "CENTRAL":     [("Joy", 0.60), ("Love", 0.40)],
    "PANTHEON":    [("Pride", 0.55), ("Sadness", 0.45)],
}
ANCHOR_FIELD_EMIT_RATE = 0.011   # per-cell per-tick at field edge; peaks at centre

SHARED_ZONE_CONFIG = {
    "CENTRAL": {
        "min_visit_ticks":      50,     # minimum stay before NPC can leave
        "max_visit_ticks":      140,    # planned duration (randomised between min/max)
        "social_threshold":      0.38,  # social_need floor to trigger pull
        "social_decay_rate":     0.007, # social_need lost per tick while AT_WORK here
        "crowd_sweet_spot":      5,     # pop count where social benefit peaks
        "crowd_penalty_above":   9,     # overcrowding threshold → score penalty
        "energy_gain_per_tick":  0.012,
        "stress_loss_per_tick":  0.008,
    },
    "PANTHEON": {
        "min_visit_ticks":      70,
        "max_visit_ticks":      200,
        "entry_energy_min":     55.0,   # gated in allowed_work_zones
        "entry_money_min":      15.0,
        "prestige_cost_tick":    0.35,  # money drained per tick while inside
        "knowledge_gain_tick":   0.0015,
        "legacy_gain_tick":      0.001,
        "crowd_exclusive_max":   4,     # above this it feels crowded → less attractive
        "energy_drain_tick":     0.008,
        "stress_gain_tick":      0.003, # deep thinking has a mild stress cost
    },
}

# ── DOCTRINE SYSTEM ────────────────────────────────────────────────────────
DOCTRINE_TYPES = ["MERITOCRATIC", "TRANSCENDENT", "CONSPIRATORIAL", "REVOLUTIONARY", "LIBERTARIAN_CULT"]
DOCTRINE_THRESHOLD  = 180.0  # accumulated pressure to break through
DOCTRINE_DECAY      = 0.015  # pressure decay per tick with no votes
DOCTRINE_VOTE_RATE  = 0.008  # pressure added per NPC-tick at CENTRAL
DOCTRINE_DEEPEN     = 0.003  # doctrine_strength gain per tick while indoctrinated
DOCTRINE_COLORS: Dict[str, Tuple] = {
    "MERITOCRATIC":    (200, 180, 50),
    "TRANSCENDENT":    (160, 90, 220),
    "CONSPIRATORIAL":  (200, 55, 55),
    "REVOLUTIONARY":   (220, 90, 30),
    "LIBERTARIAN_CULT":(55, 185, 140),
}
DOCTRINE_SHORT = {
    "MERITOCRATIC":    "MERIT",
    "TRANSCENDENT":    "TRANSC",
    "CONSPIRATORIAL":  "CONSPIR",
    "REVOLUTIONARY":   "REVOLT",
    "LIBERTARIAN_CULT":"LIBERT",
}
# Which doctrine a personality type primarily radiates at CENTRAL
DOCTRINE_PERSONALITY_VOTE: Dict[str, str] = {
    "Anchor":   "TRANSCENDENT",
    "Climber":  "MERITOCRATIC",
    "Connector":"REVOLUTIONARY",
    "Survivor": "CONSPIRATORIAL",
}
# Base susceptibility per personality per doctrine (vulnerability table)
DOCTRINE_PERSONALITY_VULN: Dict[str, Dict[str, float]] = {
    "Anchor":   {"MERITOCRATIC":0.55,"TRANSCENDENT":0.75,"CONSPIRATORIAL":0.30,"REVOLUTIONARY":0.20,"LIBERTARIAN_CULT":0.35},
    "Climber":  {"MERITOCRATIC":0.85,"TRANSCENDENT":0.40,"CONSPIRATORIAL":0.30,"REVOLUTIONARY":0.45,"LIBERTARIAN_CULT":0.75},
    "Connector":{"MERITOCRATIC":0.40,"TRANSCENDENT":0.50,"CONSPIRATORIAL":0.65,"REVOLUTIONARY":0.80,"LIBERTARIAN_CULT":0.40},
    "Survivor": {"MERITOCRATIC":0.35,"TRANSCENDENT":0.70,"CONSPIRATORIAL":0.90,"REVOLUTIONARY":0.55,"LIBERTARIAN_CULT":0.30},
}
# Doctrine escape recovery durations (ticks)
DOCTRINE_RECOVERY_TICKS: Dict[str, int] = {
    "MERITOCRATIC":200,"TRANSCENDENT":400,"CONSPIRATORIAL":250,
    "REVOLUTIONARY":180,"LIBERTARIAN_CULT":220,
}

def build_zone_factor_profiles(cfg):
    profiles_by_zone = {}
    for zone_name, zone_cfg in cfg["zones"].items():
        zone_profiles = {factor: {"efficiency": 0.0} for factor in FACTOR_NAMES}
        work_modes = zone_cfg.get("work_output_modes", {})
        for factor_name, factor_data in work_modes.items():
            if factor_name not in FACTOR_NAMES:
                continue
            zone_profiles[factor_name] = {
                "efficiency": float(factor_data.get("efficiency", 0.0))
            }
        # Add deltas from YAML config under 'deltas' key
        zone_profiles["deltas"] = {
            "money_delta": float(zone_cfg.get("money_delta", 0.0)),
            "stress_delta": float(zone_cfg.get("stress_delta", 0.0)),
            "energy_delta": float(zone_cfg.get("energy_delta", 0.0)),
        }
        profiles_by_zone[zone_name] = zone_profiles
    return profiles_by_zone

def validate_factor_config(cfg) -> List[str]:
    diagnostics: List[str] = []
    configured_work_modes = set((cfg.get("work_modes") or {}).keys())
    missing_work_modes = [f for f in FACTOR_NAMES if f not in configured_work_modes]
    if missing_work_modes:
        diagnostics.append(f"⚠️ Missing work_modes definitions for factors: {missing_work_modes}")

    for zone_name, zone_cfg in cfg.get("zones", {}).items():
        configured_zone_factors = set((zone_cfg.get("work_output_modes") or {}).keys())
        unknown = sorted(configured_zone_factors - set(FACTOR_NAMES))
        if unknown:
            diagnostics.append(f"⚠️ Zone {zone_name} has unknown work_output_modes factors: {unknown}")

    return diagnostics

ZONE_FACTOR_PROFILES = build_zone_factor_profiles(config)
FACTOR_CONFIG_DIAGNOSTICS = validate_factor_config(config)


def describe_factor_profiles() -> List[str]:
    return [*FACTOR_CONFIG_DIAGNOSTICS, f"✅ Loaded zone factor profiles: {list(ZONE_FACTOR_PROFILES.keys())}"]
# Validate and normalize anchors structure
if 'anchors' in config:
    fixed_anchors = {}
    for name, anchor_config in config['anchors'].items():
        if isinstance(anchor_config, dict) and 'norm' in anchor_config:
            fixed_anchors[name] = anchor_config['norm']
        elif isinstance(anchor_config, list) and len(anchor_config) == 2:
            fixed_anchors[name] = anchor_config
        else:
            raise ValueError(f"Invalid anchor format for {name}: {anchor_config}")
    config['anchors'] = fixed_anchors

WIDTH = config["display"]["width"]
HEIGHT = config["display"]["height"]
FPS = config["display"]["fps"]

ANCHORS_NORM = config["anchors"]

ZONE_BASES_NORM = {
    "TERRITORY_1": [0.10, 0.15],
    "TERRITORY_2": [0.90, 0.15],
    "TERRITORY_3": [0.50, 0.90],
}

COLORS = {k: tuple(v) for k, v in config["COLORS"].items()}
BLACK = COLORS["BLACK"]
WHITE = COLORS["WHITE"]
TEAL = COLORS["TEAL"]
ORANGE = COLORS["ORANGE"]
BLUE = COLORS["BLUE"]
PURPLE = COLORS["PURPLE"]
RED_ORANGE = COLORS["RED_ORANGE"]
GREEN = COLORS["GREEN"]
LIME = COLORS["LIME"]
GREEN_TEXT = COLORS["GREEN_TEXT"]
RED_TEXT = COLORS["RED_TEXT"]
YELLOW_TEXT = COLORS["YELLOW_TEXT"]
TRADE_COLOR = COLORS["TRADE_COLOR"]
DEV_COLOR = COLORS["DEV_COLOR"]
FLEX_COLOR = COLORS["FLEX_COLOR"]

PERSONALITY_COLORS = {
    "Anchor": (80, 180, 255),
    "Climber": (255, 180, 80),
    "Connector": (180, 80, 255),
    "Survivor": (80, 255, 120),
}

_EMOTION_SCHEMA = load_emotion_schema(str(BASE_DIR))
EMX_EMOTIONS = list(_EMOTION_SCHEMA.emotions)
EMX_COLORS = dict(_EMOTION_SCHEMA.color_map)
EMX_PAD = dict(_EMOTION_SCHEMA.pads)
EMX_LEGACY_ALIASES = dict(_EMOTION_SCHEMA.aliases)
EMX_OVERRIDES = dict(_EMOTION_SCHEMA.raw_overrides)


def canonical_emotion(name: str) -> str:
    return EMX_LEGACY_ALIASES.get(name, name)


def emx_get(state: Dict[str, float], name: str, default: float = 0.5) -> float:
    canonical = canonical_emotion(name)
    return float(state.get(canonical, state.get(name, default)))


def sync_emotion_aliases(state: Dict[str, float]) -> Dict[str, float]:
    for legacy_name, canonical_name in EMX_LEGACY_ALIASES.items():
        state[legacy_name] = float(state.get(canonical_name, state.get(legacy_name, 0.5)))
    return state


def _profile_for_archetype(archetype: str) -> Dict[str, float]:
    profile = {emotion: 0.28 for emotion in EMX_EMOTIONS}
    boosts = {
        "Anchor": {"Calm": 0.82, "Love": 0.70, "Joy": 0.60, "Pride": 0.42},
        "Climber": {"Pride": 0.88, "Anger": 0.52, "Joy": 0.48, "Calm": 0.32},
        "Connector": {"Love": 0.82, "Joy": 0.74, "Panic": 0.42, "Calm": 0.46},
        "Survivor": {"Fear": 0.78, "Exhaustion": 0.68, "Sadness": 0.62, "Anger": 0.44},
    }.get(archetype, {})
    for emotion in EMX_EMOTIONS:
        if emotion in boosts:
            profile[emotion] = boosts[emotion]
    return profile


EMX_PERSONALITY_BASE = {
    name: _profile_for_archetype(name)
    for name in ("Anchor", "Climber", "Connector", "Survivor")
}

PERSONALITY_EMX_SENSITIVITY = {
    "Anchor": {emotion: (1.45 if emotion in {"Calm", "Love"} else 0.85) for emotion in EMX_EMOTIONS},
    "Climber": {emotion: (1.55 if emotion in {"Pride", "Anger"} else 0.90) for emotion in EMX_EMOTIONS},
    "Connector": {emotion: (1.45 if emotion in {"Joy", "Love", "Panic"} else 0.88) for emotion in EMX_EMOTIONS},
    "Survivor": {emotion: (1.55 if emotion in {"Fear", "Sadness", "Exhaustion"} else 0.82) for emotion in EMX_EMOTIONS},
}

EMX_DEPENDENCY_CONFIG = config.get("emx_dependency") or {}
EMX_DEPENDENCY_ENABLED = bool(EMX_DEPENDENCY_CONFIG.get("enabled", True))
EMX_DEPENDENCY_COUPLING = float(EMX_DEPENDENCY_CONFIG.get("coupling", 0.55))
EMX_DEPENDENCY_CONTEXT_GAIN = float(EMX_DEPENDENCY_CONFIG.get("context_gain", 0.95))
EMX_DEPENDENCY_FIELD_GAIN = float(EMX_DEPENDENCY_CONFIG.get("field_gain", 0.65))
EMX_DEPENDENCY_VOLATILITY_GAIN = float(EMX_DEPENDENCY_CONFIG.get("volatility_gain", 1.2))
EMX_DEPENDENCY_HOMEOSTASIS = float(EMX_DEPENDENCY_CONFIG.get("homeostasis_rate", 0.00035))
EMX_DEPENDENCY_DRIFT_BASE = float(EMX_DEPENDENCY_CONFIG.get("drift_base", 0.0022))
EMX_DEPENDENCY_DRIFT_MAX = float(EMX_DEPENDENCY_CONFIG.get("drift_max", 0.014))

# Degenerate emotion state thresholds (overdrive collapse)
OVERDRIVE_THRESHOLD  = 0.82   # emotion must exceed this
OVERDRIVE_DURATION   = 80     # consecutive ticks before collapse fires
EMX_OVERUSE_BACKLASH_START = 40
EMX_OVERUSE_BACKLASH_DRAIN = 0.020
EMX_OVERUSE_BACKLASH_GAIN = 0.026
DEGEN_COLLAPSE_ADD   = 0.40   # how much the degenerate state increases on collapse
DEGEN_EMOTION_RESET  = 0.30   # emotion resets to this after collapse
DEGEN_DECAY_RATE     = 0.0008 # per-tick decay for all degenerate states
# Emotion → degenerate state mapping
OVERDRIVE_COLLAPSE_MAP: Dict[str, str] = {
    "Joy": "dissociation",
    "Fear": "apathy",
    "Panic": "apathy",
    "Anger": "exhaustion",
    "Sadness": "numbness",
    "Calm": "cynicism",
    "Love": "cynicism",
}
EMX_OVERUSE_BACKLASH_MAP: Dict[str, str] = {
    "Joy": "Anger",
    "Love": "Shame",
    "Calm": "Panic",
    "Pride": "Exhaustion",
}

_DEFAULT_EMX_DEPENDENCY_WEIGHTS = {
    "Joy": {"Sadness": -0.55, "Fear": -0.20, "Love": 0.35, "Pride": 0.25, "Anger": -0.20},
    "Love": {"Joy": 0.30, "Calm": 0.25, "Shame": -0.40, "Panic": -0.25},
    "Calm": {"Panic": -0.65, "Fear": -0.30, "Love": 0.20, "Exhaustion": -0.10},
    "Pride": {"Joy": 0.25, "Anger": 0.25, "Exhaustion": -0.35, "Shame": -0.30},
    "Anger": {"Fear": 0.30, "Panic": 0.25, "Calm": -0.30, "Joy": -0.20},
    "Fear": {"Panic": 0.30, "Calm": -0.25, "Love": -0.15, "Anger": 0.20},
    "Panic": {"Fear": 0.40, "Calm": -0.50, "Exhaustion": 0.20, "Joy": -0.20},
    "Sadness": {"Joy": -0.50, "Love": -0.20, "Shame": 0.25, "Exhaustion": 0.25},
    "Shame": {"Love": -0.55, "Pride": -0.35, "Sadness": 0.30, "Fear": 0.15},
    "Exhaustion": {"Panic": 0.15, "Pride": -0.40, "Joy": -0.25, "Calm": -0.10},
}

EMX_DEPENDENCY_WEIGHTS = {}
for emotion in EMX_EMOTIONS:
    default_weights = _DEFAULT_EMX_DEPENDENCY_WEIGHTS.get(emotion, {})
    cfg_weights = EMX_DEPENDENCY_CONFIG.get("weights", {}).get(emotion, {})
    merged = dict(default_weights)
    for source_emotion, weight in cfg_weights.items():
        if source_emotion in EMX_EMOTIONS:
            merged[source_emotion] = float(weight)
    EMX_DEPENDENCY_WEIGHTS[emotion] = merged


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# Primary dyads — adjacent pairs on Plutchik's wheel (Trust renamed to Acceptance)
NPC_PRIMARY_DYADS: Dict[frozenset, str] = {
    frozenset({"Joy", "Love"}): "Radiance",
    frozenset({"Love", "Calm"}): "Belonging",
    frozenset({"Calm", "Pride"}): "Confidence",
    frozenset({"Pride", "Anger"}): "Defiance",
    frozenset({"Anger", "Fear"}): "Alarm",
    frozenset({"Fear", "Panic"}): "Terror",
    frozenset({"Panic", "Sadness"}): "Griefstorm",
    frozenset({"Sadness", "Shame"}): "Remorse",
    frozenset({"Shame", "Exhaustion"}): "Collapse",
    frozenset({"Exhaustion", "Joy"}): "Relief",
}

# Dyad display colors — warm/cool tones matching emotional valence
NPC_DYAD_COLORS: Dict[str, tuple] = {
    "Radiance": (255, 185, 110),
    "Belonging": (255, 140, 205),
    "Confidence": (140, 205, 200),
    "Defiance": (255, 145, 70),
    "Alarm": (200, 90, 120),
    "Terror": (230, 90, 210),
    "Griefstorm": (100, 130, 210),
    "Remorse": (135, 105, 145),
    "Collapse": (110, 120, 140),
    "Relief": (180, 220, 150),
}

SHIFT_DURATION = 400
TRAVEL_RATIO = 0.25
TRAVEL_BUDGET = int(SHIFT_DURATION * TRAVEL_RATIO)
WORK_BUDGET = SHIFT_DURATION - TRAVEL_BUDGET
if "diss_thresholds" in config:
    DISS_THRESHOLD = config["diss_thresholds"].get("default", 120.0)
    DISS_THRESHOLD_BY_TERRITORY = config["diss_thresholds"]
else:
    DISS_THRESHOLD = 120.0
    DISS_THRESHOLD_BY_TERRITORY = {"default": 120.0}
WITHDRAWAL_GAP = 7

color_map = {
    "SCIENCE": (100, 200, 255),
    "TRADE": TRADE_COLOR,
    "DEVELOPMENT": DEV_COLOR,
    "FLEX": FLEX_COLOR,
    "CENTRAL": COLORS.get("LIGHT_BLUE", (173, 216, 230)),
    "PANTHEON": COLORS.get("GOLD", (255, 215, 0)),
}
INSPECTOR_ZONE_ORDER = ["SCIENCE", "TRADE", "DEVELOPMENT", "FLEX", "CENTRAL", "PANTHEON"]

# Vitality & Budget Maintenance (Task 2)
# These represent the 'base' costs of being active in the world,
# separate from zone-specific work outputs.
PASSIVE_ENERGY_DRAIN_PER_TICK = 0.0225  # 0.045 / 2 (sim logic runs 2x per frame)
PASSIVE_STRESS_GAIN_PER_TICK = 0.0075   # 0.015 / 2
WORK_BUDGET_DECAY_PER_TICK = 5.0        # 10 / 2

# 1️⃣ Add new constants near your other config values
ZONE_LOCK_THRESHOLD = 500
ZONE_UNLOCK_VISITS = 2

ZONE_TRAVEL_COSTS = {}
for zone_name, zone_cfg in (config.get("zone_travel_costs") or {}).items():
    ZONE_TRAVEL_COSTS[zone_name] = {
        "energy_cost_per_tick": float(zone_cfg.get("ENERGY_cost", 0.0)),
        "money_cost_per_tick": float(zone_cfg.get("MATERIAL_cost", 0.0)),
        "description": zone_cfg.get("description", "")
    }

EVENT_TEMPLATES = [
    {"name": "Market Boom", "zone": "TRADE", "effect": "bonus", "money_mult": 1.5, "duration": 200, "desc": "Trade profits increased!"},
    {"name": "Research Grant", "zone": "SCIENCE", "effect": "bonus", "money_mult": 1.3, "duration": 150, "desc": "Science funding boost!"},
    {"name": "Equipment Failure", "zone": "DEVELOPMENT", "effect": "penalty", "stress_mult": 1.4, "duration": 100, "desc": "Dev zone disrupted!"},
    {"name": "Wellness Program", "zone": "FLEX", "effect": "bonus", "stress_mult": 0.7, "duration": 180, "desc": "Flex zone enhanced!"},
    {"name": "Energy Crisis", "zone": None, "effect": "penalty", "energy_mult": 1.3, "duration": 120, "desc": "Energy costs increased!"},
]

EMX_OPPOSITE_PAIRS = [
    ("Joy", "Sadness"),
    ("Love", "Shame"),
    ("Calm", "Panic"),
    ("Pride", "Exhaustion"),
    ("Fear", "Anger"),
]

# Emotional complexes - when emotions coexist, they create new qualities
EMOTIONAL_COMPLEXES = {
    tuple(sorted(("Joy", "Sadness"))): {"name": "Nostalgia", "color": (200, 180, 220), "behavior": "reflective"},
    tuple(sorted(("Fear", "Pride"))): {"name": "Anxiety", "color": (180, 130, 80), "behavior": "erratic"},
    tuple(sorted(("Anger", "Joy"))): {"name": "Schadenfreude", "color": (220, 100, 50), "behavior": "competitive"},
    tuple(sorted(("Calm", "Shame"))): {"name": "Resignation", "color": (100, 100, 120), "behavior": "passive"},
    tuple(sorted(("Panic", "Pride"))): {"name": "Wonder", "color": (180, 220, 255), "behavior": "curious"},
    tuple(sorted(("Fear", "Sadness"))): {"name": "Despair", "color": (70, 70, 140), "behavior": "withdrawn"},
    tuple(sorted(("Anger", "Shame"))): {"name": "Contempt", "color": (150, 80, 40), "behavior": "rejecting"},
    tuple(sorted(("Joy", "Pride"))): {"name": "Optimism", "color": (200, 255, 150), "behavior": "proactive"},
}

_TERRITORY_COLORS = [
    (100, 200, 255),   # ZONE 1 - Blue
    (255, 180, 100),   # ZONE 2 - Orange
    (150, 100, 220),   # ZONE 3 - Purple
]
_CENTRAL_COL  = (173, 216, 230)
_PANTHEON_COL = (255, 215, 0)
_SOLITARY_ZONES = {"HOME", "SCIENCE", "DEVELOPMENT"}
