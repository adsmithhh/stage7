from __future__ import annotations

import math
import random

from config.constants import (
    ALL_ZONES,
    ANCHORS_NORM,
    DECISION_DISGUST_PENALTY_MULTIPLIER,
    DECISION_ENERGY_LOSS_MULTIPLIER,
    DECISION_HOME_RECOVERY_BONUS,
    DOCTRINE_SHORT,
    EMX_EMOTIONS,
    FACTOR_NAMES,
    PANTHEON,
    PANTHEON_CONTRIB_ENERGY,
    PANTHEON_CONTRIB_MATERIAL,
    PASSIVE_ENERGY_DRAIN_PER_TICK,
    PASSIVE_STRESS_GAIN_PER_TICK,
    SHARED_ZONE_CONFIG,
    SHARED_ZONES,
    SHIFT_DURATION,
    TRAVEL_BUDGET,
    HEIGHT,
    WIDTH,
    WITHDRAWAL_GAP,
    WORK_BUDGET,
    WORK_BUDGET_DECAY_PER_TICK,
    WORK_ZONES,
    ZONE_FACTOR_PROFILES,
    ZONE_LOCK_THRESHOLD,
    ZONE_UNLOCK_VISITS,
    config,
    emx_get,
    sync_emotion_aliases,
)
from .npc_types import (
    CollapseFlag,
    Commitment,
    CommitmentPlan,
    InfluenceDelta,
    Intent,
    MovementResult,
    NPC,
    NPCView,
    TickBuffers,
    WorkResult,
    ZoneStats,
    WorldRuntimeState,
    SimulationState,
    EnvironmentState,
    build_snapshot,
    refresh_world_zone_stats,
    compute_derived_traits,
)
from emx.api import (
    update_emx, update_atmosphere, update_cell_atmosphere,
    update_social_need, compute_emx_sensitivity_from_traits,
    compute_emx_base_from_traits, _trait_label
)
from .doctrine import update_central_doctrine


def _visit_tick_range(shared_zone_cfg: dict) -> tuple[int, int]:
    min_visit = max(1, int(round(float(shared_zone_cfg["min_visit_ticks"]))))
    max_visit = max(1, int(round(float(shared_zone_cfg["max_visit_ticks"]))))
    if max_visit < min_visit:
        max_visit = min_visit
    return min_visit, max_visit


def allowed_work_zones(npc_view: NPCView, diss_threshold: float, tick: int = 0):
    allowed = set(WORK_ZONES)
    locked_zone = getattr(npc_view, "locked_zone", None)

    if npc_view.energy < 25.0:
        return set()
    if npc_view.energy < 40.0:
        allowed -= {"SCIENCE", "DEVELOPMENT"}
    if npc_view.money < 10.0:
        allowed -= {"SCIENCE"}
    # diss_threshold sets the world's stress ceiling; personality resilience shifts it
    stress_threshold = min(diss_threshold, 50.0 + npc_view.get_stress_tolerance() * 40.0)
    if npc_view.stress_endured > stress_threshold:
        allowed -= {"DEVELOPMENT", "TRADE"}

    # PANTHEON: prestige gate — needs energy > 55 and money > 15 to enter
    if npc_view.energy < 55.0 or npc_view.money < 15.0:
        allowed -= {"PANTHEON"}

    # Can't enter if just left (cooldown)
    last_zone_exit_tick = getattr(npc_view, 'last_zone_exit_tick', None)
    last_zone = getattr(npc_view, 'last_zone', None)
    if last_zone_exit_tick is not None and last_zone:
        if tick - last_zone_exit_tick < 3:
            allowed -= {last_zone}

    # A locked zone is temporarily unavailable until the NPC has recovered and
    # diversified away from it again.
    if locked_zone:
        allowed -= {locked_zone}

    return allowed


REPETITION_GRACE_LOOPS = 2
REPETITION_REWARD_STEP = 0.15
REPETITION_MIN_REWARD_MULT = 0.55
REPETITION_SCORE_STEP = 0.8
REPETITION_ALT_BONUS_STEP = 0.35


def _repetition_overage(loop_count: int) -> int:
    return max(0, loop_count - REPETITION_GRACE_LOOPS)


def repeated_work_reward_multiplier(loop_count: int) -> float:
    overage = _repetition_overage(loop_count)
    if overage == 0:
        return 1.0
    return max(REPETITION_MIN_REWARD_MULT, 1.0 - overage * REPETITION_REWARD_STEP)


def repeated_work_score_penalty(projected_loop_count: int) -> float:
    return min(3.0, _repetition_overage(projected_loop_count) * REPETITION_SCORE_STEP)


def diversification_score_bonus(loop_count: int) -> float:
    if loop_count < REPETITION_GRACE_LOOPS:
        return 0.0
    return min(1.5, (loop_count - (REPETITION_GRACE_LOOPS - 1)) * REPETITION_ALT_BONUS_STEP)


def register_work_zone_entry(npc: NPC, zone: str, tick: int) -> None:
    npc.last_zone_change_tick = tick
    if zone not in WORK_ZONES:
        return

    if getattr(npc, "last_work_zone", None) == zone:
        npc.repeated_work_loops = max(1, getattr(npc, "repeated_work_loops", 0) + 1)
    else:
        npc.last_work_zone = zone
        npc.repeated_work_loops = 1


def finalize_arrival(npc: NPC, zone: str, tick: int) -> None:
    """Unifies the logical transition when an NPC arrives at their target zone."""
    is_new_entry = npc.state != "AT_WORK" or npc.zone != zone

    npc.state = "AT_WORK"
    npc.zone = zone
    npc.prepared_intent = None

    if is_new_entry:
        register_work_zone_entry(npc, zone, tick)


def finalize_exit_to_home(npc: NPC, tick: int, min_rest: int = 8, max_rest: int = 20) -> None:
    """Unifies the logical transition when an NPC leaves their zone to return HOME."""
    npc.state = "AT_HOME"
    npc.zone = "HOME"
    npc.work_budget = WORK_BUDGET
    npc.travel_budget = TRAVEL_BUDGET
    npc.prepared_intent = None
    set_home_rest(npc, tick, min_rest, max_rest)


def compute_basin_entropy(sim_state: SimulationState) -> float:
    occupancies = []
    for basin in sim_state.zone_basins.values():
        if basin.occupancy > 0.001:
            occupancies.append(basin.occupancy)

    if not occupancies or len(occupancies) < 2:
        return 0.0

    total = sum(occupancies)
    if total < 0.001:
        return 0.0

    entropy = 0.0
    for occ in occupancies:
        p = occ / total
        if p > 0.001:
            entropy -= p * math.log2(p)

    return entropy / 2.0

