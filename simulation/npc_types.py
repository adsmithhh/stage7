from __future__ import annotations

import math
import random
import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, deque

from config.constants import (
    ALL_ZONES,
    DISS_THRESHOLD,
    DOCTRINE_TYPES,
    EMX_CELL_GRID_ENABLED,
    EMX_EMOTIONS,
    EMX_WEATHER_HISTORY_LENGTH,
    EVENT_TEMPLATES,
    TRAVEL_BUDGET,
    WORK_BUDGET,
    WORK_ZONES,
)
from rendering.render_types import RenderState

NPC_ID_GEN = itertools.count(1)


@dataclass
class UIState:
    show_statistics: bool = False
    show_npc_inspector: bool = False
    show_combined_view: bool = False
    show_game_snapshot_panel: bool = False
    show_controls_panel: bool = False
    focused_zone: Optional[str] = None  # None means standard view, otherwise zone name
    paused: bool = False
    status_message: Optional[str] = None
    status_until_ms: int = 0
    show_tuning_panel: bool = False
    show_emx_panel: bool = False
    show_weather_panel: bool = False
    show_cell_grid: bool = EMX_CELL_GRID_ENABLED
    show_load_menu: bool = False
    active_save_slot: int = 1
    inspector_zone_idx: Optional[int] = 0
    inspector_page: int = 0
    current_world_idx: int = 0
    statistics_page: int = 0
    emx_page: int = 0
    tuning_category_idx: int = 0
    tuning_item_idx: int = 0
    startup_messages: List[str] = field(default_factory=list)
    startup_until_ms: int = 0
    render_scroll_x: int = 0
    render_scroll_y: int = 0
    horizontal_scroll_dragging: bool = False
    horizontal_scroll_drag_offset_x: int = 0

@dataclass
class Commitment:
    key: str
    created_tick: int
    due_tick: int
    strength: float = 0.5
    honored: bool = False
    broken: bool = False
    break_reason: str = ""

@dataclass
class ZoneStats:
    name: str
    total_work_done: int = 0
    total_npcs_served: int = 0
    current_population: int = 0
    congestion_level: float = 0.0
    market_demand: float = 1.0
    efficiency_rating: float = 1.0
    total_money_generated: float = 0.0
    total_stress_absorbed: float = 0.0
    history_population: deque = field(default_factory=lambda: deque(maxlen=100))
    history_efficiency: deque = field(default_factory=lambda: deque(maxlen=100))

    def update_metrics(self, population: int):
        self.current_population = population
        self.history_population.append(population)
        optimal_population = 5
        self.congestion_level = min(1.0, population / (optimal_population * 2))
        self.efficiency_rating = 1.0 / (1.0 + self.congestion_level * 0.5)
        self.history_efficiency.append(self.efficiency_rating)
        self.market_demand = 0.8 + random.random() * 0.4

@dataclass(frozen=True)
class ZoneStatsView:
    name: str
    current_population: int
    congestion_level: float
    market_demand: float
    efficiency_rating: float
    total_work_done: int
    total_money_generated: float
    total_stress_absorbed: float

@dataclass
class WorkBasin:
    """Dynamic work basin for each zone - enables multi-zone work distribution basin"""
    zone_name: str
    occupancy: float = 0.0              # Current occupancy (0.0 to 1.0)
    attraction_score: float = 0.5       # Zone attractiveness for decisions
    work_diversity: float = 0.0          # Entropy of work type distribution
    travel_emergence_rate: float = 0.0  # How often NPCs travel to this zone
    active_worker_count: int = 0        # Current NPCs working at this zone
    history_occupancy: deque = field(default_factory=lambda: deque(maxlen=50))
    history_attraction: deque = field(default_factory=lambda: deque(maxlen=50))
    history_diversity: deque = field(default_factory=lambda: deque(maxlen=50))

    def update_basin_state(self, current_population: int, total_population: int, efficiency_rating: float):
        """Update basin state based on current zone metrics"""
        self.occupancy = min(1.0, current_population / max(1, total_population))
        # Attraction varies inversely with occupancy.
        self.attraction_score = max(0.3, 1.0 - self.occupancy) * efficiency_rating
        self.history_occupancy.append(self.occupancy)
        self.history_attraction.append(self.attraction_score)


@dataclass
class GameEvent:
    name: str
    description: str
    zone_affected: Optional[str]
    duration: int
    effect_type: str
    money_multiplier: float = 1.0
    stress_multiplier: float = 1.0
    energy_multiplier: float = 1.0
    remaining_ticks: int = 0

@dataclass
class World:
    npcs: List["NPC"] = field(default_factory=list)
    zone_stats: Dict[str, ZoneStats] = field(default_factory=dict)
    zone_modifiers: Dict[str, Dict[str, float]] = field(default_factory=dict)
    dissonance_threshold: float = DISS_THRESHOLD
    active_events: List[GameEvent] = field(default_factory=list)
    runtime_state: "WorldRuntimeState" = field(default_factory=lambda: WorldRuntimeState())
    name: str = ""

    def spawn_random_event(self):
        if random.random() < 0.01:
            template = random.choice(EVENT_TEMPLATES)
            event = GameEvent(
                name=template["name"],
                description=template["desc"],
                zone_affected=template.get("zone"),
                duration=template["duration"],
                effect_type=template["effect"],
                money_multiplier=template.get("money_mult", 1.0),
                stress_multiplier=template.get("stress_mult", 1.0),
                energy_multiplier=template.get("energy_mult", 1.0),
                remaining_ticks=template["duration"]
            )
            self.active_events.append(event)

    def update_events(self):
        self.active_events = [e for e in self.active_events if e.remaining_ticks > 0]
        for event in self.active_events:
            event.remaining_ticks -= 1

    def step(self, global_tick, anchors: dict):
        from .simulation import simtick
        self.update_events()
        self.spawn_random_event()
        simtick(self, global_tick, self.runtime_state, anchors)

@dataclass(frozen=True)
class WorldSnapshot:
    tick: int
    npc: Dict[int, "NPCView"]
    zone_density: Dict[str, int]
    zone_stats: Dict[str, "ZoneStatsView"]
    active_events: Tuple["GameEvent", ...]

def anchors_to_pixels(anchors_norm, width, height):
    return {name: (int(x * width), int(y * height)) for name, (x, y) in anchors_norm.items()}

def build_zone_density(npcs) -> Dict[str, int]:
    dens = {z: 0 for z in ALL_ZONES}
    for npc in npcs:
        # Count all NPCs at their current zone, including HOME
        if npc.zone in dens:
            dens[npc.zone] += 1
    return dens

def update_zone_stats_mutable(zone_stats_mutable: Dict[str, "ZoneStats"], zone_density: Dict[str, int]):
    for zone_name, pop in zone_density.items():
        if zone_name not in zone_stats_mutable:
            zone_stats_mutable[zone_name] = ZoneStats(name=zone_name)
        zone_stats_mutable[zone_name].update_metrics(pop)

def freeze_zone_stats_views(zone_stats_mutable: Dict[str, "ZoneStats"]) -> Dict[str, ZoneStatsView]:
    out: Dict[str, ZoneStatsView] = {}
    for zn, zs in zone_stats_mutable.items():
        out[zn] = ZoneStatsView(
            name=zs.name,
            current_population=zs.current_population,
            congestion_level=zs.congestion_level,
            market_demand=zs.market_demand,
            efficiency_rating=zs.efficiency_rating,
            total_work_done=zs.total_work_done,
            total_money_generated=zs.total_money_generated,
            total_stress_absorbed=zs.total_stress_absorbed,

        )
    return out