def log_basin_state(sim_state: SimulationState, tick: int):
    if tick % 5000 != 0:
        return

    entropy = compute_basin_entropy(sim_state)
    basin_info = []

    for zone_name in ["SCIENCE", "TRADE", "DEVELOPMENT", "FLEX"]:
        basin = sim_state.zone_basins.get(zone_name)
        if basin is None:
            continue
        basin_info.append(
            f"{zone_name}: occ={basin.occupancy:.2f} "
            f"attr={basin.attraction_score:.2f} "
            f"div={basin.work_diversity:.2f}"
        )

    print(f"🌊 [Tick {tick}] Basin Entropy: {entropy:.3f} | " + " | ".join(basin_info))

def update_zone_basins(world: World, sim_state: SimulationState):
    total_npc_count = len(world.npcs)
    if total_npc_count == 0:
        return

    for zone_name, basin in sim_state.zone_basins.items():
        zone_stat = world.zone_stats.get(zone_name)
        if zone_stat is None:
            continue

        current_pop = zone_stat.current_population
        efficiency = zone_stat.efficiency_rating

        basin.update_basin_state(current_pop, total_npc_count, efficiency)
        basin.travel_emergence_rate = 0.1 + (basin.attraction_score * 0.6)

        if zone_name in ZONE_FACTOR_PROFILES:
            active_factors = sum(
                1 for factor in FACTOR_NAMES
                if ZONE_FACTOR_PROFILES[zone_name].get(factor, {}).get("efficiency", 0.0) > 0
            )
            basin.work_diversity = active_factors / len(FACTOR_NAMES) if FACTOR_NAMES else 0.5
            basin.history_diversity.append(basin.work_diversity)

def personality_affinity(npc_view, zone_name):
    affinity = 0.0
    personality = npc_view.personality
    if zone_name == "SCIENCE":
        affinity += personality.get("ambition", 0.0) * 0.8
        affinity += personality.get("sociability", 0.0) * 0.5
        affinity -= personality.get("stability_bias", 0.0) * 0.4
    elif zone_name == "TRADE":
        affinity += personality.get("ambition", 0.0) * 1.5
        affinity -= personality.get("stability_bias", 0.0) * 0.8
        affinity += personality.get("reactivity", 0.0) * 0.5
    elif zone_name == "DEVELOPMENT":
        affinity += personality.get("stability_bias", 0.0) * 1.0
        affinity += personality.get("resilience", 0.0) * 0.8
        affinity -= personality.get("reactivity", 0.0) * 0.4
    elif zone_name == "FLEX":
        affinity += npc_view.get_risk_tolerance() * 1.2
        affinity += personality.get("sociability", 0.0) * 0.8
        affinity += npc_view.get_recovery_rate() * 0.5
    elif zone_name == "CENTRAL":
        affinity += personality.get("sociability", 0.0) * 1.5
        affinity += personality.get("reactivity", 0.0) * 0.5
        # Consistent social drive accessor
        affinity += npc_view.get_social_drive() * 0.4
    elif zone_name == "PANTHEON":
        affinity += personality.get("ambition", 0.0) * 1.2
        affinity += personality.get("stability_bias", 0.0) * 0.7
        affinity += personality.get("resilience", 0.0) * 0.4
        affinity -= personality.get("reactivity", 0.0) * 0.5
    return affinity

def best_recovery_zone(npc_view, sim_state: SimulationState):
    if npc_view.energy < 35 and "FLEX" in sim_state.zone_mechanics:
        return "FLEX"
    if npc_view.money < 5 and "TRADE" in sim_state.zone_mechanics:
        return "TRADE"
    if npc_view.stress_endured > 85 and "FLEX" in sim_state.zone_mechanics:
        return "FLEX"
    return "HOME"


_DOCTRINE_PREFERRED_ZONE = {
    "MERITOCRATIC": "TRADE",
    "TRANSCENDENT": "PANTHEON",
    "CONSPIRATORIAL": "PANTHEON",
    "REVOLUTIONARY": "DEVELOPMENT",
    "LIBERTARIAN_CULT": "TRADE",
}


def _doctrine_intent_override(npc_view: NPCView, allowed: set[str]) -> Intent | None:
    npc_doctrine = getattr(npc_view, "doctrine", None)
    npc_doc_str = getattr(npc_view, "doctrine_strength", 0.0)
    if not npc_doctrine or npc_doc_str <= 0.6:
        return None

    forced = _DOCTRINE_PREFERRED_ZONE.get(npc_doctrine)
    if forced and forced in allowed and random.random() < npc_doc_str * 0.5:
        return Intent(
            npc_id=npc_view.id,
            desired_zone=forced,
            phase_label=f"DOCTRINE_{DOCTRINE_SHORT.get(npc_doctrine, '?')}",
            confidence=npc_doc_str,
        )
    return None


def _extract_emotion_state(npc_view: NPCView) -> dict[str, float]:
    emx = getattr(npc_view, "emx", {})
    joy = emx_get(emx, "Joy", 0.5)
    sadness = emx_get(emx, "Sadness", 0.5)
    return {
        "joy": joy,
        "sadness": sadness,
        "calm": emx.get("Calm", emx_get(emx, "Acceptance", 0.5)),
        "shame": emx.get("Shame", emx_get(emx, "Disgust", 0.0)),
        "fear": emx_get(emx, "Fear", 0.0),
        "anger": emx_get(emx, "Anger", 0.5),
        "panic": emx.get("Panic", emx_get(emx, "Surprise", 0.5)),
        "pride": emx.get("Pride", emx_get(emx, "Anticipation", 0.5)),
        "love": emx.get("Love", joy),
        "exhaustion": emx.get("Exhaustion", sadness),
    }


def _critical_intent(npc_view: NPCView, allowed: set[str], zone_density, zone_complexes) -> Intent | None:
    if npc_view.energy >= 20 and npc_view.stress_endured <= 80:
        return None

    current_complex = zone_complexes.get(npc_view.zone, {}) if isinstance(zone_complexes, dict) else {}
    behavior = current_complex.get("behavior", "") if isinstance(current_complex, dict) else ""
    emergency_zone = "HOME"
    if behavior == "erratic":
        candidates = [z for z in WORK_ZONES if z != npc_view.zone and z in allowed]
        if candidates:
            emergency_zone = min(candidates, key=lambda z: zone_density.get(z, 0))
    elif behavior == "rejecting":
        candidates = [z for z in ALL_ZONES if z != npc_view.zone and (z == "HOME" or z in allowed)]
        if candidates:
            emergency_zone = min(candidates, key=lambda z: zone_density.get(z, 0))

    return Intent(
        npc_id=npc_view.id,
        desired_zone=emergency_zone,
        phase_label="CRITICAL",
        confidence=0.9,
    )