@dataclass(frozen=False)
class NPCView:
    def __init__(
        self,
        id,
        factor_skills,
        x,
        y,
        state,
        zone,
        target,
        next_intended_zone,
        travel_budget,
        work_budget,
        shift_offset,
        speed,
        stress_endured,
        money,
        energy,
        is_collapsing,
        skills,
        zone_visit_count,
        zones_visited_this_cycle,
        zone_fatigue,
        personality,
        derived,
        personality_name,
        emx,
        **kwargs
    ):
        self.id = id
        self.factor_skills = factor_skills
        self.x = x
        self.y = y
        self.state = state
        self.zone = zone
        self.target = target
        self.next_intended_zone = next_intended_zone
        self.travel_budget = travel_budget
        self.work_budget = work_budget
        self.shift_offset = shift_offset
        self.speed = speed
        self.stress_endured = stress_endured
        self.money = money
        self.energy = energy
        self.is_collapsing = is_collapsing
        self.skills = skills
        self.zone_visit_count = zone_visit_count
        self.zones_visited_this_cycle = zones_visited_this_cycle
        self.zone_fatigue = zone_fatigue
        self.personality = personality
        self.derived = derived
        self.personality_name = personality_name
        self.emx = emx
        self.social_need = kwargs.get('social_need', 0.5)
        self.legacy_points = kwargs.get('legacy_points', 0.0)
        self.current_zone_ticks = kwargs.get('current_zone_ticks', 0)
        self.locked_zone = kwargs.get('locked_zone')
        self.last_work_zone = kwargs.get('last_work_zone')
        self.repeated_work_loops = kwargs.get('repeated_work_loops', 0)
        self.apathy       = kwargs.get('apathy',       0.0)
        self.exhaustion   = kwargs.get('exhaustion',   0.0)
        self.dissociation = kwargs.get('dissociation', 0.0)
        self.numbness     = kwargs.get('numbness',     0.0)
        self.cynicism     = kwargs.get('cynicism',     0.0)
        self.reserve      = kwargs.get('reserve',      50.0)

    def get_self_esteem(self) -> float:
        return self.derived.get("self_esteem_base", 0.0)

    def get_stress_tolerance(self) -> float:
        return self.derived.get("stress_tolerance", 0.5)

    def get_risk_tolerance(self) -> float:
        return self.derived.get("risk_tolerance", 0.5)

    def get_recovery_rate(self) -> float:
        return self.derived.get("recovery_rate", 0.5)

    def get_social_sensitivity(self) -> float:
        return self.derived.get("social_sensitivity", 0.5)

    def get_goal_stickiness(self) -> float:
        return self.derived.get("goal_stickiness", 0.5)

    def get_efficiency_invariance(self) -> float:
        return self.derived.get("efficiency_invariance", 0.5)

    def get_anchoring_authority(self) -> float:
        return self.derived.get("anchoring_authority", 0.5)

    def get_social_drive(self) -> float:
        return self.derived.get("social_drive", self.personality.get("sociability", 0.5))

    @classmethod
    def from_npc(cls, npc: "NPC"):
        return cls(
            id=npc.id,
            factor_skills=dict(npc.factor_skills),
            x=npc.x,
            y=npc.y,
            state=npc.state,
            zone=npc.zone,
            target=npc.target,
            next_intended_zone=npc.next_intended_zone,
            travel_budget=npc.travel_budget,
            work_budget=npc.work_budget,
            shift_offset=npc.shift_offset,
            speed=npc.speed,
            stress_endured=npc.stress_endured,
            money=npc.money,
            energy=npc.energy,
            is_collapsing=npc.is_collapsing,
            skills=dict(npc.skills),
            zone_visit_count=dict(npc.zone_visit_count),
            zones_visited_this_cycle=list(npc.zones_visited_this_cycle),
            zone_fatigue=dict(npc.zone_fatigue),
            personality=dict(npc.personality),
            derived=dict(npc.derived),
            personality_name=npc.personality_name,
            emx=dict(npc.emx),
            social_need=npc.social_need,
            legacy_points=npc.legacy_points,
            current_zone_ticks=npc.current_zone_ticks,
            locked_zone=npc.locked_zone,
            last_work_zone=npc.last_work_zone,
            repeated_work_loops=npc.repeated_work_loops,
            apathy=npc.apathy,
            exhaustion=npc.exhaustion,
            dissociation=npc.dissociation,
            numbness=npc.numbness,
            cynicism=npc.cynicism,
            reserve=npc.reserve,
        )

def refresh_world_zone_stats(world) -> tuple[Dict[str, int], Dict[str, ZoneStatsView]]:
    zone_density = build_zone_density(world.npcs)
    update_zone_stats_mutable(world.zone_stats, zone_density)
    return zone_density, freeze_zone_stats_views(world.zone_stats)


def build_snapshot(world, tick: int, zone_density: Dict[str, int], zone_stats_view: Dict[str, ZoneStatsView]) -> WorldSnapshot:
    npc_view = {npc.id: NPCView.from_npc(npc) for npc in world.npcs}
    return WorldSnapshot(
        tick=tick,
        npc=npc_view,
        zone_density=zone_density,
        zone_stats=zone_stats_view,
        active_events=tuple(world.active_events),
    )

@dataclass
class NPC:
    x: float
    y: float
    zone: str = ""
    state: str = "AT_HOME"
    target: Optional[str] = None
    next_intended_zone: Optional[str] = None
    speed: float = 4.0
    travel_budget: int = TRAVEL_BUDGET
    work_budget: int = WORK_BUDGET
    shift_offset: int = 0
    id: int = field(default_factory=lambda: next(NPC_ID_GEN))
    locked_zone: Optional[str] = None
    unlock_progress: set = field(default_factory=set)
    stress_endured: float = 0.0
    prepared_intent: Optional[str] = None

    # --- Core variables ---
    money: float = 190.0
    energy: float = 100.0
    reserve: float = 50.0
    opportunity_access: float = 1.0

    # --- Existing fields ---
    skills: Dict[str, float] = field(default_factory=lambda: {"SCIENCE": 0.0, "TRADE": 0.0, "DEVELOPMENT": 0.0, "FLEX": 0.0})
    commitments: Dict[str, Commitment] = field(default_factory=dict)
    derived: Dict[str, float] = field(default_factory=dict)
    zone_visit_count: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    zones_visited_this_cycle: list = field(default_factory=list)
    last_zone_choice: Optional[str] = None
    total_jobs_completed: int = 0
    factor_skills: Dict[str, float] = field(default_factory=lambda: {
        "MATERIAL": 0.0,
        "ENERGY": 0.0,
        "SOCIAL": 0.0,
        "KNOWLEDGE": 0.0,
        "STABILITY": 0.0
    })
    cycle_phase: str = "RESTING"
    is_collapsing: bool = False
    vx_target: Optional[float] = None
    vy_target: Optional[float] = None
    personality: Dict[str, float] = field(default_factory=dict)
    personality_name: str = "Unknown"
    current_zone_ticks: int = 0
    fatigue_penalty_multiplier: float = 3.0
    zone_fatigue: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_zone: Optional[str] = None
    last_work_zone: Optional[str] = None
    repeated_work_loops: int = 0
    last_zone_change_tick: int = 0
    fatigue_history: deque = field(default_factory=lambda: deque(maxlen=20))
    fatigue_multiplier: float = 2.0
    emx: Dict[str, float] = field(default_factory=lambda: {e: 0.5 for e in EMX_EMOTIONS})
    emx_dyad: Optional[str] = None

    # --- Shared zone tracking ---
    social_need: float = 0.5          # 0-1; builds in solitary zones, drains at CENTRAL
    legacy_points: float = 0.0        # accumulated prestige from PANTHEON visits
    shared_zone_entry_tick: int = 0   # tick when NPC entered current shared zone
    planned_departure_tick: int = 0   # tick when NPC intends to leave shared zone

    # --- Doctrine (CENTRAL indoctrination layer) ---
    doctrine: Optional[str]  = None   # active doctrine type, or None
    doctrine_strength: float = 0.0    # 0-1; how deeply held
    deradicalization_timer: int = 0   # countdown ticks until doctrine cleared

    # --- Degenerate emotion states (overdrive collapse) ---
    apathy: float = 0.0        # collapsed fear/anger — suppresses ALL emotions × 0.7
    exhaustion: float = 0.0    # collapsed anger — blocks TRADE/DEVELOPMENT, pulls HOME
    dissociation: float = 0.0  # collapsed joy — blocks CENTRAL/FLEX social pull
    numbness: float = 0.0      # collapsed sadness — mutes stress signals, flattens HOME pull
    cynicism: float = 0.0      # collapsed trust/acceptance — blocks CENTRAL/PANTHEON
    emx_overdrive_ticks: Dict[str, int] = field(default_factory=dict)  # ticks above threshold

    def get_self_esteem(self) -> float:
        return self.derived.get("self_esteem_base", 0.0)

    def get_trust(self) -> float:
        """Trust is currently derived from (1.0 - cynicism)."""
        return 1.0 - self.cynicism

    def get_stress_tolerance(self) -> float:
        return self.derived.get("stress_tolerance", 0.5)

    def get_risk_tolerance(self) -> float:
        return self.derived.get("risk_tolerance", 0.5)

    def get_recovery_rate(self) -> float:
        return self.derived.get("recovery_rate", 0.5)

    def get_social_sensitivity(self) -> float:
        return self.derived.get("social_sensitivity", 0.5)

    def get_goal_stickiness(self) -> float:
        return self.derived.get("goal_stickiness", 0.5)

    def get_efficiency_invariance(self) -> float:
        return self.derived.get("efficiency_invariance", 0.5)

    def get_anchoring_authority(self) -> float:
        return self.derived.get("anchoring_authority", 0.5)

    def get_social_drive(self) -> float:
        return self.derived.get("social_drive", self.personality.get("sociability", 0.5))

    def set_target_anchor(self, anchor_pos):
        self.vx_target, self.vy_target = anchor_pos

    def visual_step(self, speed=5.0):
        if self.vx_target is None or self.vy_target is None:
            return False
        dx = self.vx_target - self.x
        dy = self.vy_target - self.y
        dist = math.hypot(dx, dy)
        if dist <= speed:
            self.x, self.y = self.vx_target, self.vy_target
            self.vx_target = None
            self.vy_target = None
            return True
        self.x += dx / dist * speed
        self.y += dy / dist * speed
        return False