def _rebellion_intent(npc_view: NPCView, emotions: dict, allowed: set[str], zone_density) -> Intent | None:
    """Hard override for emotional backlash rejection. Breaks clustering basins."""
    anger = emotions.get("anger", 0.0)
    panic = emotions.get("panic", 0.0)

    # Trigger: High antagonist emotion (0.65 threshold to include stable archetypes)
    if max(anger, panic) < 0.65:
        return None

    # Must have accumulated fatigue in the current zone to 'rebel' against it
    fatigue = npc_view.zone_fatigue.get(npc_view.zone, 0)
    if fatigue < 250:
        return None

    # REBELLION: Hard rejection of current zone. Bypass survival fear to find any other place.
    # We prioritize low-density work zones first, fallback to HOME.
    work_candidates = [z for z in WORK_ZONES if z != npc_view.zone and z in allowed]
    if work_candidates:
        target_zone = min(work_candidates, key=lambda z: zone_density.get(z, 0))
    else:
        target_zone = "HOME"

    return Intent(
        npc_id=npc_view.id,
        desired_zone=target_zone,
        phase_label="REBELLION",
        confidence=0.95,
    )


def _decision_phase(npc_view: NPCView) -> str:
    if npc_view.money < 3:
        return "SURVIVAL"
    if npc_view.stress_endured > 80:
        return "STABILIZATION"
    if npc_view.energy > 85:
        return "EXPLORATION"
    return "GROWTH"


def _compute_basin_attractions(zone_basins) -> dict[str, float]:
    basin_attractions: dict[str, float] = {}
    if zone_basins is None:
        return basin_attractions

    total_basin_attr = 0.0
    for zone_name, basin in zone_basins.items():
        attractive = basin.attraction_score * (1.0 - basin.occupancy)
        basin_attractions[zone_name] = max(0.1, attractive)
        total_basin_attr += basin_attractions[zone_name]

    if total_basin_attr > 0.001:
        return {z: basin_attractions[z] / total_basin_attr for z in basin_attractions}
    return basin_attractions


def _apply_current_complex_behavior(score: float, zone_name: str, current_zone: str, current_complex) -> float:
    if not (current_complex and isinstance(current_complex, dict)):
        return score

    current_behavior = current_complex.get("behavior", "")
    if current_behavior == "erratic":
        if zone_name != current_zone:
            score += 0.3
    elif current_behavior == "withdrawn":
        if zone_name == "HOME":
            score += 0.4
    elif current_behavior == "rejecting":
        if zone_name != current_zone:
            score += 0.2
    elif current_behavior == "competitive":
        if zone_name == "TRADE":
            score += 0.15
    return score


def _base_zone_score(
    npc_view: NPCView,
    zone_name: str,
    zone_density,
    joy: float,
    sadness: float,
    calm: float,
    shame: float,
    fear: float,
    anger: float,
    panic: float,
    pride: float,
    love: float,
    exhaustion: float,
) -> float:
    score = 0.0
    personality = npc_view.personality
    if zone_name == "SCIENCE":
        score += max(0.0, (100.0 - npc_view.energy) / 100.0)
        score += calm * 0.35 + pride * 0.20 - panic * 0.25
    elif zone_name == "TRADE":
        score += max(0.0, (50.0 - npc_view.money) / 50.0) * 1.1
        score += (npc_view.stress_endured / 100.0) * 0.3
        score += pride * 0.45 + anger * 0.25 - shame * 0.20
    elif zone_name == "DEVELOPMENT":
        need_energy = (100.0 - npc_view.energy) / 100.0
        score += need_energy * 0.8
        score += calm * 0.40 + pride * 0.25 - anger * 0.10
    elif zone_name == "FLEX":
        stress_pressure = npc_view.stress_endured / 100.0
        score += stress_pressure * 1.2
        score += joy * 0.25 + calm * 0.35 - anger * 0.20 - shame * 0.15
        score += exhaustion * 0.35 + panic * 0.15
        if npc_view.zone == "HOME":
            score += 0.55
    elif zone_name == "HOME":
        home_attract_weight = 0.05
        energy_need = max(0.0, (55.0 - npc_view.energy) / 55.0)
        stress_need = npc_view.stress_endured / 100.0
        recovery_need = energy_need * 0.7 + stress_need * 0.6 + exhaustion * 0.5 + panic * 0.3
        score += recovery_need * home_attract_weight
        score += sadness * 0.10 + exhaustion * 0.12 + panic * 0.08
        if npc_view.energy > 60 and npc_view.stress_endured < 35:
            score -= 3.0
        if getattr(npc_view, "home_ticks_recent", 0) > 8:
            score -= 2.0
    elif zone_name == "CENTRAL":
        ccfg = SHARED_ZONE_CONFIG["CENTRAL"]
        social_need = getattr(npc_view, "social_need", 0.5)
        current_pop = zone_density.get("CENTRAL", 0)
        if social_need > ccfg["social_threshold"]:
            score += (social_need - ccfg["social_threshold"]) * 2.8
        if 1 <= current_pop <= ccfg["crowd_sweet_spot"]:
            score += 0.1 + (current_pop / ccfg["crowd_sweet_spot"]) * 0.5
        elif current_pop > ccfg["crowd_penalty_above"]:
            score -= 0.4
        score += sadness * 0.45
        score += love * 0.55
        score += pride * 0.25
        if joy > 0.6 or love > 0.6:
            score += 0.2
        if npc_view.zone in ("DEVELOPMENT", "SCIENCE", "HOME"):
            zone_ticks = getattr(npc_view, "current_zone_ticks", 0)
            score += min(0.7, zone_ticks / 200.0)
        if npc_view.energy < 45 or npc_view.stress_endured > 55:
            score += 0.35
        score -= shame * 0.20
    elif zone_name == "PANTHEON":
        pcfg = SHARED_ZONE_CONFIG["PANTHEON"]
        legacy_points = getattr(npc_view, "legacy_points", 0.0)
        current_pop = zone_density.get("PANTHEON", 0)
        stability_ratio = max(0.0, 1.0 - npc_view.stress_endured / 100.0)
        wealth_ratio = min(1.0, npc_view.money / 120.0)
        score += stability_ratio * 0.7
        score += wealth_ratio * 0.6
        legacy_desire = (
            personality.get("ambition", 0.5) * 0.5
            + personality.get("stability_bias", 0.5) * 0.3
            + min(1.0, legacy_points / 5.0) * 0.2
        )
        if legacy_desire > 0.5:
            score += (legacy_desire - 0.4) * 1.5
        if current_pop < pcfg["crowd_exclusive_max"]:
            score += (pcfg["crowd_exclusive_max"] - current_pop) * 0.12
        else:
            score -= 0.3
        score += joy * 0.25
        score += pride * 0.65
        score += calm * 0.25
        score -= max(fear, panic) * 0.4
        score -= sadness * 0.3
        if npc_view.money > 180 and npc_view.stress_endured < 35:
            score += 0.5

    return score