def compute_derived_traits(personality: Dict[str, float]) -> Dict[str, float]:
    r = personality.get("reactivity", 0.5)
    res = personality.get("resilience", 0.5)
    amb = personality.get("ambition", 0.5)
    soc = personality.get("sociability", 0.5)
    stb = personality.get("stability_bias", 0.5)
    return {
        "self_esteem_base": 0.4 - 0.4 * amb + 0.2 * res,
        "goal_stickiness": 0.5 * stb + 0.3 * res + 0.2 * amb,
        "stress_tolerance": 0.6 * res + 0.4 * (1 - r),
        "recovery_rate": 0.7 * res + 0.3 * stb,
        "social_sensitivity": 0.7 * soc + 0.3 * r,
        "risk_tolerance": 0.6 * (1 - stb) + 0.4 * soc,
        "efficiency_invariance": res,
        "anchoring_authority": stb * res,
    }

@dataclass(frozen=True)
class Intent:
    npc_id: int
    desired_zone: str
    phase_label: str
    confidence: float = 0.5

IntentBuffer = Dict[int, Intent]

@dataclass(frozen=True)
class CommitmentPlan:
    npc_id: int
    key: str
    zone: str
    due_tick: int
    strength: float
    forced_reason: str = ""

@dataclass(frozen=True)
class BreakPlan:
    npc_id: int
    key: str
    reason: str

CommitmentBuffer = Dict[int, CommitmentPlan]
BreakBuffer = List[BreakPlan]

@dataclass(frozen=True)
class MovementResult:
    npc_id: int
    new_pos: Tuple[float, float]
    arrived: bool
    energy_cost: float = 0.0
    money_cost: float = 0.0