def _apply_shared_zone_modifiers(
    score: float,
    npc_view: NPCView,
    zone_name: str,
    current_complex,
    joy: float,
    sadness: float,
    calm: float,
    shame: float,
    fear: float,
    anger: float,
    panic: float,
    pride: float,
) -> float:
    recent_visits = getattr(npc_view, "zones_visited_this_cycle", [])
    last_3_visits = recent_visits[-3:] if len(recent_visits) >= 3 else recent_visits
    recent_count = last_3_visits.count(zone_name)
    stickiness = npc_view.get_goal_stickiness()
    score -= recent_count * 1.2 * (1.0 - stickiness * 0.6)

    skill_bonus = npc_view.skills.get(zone_name, 0.0) * 1.5
    score += skill_bonus
    occupancy_penalty = npc_view.zone_fatigue.get(zone_name, 0) / 40.0
    score -= occupancy_penalty
    score += personality_affinity(npc_view, zone_name)

    score += joy * 0.4 if zone_name == npc_view.zone else 0
    if zone_name in ["SCIENCE", "DEVELOPMENT"]:
        score += joy * 0.3

    if zone_name == "HOME":
        score += sadness * 0.6
    else:
        score -= sadness * 0.3

    if zone_name == npc_view.zone and zone_name != "HOME":
        score += calm * 0.5

    if zone_name == "TRADE":
        score += anger * 0.2
    if zone_name == "HOME":
        score -= anger * 0.2

    if zone_name in ["DEVELOPMENT", "FLEX"]:
        score += panic * 0.2

    score += npc_view.skills.get(zone_name, 0.0) * pride * 0.2

    if npc_view.zone_visit_count.get(zone_name, 0) < 3:
        score -= fear * 0.2

    if zone_name == npc_view.zone:
        score -= shame * (DECISION_DISGUST_PENALTY_MULTIPLIER * 0.33)

    score = _apply_current_complex_behavior(score, zone_name, npc_view.zone, current_complex)

    efficiency_inv = npc_view.get_efficiency_invariance()
    fatigue_threshold = 200 + efficiency_inv * 300
    fatigue = npc_view.zone_fatigue.get(zone_name, 0)
    if fatigue > fatigue_threshold:
        fatigue_penalty = min(1.5, (fatigue - fatigue_threshold) / 200.0)
        score -= fatigue_penalty

    return score


def _apply_zone_market_modifiers(
    score: float,
    npc_view: NPCView,
    zone_name: str,
    zone_stats,
    pride: float,
    shame: float,
) -> float:
    if zone_name in zone_stats:
        score *= zone_stats[zone_name].market_demand
        congestion_penalty = zone_stats[zone_name].congestion_level * 0.6
        score *= (1.0 - congestion_penalty)

    if npc_view.zone_visit_count.get(zone_name, 0) < 2:
        score += 0.2

    zone_visits = npc_view.zone_visit_count.get(zone_name, 0)
    total_visits = sum(npc_view.zone_visit_count.values())
    if total_visits > 0:
        visit_ratio = zone_visits / total_visits
        if visit_ratio < 0.2:
            score += 0.4 * (1.0 + pride * 0.5)
            score += random.uniform(0.0, 0.1)

    if zone_name == npc_view.zone:
        score -= shame * (DECISION_DISGUST_PENALTY_MULTIPLIER * 0.67)

    return score


def _apply_work_zone_modifiers(
    score: float,
    npc_view: NPCView,
    zone_name: str,
    zone_density,
    last_work_zone,
    repeated_work_loops: int,
) -> float:
    if zone_name in WORK_ZONES:
        current_zone_pop = zone_density.get(zone_name, 1)
        raw_multiplier = 1.0 - (max(0, current_zone_pop - 12) / 4.0)
        salary_multiplier = max(-2.0, raw_multiplier)
        effective_skill = max(npc_view.skills.get(zone_name, 0.0), 0.3)
        expected_income = salary_multiplier * effective_skill
        score += expected_income * 2.0

        if current_zone_pop > 12:
            overcrowd_penalty = ((current_zone_pop - 12) / 4.0) * 1.5
            score -= min(4.0, overcrowd_penalty)

        if last_work_zone in WORK_ZONES:
            if zone_name == last_work_zone:
                projected_loop_count = repeated_work_loops + 1
                score -= repeated_work_score_penalty(projected_loop_count)
            else:
                score += diversification_score_bonus(repeated_work_loops)

    return score


def _apply_zone_transition_modifiers(score: float, npc_view: NPCView, zone_name: str, zone_density, zone_modifiers) -> float:
    if zone_name != npc_view.zone:
        current_zone_pop = zone_density.get(npc_view.zone, 1)
        if current_zone_pop >= 4:
            escape_bonus = (current_zone_pop - 3) ** 1.2 * 0.08
            score += min(3.0, escape_bonus)
        if npc_view.zone == "HOME" and npc_view.energy > 50:
            score += 1.05

    if zone_modifiers and zone_name in zone_modifiers:
        mods = zone_modifiers[zone_name]
        score *= mods.get("efficiency_scale", 1.0)
        score *= mods.get("attraction_weight", 1.0)

    return score


def _apply_degenerate_score_modifiers(scores: dict[str, float], npc_view: NPCView) -> dict[str, float]:
    apathy_v = getattr(npc_view, "apathy", 0.0)
    exhaustion_v = getattr(npc_view, "exhaustion", 0.0)
    dissoc_v = getattr(npc_view, "dissociation", 0.0)
    numbness_v = getattr(npc_view, "numbness", 0.0)
    cynicism_v = getattr(npc_view, "cynicism", 0.0)

    if apathy_v > 0.1:
        flatten = 1.0 - apathy_v * 0.55
        scores = {z: s * flatten for z, s in scores.items()}

    if exhaustion_v > 0.15:
        scores["HOME"] = scores.get("HOME", 0.0) + exhaustion_v * 1.2
        for block_z in ("TRADE", "DEVELOPMENT"):
            if block_z in scores:
                scores[block_z] -= exhaustion_v * 0.8

    if dissoc_v > 0.15:
        for block_z in ("CENTRAL", "FLEX"):
            if block_z in scores:
                scores[block_z] -= dissoc_v * 1.0

    if numbness_v > 0.15:
        scores["HOME"] = scores.get("HOME", 0.0) * (1.0 - numbness_v * 0.4)

    if cynicism_v > 0.15:
        for block_z in ("CENTRAL", "PANTHEON"):
            if block_z in scores:
                scores[block_z] -= cynicism_v * 1.2

    return scores


def decide_intent(
    npc_view: NPCView,
    zone_stats,
    zone_density,
    tick,
    diss_threshold,
    zone_complexes=None,
    zone_basins=None,
    zone_modifiers=None,
    sim_state: SimulationState = None
):
    # Injection-ready best_recovery_zone
    zone = best_recovery_zone(npc_view, sim_state) if sim_state else "HOME"
    allowed = allowed_work_zones(npc_view, diss_threshold, tick)
    doctrine_override = _doctrine_intent_override(npc_view, allowed)
    if doctrine_override is not None:
        return doctrine_override

    emotions = _extract_emotion_state(npc_view)
    joy = emotions["joy"]
    sadness = emotions["sadness"]
    calm = emotions["calm"]
    shame = emotions["shame"]
    fear = emotions["fear"]
    anger = emotions["anger"]
    panic = emotions["panic"]
    pride = emotions["pride"]
    love = emotions["love"]
    exhaustion = emotions["exhaustion"]

    critical_override = _critical_intent(npc_view, allowed, zone_density, zone_complexes)
    if critical_override is not None:
        return critical_override

    if max(fear, panic) > 0.85:
        return Intent(
            npc_id=npc_view.id,
            desired_zone="HOME",
            phase_label="FEAR_SURVIVAL",
            confidence=max(fear, panic),
        )

    if not allowed:
        return Intent(npc_id=npc_view.id, desired_zone=zone, phase_label="NO_ENERGY", confidence=0.0)
    if npc_view.state == "AT_WORK":
        return Intent(npc_id=npc_view.id, desired_zone=npc_view.zone, phase_label="WORKING", confidence=1.0)

    phase = _decision_phase(npc_view)
    basin_attractions = _compute_basin_attractions(zone_basins)
    resolved_zone_complexes = zone_complexes if isinstance(zone_complexes, dict) else {}
    current_complex = resolved_zone_complexes.get(npc_view.zone, {})

    scores = {}
    last_work_zone = getattr(npc_view, "last_work_zone", None)
    repeated_work_loops = getattr(npc_view, "repeated_work_loops", 0)
    for zone_name in ALL_ZONES:
        if zone_name not in allowed and zone_name != "HOME":
            continue
        score = _base_zone_score(
            npc_view,
            zone_name,
            zone_density,
            joy,
            sadness,
            calm,
            shame,
            fear,
            anger,
            panic,
            pride,
            love,
            exhaustion,
        )
        if zone_name == "HOME" and zone_name not in allowed:
            score += 0.1

        score = _apply_shared_zone_modifiers(
            score,
            npc_view,
            zone_name,
            current_complex,
            joy,
            sadness,
            calm,
            shame,
            fear,
            anger,
            panic,
            pride,
        )
        score = _apply_zone_market_modifiers(score, npc_view, zone_name, zone_stats, pride, shame)

        if zone_basins is not None and zone_name in basin_attractions:
            basin_attraction_bonus = basin_attractions[zone_name] * 0.4
            score += basin_attraction_bonus

        score = _apply_work_zone_modifiers(
            score,
            npc_view,
            zone_name,
            zone_density,
            last_work_zone,
            repeated_work_loops,
        )
        score = _apply_zone_transition_modifiers(score, npc_view, zone_name, zone_density, zone_modifiers)

        scores[zone_name] = score
    scores = _apply_degenerate_score_modifiers(scores, npc_view)

    if not scores:
        return Intent(npc_id=npc_view.id, desired_zone="HOME", phase_label=phase, confidence=0.0)
    choice = max(scores.items(), key=lambda item: item[1])[0]
    confidence = min(1.0, scores[choice] / (sum(scores.values()) + 1e-6))
    return Intent(npc_id=npc_view.id, desired_zone=choice, phase_label=phase, confidence=confidence)


def _work_profile_deltas(npc_view: NPCView, zone: str) -> tuple[float, float, float, dict[str, float]]:
    money_delta = 0.0
    stress_delta = 0.0
    energy_delta = 0.0
    factor_deltas: dict[str, float] = {}

    if zone not in ZONE_FACTOR_PROFILES:
        return money_delta, stress_delta, energy_delta, factor_deltas

    zone_profile = ZONE_FACTOR_PROFILES[zone]
    deltas = zone_profile.get("deltas", {})
    money_delta = deltas.get("money_delta", 0.0)
    stress_delta = deltas.get("stress_delta", 0.0)
    energy_delta = deltas.get("energy_delta", 0.0)

    for factor_name in FACTOR_NAMES:
        factor_data = zone_profile.get(factor_name, {})
        efficiency = factor_data.get("efficiency", 0.0)
        if efficiency != 0.0:
            base_gain = efficiency * 0.001
            fatigue_mult = getattr(npc_view, "fatigue_penalty_multiplier", 1.0)
            factor_deltas[factor_name] = base_gain * fatigue_mult

    return money_delta, stress_delta, energy_delta, factor_deltas


def _work_skill_delta(npc_view: NPCView, zone: str) -> dict[str, float]:
    skills_delta: dict[str, float] = {}
    if zone not in npc_view.skills:
        return skills_delta

    zone_skill_gain = 0.01
    if zone == "SCIENCE":
        zone_skill_gain = 0.015
    elif zone == "TRADE":
        zone_skill_gain = 0.008
    skills_delta[zone] = zone_skill_gain
    return skills_delta


def _work_emotional_efficiency(npc_view: NPCView, zone: str) -> float:
    emx = getattr(npc_view, "emx", {})
    anger = emx_get(emx, "Anger", 0.0)
    joy = emx_get(emx, "Joy", 0.5)
    sadness = emx_get(emx, "Sadness", 0.0)
    calm = emx.get("Calm", emx_get(emx, "Acceptance", 0.5))
    pride = emx.get("Pride", emx_get(emx, "Anticipation", 0.5))
    exhaustion = emx.get("Exhaustion", 0.0)

    efficiency = 1.0
    if zone == "FLEX":
        efficiency = 1.0 + joy * 0.4 + calm * 0.3 - anger * 0.3 - sadness * 0.2 - exhaustion * 0.2
    elif zone == "TRADE":
        efficiency = 1.0 + pride * 0.4 + anger * 0.2 + joy * 0.2 - sadness * 0.3 - exhaustion * 0.2
    elif zone == "SCIENCE":
        efficiency = 1.0 + calm * 0.4 + pride * 0.2 + joy * 0.1 - anger * 0.2
    elif zone == "DEVELOPMENT":
        efficiency = 1.0 + calm * 0.35 + pride * 0.25 - anger * 0.15 - sadness * 0.1

    return max(0.1, efficiency)


def _apply_repeated_work_adjustment(
    npc_view: NPCView,
    zone: str,
    money_delta: float,
    factor_deltas: dict[str, float],
) -> tuple[float, dict[str, float]]:
    if zone != getattr(npc_view, "last_work_zone", None):
        return money_delta, factor_deltas

    reward_multiplier = repeated_work_reward_multiplier(
        getattr(npc_view, "repeated_work_loops", 0)
    )
    money_delta *= reward_multiplier
    factor_deltas = {
        factor_name: delta * reward_multiplier
        for factor_name, delta in factor_deltas.items()
    }
    return money_delta, factor_deltas


def compute_work_result(npc_view: NPCView, zone, active_events):
    money_delta, stress_delta, energy_delta, factor_deltas = _work_profile_deltas(npc_view, zone)
    skills_delta = _work_skill_delta(npc_view, zone)
    efficiency = _work_emotional_efficiency(npc_view, zone)

    for event in active_events:
        if event.zone_affected is None or event.zone_affected == zone:
            money_delta *= event.money_multiplier
            stress_delta *= event.stress_multiplier
            energy_delta *= event.energy_multiplier

    money_delta *= efficiency
    energy_delta *= efficiency

    money_delta, factor_deltas = _apply_repeated_work_adjustment(
        npc_view,
        zone,
        money_delta,
        factor_deltas,
    )

    if energy_delta < 0:
        energy_delta *= DECISION_ENERGY_LOSS_MULTIPLIER

    return WorkResult(
        npc_id=npc_view.id,
        zone=zone,
        money_delta=money_delta,
        stress_delta=stress_delta,
        energy_delta=energy_delta,
        skills_delta=skills_delta,
        factor_deltas=factor_deltas
    )