MoveBuffer = Dict[int, MovementResult]

@dataclass(frozen=True)
class WorkResult:
    npc_id: int
    zone: str
    money_delta: float = 0.0
    stress_delta: float = 0.0
    energy_delta: float = 0.0
    skills_delta: Dict[str, float] = field(default_factory=dict)
    factor_deltas: Dict[str, float] = field(default_factory=dict)

WorkBuffer = Dict[int, WorkResult]

@dataclass(frozen=True)
class InfluenceDelta:
    npc_id: int
    doctrine_bias_delta: float = 0.0
    stress_delta: float = 0.0
    pull_flag: Optional[str] = None

InfluenceBuffer = Dict[int, InfluenceDelta]

@dataclass(frozen=True)
class CollapseFlag:
    npc_id: int
    reason: str
    severity: float = 1.0

CollapseBuffer = Dict[int, CollapseFlag]

@dataclass
class TickBuffers:
    snapshot: WorldSnapshot
    intents: IntentBuffer = field(default_factory=dict)
    commitments: CommitmentBuffer = field(default_factory=dict)
    breaks: BreakBuffer = field(default_factory=list)
    moves: MoveBuffer = field(default_factory=dict)
    work: WorkBuffer = field(default_factory=dict)
    influence: InfluenceBuffer = field(default_factory=dict)
    collapse: CollapseBuffer = field(default_factory=dict)


@dataclass
class CentralDoctrineState:
    pressure: Dict[str, float]        = field(default_factory=lambda: {d: 0.0 for d in DOCTRINE_TYPES})
    active:   Optional[str]           = None
    active_since_tick: int            = 0
    history:  List[Tuple]             = field(default_factory=list)
    indoctrinated: Dict[str, int]     = field(default_factory=lambda: {d: 0 for d in DOCTRINE_TYPES})


@dataclass
class SimulationState:
    """Explicit container for simulation-wide logic and mechanics state."""
    doctrine: CentralDoctrineState = field(default_factory=CentralDoctrineState)
    zone_mechanics: Dict[str, object] = field(default_factory=dict)
    # Basin attraction logic
    zone_basins: Dict[str, WorkBasin] = field(
        default_factory=lambda: {
            z: WorkBasin(zone_name=z) for z in WORK_ZONES
        }
    )


@dataclass
class EnvironmentState:
    """Explicit container for atmospheric and emotional field data."""
    zone_atmosphere: Dict[str, Dict[str, float]] = field(
        default_factory=lambda: {
            z: {e: 0.0 for e in EMX_EMOTIONS}
            for z in WORK_ZONES
        }
    )
    zone_complexes: Dict[str, Optional[Dict[str, object]]] = field(
        default_factory=lambda: {
            z: None for z in WORK_ZONES
        }
    )
    zone_weather_history: Dict[str, deque] = field(
        default_factory=lambda: {
            z: deque(maxlen=EMX_WEATHER_HISTORY_LENGTH) for z in WORK_ZONES
        }
    )
    cell_atmosphere: List[Dict[str, float]] = field(default_factory=list)
    cell_population: List[int] = field(default_factory=list)

    # Pre-allocated buffers for optimization:
    _cell_sums: List[List[float]] = field(default_factory=list)
    _cell_new: List[List[float]] = field(default_factory=list)
    _cell_old: List[List[float]] = field(default_factory=list)
    _cell_populations: List[int] = field(default_factory=list)
    _anchor_field_sums: List[List[float]] = field(default_factory=list)
    _anchor_field_cache: List[list] = field(default_factory=list)
    _anchor_cache_valid: bool = False
    _central_cell_idxs: set = field(default_factory=set)
    _cached_anchor_fields: dict = field(default_factory=dict)


@dataclass
class WorldRuntimeState:
    """World-scoped bundle for mutable simulation, environment, and render state."""
    sim_state: SimulationState = field(default_factory=SimulationState)
    env_state: EnvironmentState = field(default_factory=EnvironmentState)
    render_state: RenderState = field(default_factory=RenderState)