def compute_influence_delta(npc_view: NPCView):
    return InfluenceDelta(npc_id=npc_view.id)

def compute_collapse_flag(npc_view: NPCView, stress_threshold=90.0, withdrawal_gap=15):
    if npc_view.stress_endured > stress_threshold:
        return CollapseFlag(
            npc_id=npc_view.id,
            reason="stress",
            severity=min(1.0, (npc_view.stress_endured - stress_threshold) / 50.0)
        )
    return None

def commit_phase(snapshot, world):
    commitments = {}
    breaks = []

    for npc in world.npcs:
        if npc.prepared_intent is None:
            continue
        if npc.state != "AT_HOME":
            continue
        if npc.prepared_intent == "HOME":
            continue

        plan = CommitmentPlan(
            npc_id=npc.id,
            key=f"WORK:{npc.prepared_intent}",
            zone=npc.prepared_intent,
            due_tick=snapshot.tick + WORK_BUDGET,
            strength=1.0
        )
        commitments[npc.id] = plan

    return commitments, breaks

def move_phase(snapshot, commitments, anchors: dict):
    moves = {}
    for npc_id, plan in commitments.items():
        if plan.zone not in anchors:
            continue
        moves[npc_id] = MovementResult(
            npc_id=npc_id,
            new_pos=anchors[plan.zone],
            arrived=False
        )
    return moves


def _get_home_recovery_multiplier(home_population: int) -> float:
    if home_population > 36:
        return 0.15
    if home_population > 29:
        return 0.40
    if home_population > 22:
        return 0.70
    return 1.0


def _apply_routine_state(
    world,
    *,
    viability_floor: float,
    stress_warning_band: float,
    collapse_threshold: float,
    reserve_target: float,
    exploitation_limit: int,
    routine_window: int,
    routine_energy_recovery: float,
    routine_stress_relief: float,
) -> None:
    for npc in world.npcs:
        if npc.energy < viability_floor:
            npc.cycle_phase = "PRESERVATION"
            npc.is_collapsing = False
        elif npc.stress_endured > stress_warning_band:
            npc.cycle_phase = "DEFENSIVE"
        if npc.stress_endured > collapse_threshold:
            npc.is_collapsing = True
            npc.cycle_phase = "COLLAPSED"
        if npc.reserve < reserve_target:
            npc.cycle_phase = "RESISTANCE"
        if hasattr(npc, "zone_switch_count_recent") and npc.zone_switch_count_recent > exploitation_limit:
            npc.energy = max(0.0, npc.energy - 2.0)
            npc.stress_endured = min(100.0, npc.stress_endured + 2.0)

        if not hasattr(npc, "home_ticks_recent"):
            npc.home_ticks_recent = 0
        if npc.zone == "HOME":
            npc.home_ticks_recent += 1
        else:
            npc.home_ticks_recent = max(0, npc.home_ticks_recent - 1)

        if not hasattr(npc, "routine_score"):
            npc.routine_score = 0.5
        if hasattr(npc, "zone_switch_count_recent") and npc.zone_switch_count_recent > 6:
            npc.routine_score -= 0.05
        if npc.stress_endured > 80:
            npc.routine_score -= 0.08
        if npc.home_ticks_recent > 0.7 * routine_window:
            npc.routine_score -= 0.06
        npc.routine_score = max(0.0, min(1.0, npc.routine_score))

        npc.energy = min(100.0, npc.energy + routine_energy_recovery * npc.routine_score)
        npc.stress_endured = max(0.0, npc.stress_endured - routine_stress_relief * npc.routine_score)


def _apply_home_recovery(world) -> None:
    home_pop = world.zone_stats.get("HOME", ZoneStats("HOME")).current_population
    for npc in world.npcs:
        home_mult = _get_home_recovery_multiplier(home_pop)
        if npc.zone == "HOME" and npc.routine_score < 0.25 and npc.home_ticks_recent > 12:
            home_mult *= 0.5
        if npc.zone == "HOME":
            npc.energy = min(100.0, npc.energy + 0.04 * DECISION_HOME_RECOVERY_BONUS * home_mult)
            npc.stress_endured = max(0.0, npc.stress_endured - 0.010 * home_mult)
            if npc.locked_zone:
                locked_fatigue = npc.zone_fatigue.get(npc.locked_zone, 0.0)
                recovery_ticks = max(0, getattr(npc, "home_ticks_recent", 0))
                fatigue_decay = max(1.0, 0.75 * home_mult + recovery_ticks * 0.08)
                locked_fatigue = max(0.0, locked_fatigue - fatigue_decay)
                npc.zone_fatigue[npc.locked_zone] = locked_fatigue
                if recovery_ticks >= ZONE_UNLOCK_VISITS * 4 and locked_fatigue <= ZONE_LOCK_THRESHOLD * 0.45:
                    npc.locked_zone = None
                    npc.unlock_progress.clear()
        else:
            zone_st = world.zone_stats.get(npc.zone)
            if zone_st is not None:
                npc.opportunity_access = min(1.0, 0.7 * npc.opportunity_access + 0.3 * zone_st.efficiency_rating)


def _apply_move_commit(world, buffers, npc_by_id, anchors: dict) -> None:
    for npc_id, move in buffers.moves.items():
        npc = npc_by_id.get(npc_id)
        if not npc:
            continue

        plan = buffers.commitments.get(npc_id)
        if not plan:
            continue

        if npc.state not in ("TRAVELING", "AT_WORK"):
            npc.target = plan.zone
            npc.state = "TRAVELING"
            ax, ay = anchors[plan.zone]
            scatter = 20
            npc.set_target_anchor((
                ax + random.randint(-scatter, scatter),
                ay + random.randint(-scatter, scatter)
            ))

        if move.arrived or (
            npc.state == "TRAVELING"
            and npc.vx_target is None
            and npc.vy_target is None
            and npc.target == plan.zone
        ):
            finalize_arrival(npc, plan.zone, buffers.snapshot.tick)


def _apply_work_commit(buffers, npc_by_id) -> None:
    for npc_id, work in buffers.work.items():
        npc = npc_by_id.get(npc_id)
        if not npc:
            continue

        for skill, delta in work.skills_delta.items():
            npc.skills[skill] = min(1.0, npc.skills.get(skill, 0.0) + delta)

        for factor, delta in work.factor_deltas.items():
            npc.factor_skills[factor] = min(
                1.0, npc.factor_skills.get(factor, 0.0) + delta
            )

        npc.money += getattr(work, "money_delta", 0.0)
        npc.stress_endured = max(
            0.0, min(100.0, npc.stress_endured + getattr(work, "stress_delta", 0.0))
        )
        npc.energy = max(
            0.0, min(100.0, npc.energy + getattr(work, "energy_delta", 0.0))
        )


def _apply_influence_commit(buffers, npc_by_id) -> None:
    for npc_id, infl in buffers.influence.items():
        npc = npc_by_id.get(npc_id)
        if npc:
            npc.stress_endured = max(
                0.0, min(100.0, npc.stress_endured + infl.stress_delta)
            )


def _apply_collapse_commit(world, buffers, npc_by_id) -> None:
    for npc_id, flag in buffers.collapse.items():
        npc = npc_by_id.get(npc_id)
        if npc:
            npc.is_collapsing = True
            npc.cycle_phase = "COLLAPSED"
            collapse_penalty = 10.0
            npc.stress_endured = min(100.0, npc.stress_endured + collapse_penalty)
            npc.energy = max(0.0, npc.energy - 15.0)
            npc.money = max(0.0, npc.money - 20.0)

    for npc in world.npcs:
        if npc.is_collapsing and npc.stress_endured < 90.0:
            npc.is_collapsing = False
            npc.cycle_phase = "RESTING"
        total_degen = (npc.apathy + npc.exhaustion + npc.dissociation
                       + npc.numbness + npc.cynicism)
        if total_degen > 1.5 and not npc.is_collapsing:
            npc.is_collapsing = True
            npc.cycle_phase = "COLLAPSED"


def _persist_commitments(buffers, npc_by_id) -> None:
    for npc_id, plan in buffers.commitments.items():
        npc = npc_by_id.get(npc_id)
        if npc:
            npc.commitments[plan.key] = Commitment(
                key=plan.key,
                created_tick=buffers.snapshot.tick,
                due_tick=plan.due_tick,
                strength=plan.strength
            )

def _apply_work_maintenance(world, tick: int) -> None:
    """Handles passive vitality drain and budget depletion for working NPCs."""
    for npc in world.npcs:
        if npc.state == "AT_WORK":
            npc.energy = max(0.0, npc.energy - PASSIVE_ENERGY_DRAIN_PER_TICK)
            npc.stress_endured = min(100.0, npc.stress_endured + PASSIVE_STRESS_GAIN_PER_TICK)
            npc.work_budget -= WORK_BUDGET_DECAY_PER_TICK

            if npc.work_budget <= 0:
                finalize_exit_to_home(npc, tick)


def apply_commit_reality(world, buffers, anchors: dict):
    VIABILITY_FLOOR = 10.0
    STRESS_WARNING_BAND = 70.0
    COLLAPSE_THRESHOLD = 90.0
    RESERVE_TARGET = 40.0
    EXPLOITATION_LIMIT = 8

    ROUTINE_WINDOW = 20
    ROUTINE_ENERGY_RECOVERY = 0.005
    ROUTINE_STRESS_RELIEF = 0.003
    _apply_routine_state(
        world,
        viability_floor=VIABILITY_FLOOR,
        stress_warning_band=STRESS_WARNING_BAND,
        collapse_threshold=COLLAPSE_THRESHOLD,
        reserve_target=RESERVE_TARGET,
        exploitation_limit=EXPLOITATION_LIMIT,
        routine_window=ROUTINE_WINDOW,
        routine_energy_recovery=ROUTINE_ENERGY_RECOVERY,
        routine_stress_relief=ROUTINE_STRESS_RELIEF,
    )
    _apply_home_recovery(world)
    _apply_work_maintenance(world, buffers.snapshot.tick)

    npc_by_id = {npc.id: npc for npc in world.npcs}
    _apply_move_commit(world, buffers, npc_by_id, anchors)
    _apply_work_commit(buffers, npc_by_id)
    _apply_influence_commit(buffers, npc_by_id)
    _apply_collapse_commit(world, buffers, npc_by_id)
    _persist_commitments(buffers, npc_by_id)


def sample_trait_vector(zone_name: str) -> dict:
    trait_cfg = config.get("continuous_traits", {})
    zone_dist = trait_cfg.get(zone_name, {})
    trait_keys = ["reactivity", "resilience", "ambition", "sociability", "stability_bias"]
    trait_vector = {}
    for k in trait_keys:
        dist = zone_dist.get(k, [0.5, 0.15])
        if isinstance(dist, list):
            mean, std = dist[0], dist[1]
        else:
            mean = dist.get("mean", 0.5)
            std = dist.get("std", 0.15)
        trait_vector[k] = max(0.0, min(1.0, random.gauss(mean, std)))
    return trait_vector


def trait_to_color(traits: dict) -> tuple:
    r = int(80 + 120 * traits.get("ambition", 0.5))
    g = int(80 + 120 * traits.get("sociability", 0.5))
    b = int(80 + 120 * traits.get("stability_bias", 0.5))
    return (r, g, b)


def set_home_rest(npc, tick: int, min_ticks: int = 8, max_ticks: int = 20) -> None:
    npc.home_rest_until = tick + random.randint(min_ticks, max_ticks)


def spawn_npc(zone_name, initial_resources, home_x, home_y):
    trait_vector = sample_trait_vector(zone_name)
    personality = trait_vector
    derived = compute_derived_traits(personality)
    trait_color = trait_to_color(personality)
    emx_sensitivity = compute_emx_sensitivity_from_traits(personality)
    emx_base = compute_emx_base_from_traits(personality)
    shift_offset = random.randint(0, SHIFT_DURATION)
    money = initial_resources.get('money', random.uniform(50, 150))
    energy = initial_resources.get('energy', random.uniform(50, 100))
    stress = initial_resources.get('stress', random.uniform(0, 30))
    npc = NPC(
        x=home_x,
        y=home_y,
        zone=zone_name,
        state="AT_WORK",
        shift_offset=shift_offset,
        money=money,
        energy=energy,
        stress_endured=stress,
    )
    npc.personality = personality
    npc.derived = derived
    npc.trait_color = trait_color
    npc.emx_sensitivity = emx_sensitivity
    npc.personality_name = _trait_label(personality)
    npc.emx = {e: max(0.0, min(1.0, emx_base[e] + random.uniform(-0.1, 0.1))) for e in EMX_EMOTIONS}
    sync_emotion_aliases(npc.emx)
    anchor = ANCHORS_NORM.get(zone_name, [0.5, 0.5])
    scatter = 20
    npc.x = anchor[0] * WIDTH + random.randint(-scatter, scatter)
    npc.y = anchor[1] * HEIGHT + random.randint(-scatter, scatter)
    return npc


def update_zone_occupancy(world, tick):
     for npc in world.npcs:
        if npc.state == "TRAVELING":
            npc.stress_endured += 0.003
            npc.energy = max(0, npc.energy - 0.0015)

     for npc in world.npcs:
        if npc.state == "AT_WORK":
            acceptance = emx_get(npc.emx, "Acceptance", 0.5)
            disgust    = emx_get(npc.emx, "Disgust", 0.0)
            fatigue_rate = (1.0 - acceptance) * 0.3 + disgust * 0.4
            npc.zone_fatigue[npc.zone] = npc.zone_fatigue.get(npc.zone, 0) + max(0.1, fatigue_rate)
            if npc.last_zone != npc.zone:
                npc.current_zone_ticks = 0
                npc.last_zone = npc.zone
            npc.current_zone_ticks += 1

            if not hasattr(npc, 'zones_visited_this_cycle'):
                npc.zones_visited_this_cycle = []

            if not npc.zones_visited_this_cycle or npc.zones_visited_this_cycle[-1] != npc.zone:
                npc.zones_visited_this_cycle.append(npc.zone)
                if len(npc.zones_visited_this_cycle) > 4:
                    npc.zones_visited_this_cycle.pop(0)

            if npc.zone_fatigue[npc.zone] > ZONE_LOCK_THRESHOLD and not npc.locked_zone:
                npc.locked_zone = npc.zone
                npc.unlock_progress.clear()

            if npc.locked_zone and npc.zone != npc.locked_zone:
                npc.unlock_progress.add(npc.zone)
                if len(npc.unlock_progress) >= ZONE_UNLOCK_VISITS:
                    unlocked_zone = npc.locked_zone
                    npc.locked_zone = None
                    npc.unlock_progress.clear()
                    npc.zone_fatigue[unlocked_zone] = 0


def apply_shared_zone_behavior(world, tick):
    """Apply passive effects and manage departure for NPCs AT_WORK in shared zones."""
    shared_pop = {z: 0 for z in SHARED_ZONES}
    for npc in world.npcs:
        if npc.state == "AT_WORK" and npc.zone in SHARED_ZONES:
            shared_pop[npc.zone] += 1

    for npc in world.npcs:
        if npc.state != "AT_WORK" or npc.zone not in SHARED_ZONES:
            continue

        if npc.zone == "CENTRAL":
            ccfg = SHARED_ZONE_CONFIG["CENTRAL"]

            if npc.shared_zone_entry_tick == 0 or npc.last_zone != "CENTRAL":
                npc.shared_zone_entry_tick = tick
                min_visit, max_visit = _visit_tick_range(ccfg)
                npc.planned_departure_tick = tick + random.randint(
                    min_visit, max_visit
                )

            crowd = shared_pop["CENTRAL"]
            crowd_mult = min(1.8, 1.0 + crowd / ccfg["crowd_sweet_spot"])
            npc.energy = min(100.0, npc.energy + ccfg["energy_gain_per_tick"] * crowd_mult)
            npc.stress_endured = max(0.0, npc.stress_endured - ccfg["stress_loss_per_tick"] * crowd_mult)

            if tick >= npc.planned_departure_tick or npc.social_need < 0.1:
                finalize_exit_to_home(npc, tick, min_rest=8, max_rest=20)

        elif npc.zone == "PANTHEON":
            pcfg = SHARED_ZONE_CONFIG["PANTHEON"]

            if npc.shared_zone_entry_tick == 0 or npc.last_zone != "PANTHEON":
                npc.shared_zone_entry_tick = tick
                min_visit, max_visit = _visit_tick_range(pcfg)
                npc.planned_departure_tick = tick + random.randint(
                    min_visit, max_visit
                )

            mat_contrib = min(PANTHEON_CONTRIB_MATERIAL, max(0.0, npc.money - pcfg["entry_money_min"]))
            eng_contrib = min(PANTHEON_CONTRIB_ENERGY,  max(0.0, npc.energy - 32.0))

            npc.money  = max(0.0, npc.money  - mat_contrib)
            npc.energy = max(0.0, npc.energy - eng_contrib)
            PANTHEON.contribute(mat_contrib, eng_contrib, tick)

            npc.legacy_points += mat_contrib + eng_contrib * 0.5

            npc.factor_skills["KNOWLEDGE"] = min(
                1.0, npc.factor_skills.get("KNOWLEDGE", 0.0) + pcfg["knowledge_gain_tick"]
            )

            npc.stress_endured = min(100.0, npc.stress_endured + pcfg["stress_gain_tick"])

            if (tick >= npc.planned_departure_tick
                    or npc.money  < pcfg["entry_money_min"]
                    or npc.energy < 30.0):
                finalize_exit_to_home(npc, tick, min_rest=10, max_rest=24)
                if npc.money < 20.0:
                    npc.prepared_intent = "TRADE"
                elif npc.energy < 35.0:
                    npc.prepared_intent = "HOME"
                else:
                    npc.prepared_intent = None


def simtick(world: World, tick: int, world_state: WorldRuntimeState, anchors: dict):
    sim_state = world_state.sim_state
    env_state = world_state.env_state
    update_social_need(world)
    apply_shared_zone_behavior(world, tick)
    update_central_doctrine(world, tick, sim_state)
    update_zone_occupancy(world, tick)
    update_emx(world, env_state, tick)
    update_atmosphere(world, env_state, tick)
    update_cell_atmosphere(world, env_state, tick)
    zone_density, zone_stats_view = refresh_world_zone_stats(world)
    update_zone_basins(world, sim_state)
    snapshot = build_snapshot(world, tick, zone_density, zone_stats_view)
    buffers = TickBuffers(snapshot=snapshot)
    npc_by_id = {npc.id: npc for npc in world.npcs}
    for npc_id, npc_view in snapshot.npc.items():
        npc = npc_by_id.get(npc_id)
        if npc is None:
            continue

        if npc.state in ("AT_WORK", "TRAVELING"):
            continue

        if tick < getattr(npc, "home_rest_until", 0):
            continue

        if npc.prepared_intent is not None:
            continue

        intent = decide_intent(
            npc_view,
            snapshot.zone_stats,
            snapshot.zone_density,
            tick,
            world.dissonance_threshold,
            zone_complexes=env_state.zone_complexes,
            zone_basins=sim_state.zone_basins,
            zone_modifiers=world.zone_modifiers,
            sim_state=sim_state
        )

        if intent.desired_zone == "HOME":
            npc.prepared_intent = None
            set_home_rest(npc, tick, 4, 10)
        else:
            npc.prepared_intent = intent.desired_zone
    buffers.commitments, buffers.breaks = commit_phase(snapshot, world)
    buffers.moves = move_phase(snapshot, buffers.commitments, anchors)
    for npc_id, npc_view in snapshot.npc.items():
        if npc_view.state == "AT_WORK":
            buffers.work[npc_id] = compute_work_result(
                npc_view, npc_view.zone, snapshot.active_events
            )
    for npc_id, npc_view in snapshot.npc.items():
        buffers.influence[npc_id] = compute_influence_delta(npc_view)
    for npc_id, npc_view in snapshot.npc.items():
        flag = compute_collapse_flag(npc_view, 90.0, WITHDRAWAL_GAP)
        if flag:
            buffers.collapse[npc_id] = flag
    apply_commit_reality(world, buffers, anchors)
    log_basin_state(sim_state, tick)
    return buffers
