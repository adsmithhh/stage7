
from __future__ import annotations

import math
from typing import Dict, Optional, cast
from collections import deque

from config.constants import (
    ANCHOR_EMX_SIGNATURE,
    ANCHOR_FIELD_EMIT_RATE,
    ANCHORS_NORM,
    DEGEN_COLLAPSE_ADD,
    DEGEN_DECAY_RATE,
    DEGEN_EMOTION_RESET,
    EMOTIONAL_COMPLEXES,
    EMX_CELL_ABSORB_RATE,
    EMX_CELL_BASE_DECAY,
    EMX_CELL_CANCEL_RATIO,
    EMX_CELL_EMIT_RATE,
    EMX_CELL_EMPTY_EXTRA_DECAY,
    EMX_CELL_GRID_COLS,
    EMX_CELL_GRID_ENABLED,
    EMX_CELL_GRID_ROWS,
    EMX_CELL_NEIGHBOR_MIX,
    EMX_CELL_OCCUPANCY_REF,
    EMX_COMPLEX_SECOND_THRESHOLD,
    EMX_COMPLEX_TOP_THRESHOLD,
    EMX_COMPLEX_ZONE_THRESHOLD_SCALE,
    EMX_DEPENDENCY_CONTEXT_GAIN,
    EMX_DEPENDENCY_COUPLING,
    EMX_DEPENDENCY_DRIFT_BASE,
    EMX_DEPENDENCY_DRIFT_MAX,
    EMX_DEPENDENCY_ENABLED,
    EMX_DEPENDENCY_FIELD_GAIN,
    EMX_DEPENDENCY_HOMEOSTASIS,
    EMX_DEPENDENCY_VOLATILITY_GAIN,
    EMX_DEPENDENCY_WEIGHTS,
    EMX_EMOTIONS,
    EMX_OPPOSITE_PAIRS,
    EMX_OVERUSE_BACKLASH_DRAIN,
    EMX_OVERUSE_BACKLASH_GAIN,
    EMX_OVERUSE_BACKLASH_MAP,
    EMX_OVERUSE_BACKLASH_START,
    EMX_PERSONALITY_BASE,
    EMX_WEATHER_ABSORB_RATE,
    EMX_WEATHER_CANCEL_RATIO,
    EMX_WEATHER_DECAY,
    EMX_WEATHER_EMIT_RATE,
    EMX_WEATHER_HISTORY_LENGTH,
    HEIGHT,
    NPC_PRIMARY_DYADS,
    OVERDRIVE_COLLAPSE_MAP,
    OVERDRIVE_DURATION,
    OVERDRIVE_THRESHOLD,
    WIDTH,
    WORK_ZONES,
    clamp01,
    sync_emotion_aliases,
)
from simulation.api import anchors_to_pixels, EnvironmentState

# Injected by emx.py after pygame init
ANCHORS: dict = {}
EMOTION_TO_IDX = {emotion: idx for idx, emotion in enumerate(EMX_EMOTIONS)}

INTERLEAVE_WINDOW = 5


def configure_runtime(*, anchors: dict | None = None) -> None:
    global ANCHORS
    if anchors is not None:
        ANCHORS = anchors


def compute_emx_dependency_shift(emotion: str, current_emx: Dict[str, float]) -> float:
    weights = EMX_DEPENDENCY_WEIGHTS.get(emotion, {})
    shift = 0.0
    for source_emotion, weight in weights.items():
        source_val = current_emx.get(source_emotion, 0.5)
        shift += (source_val - 0.5) * weight
    return shift


def compute_emx_context_multiplier(stress: float, energy: float, money: float, fatigue: float) -> float:
    resource_pressure = max(0.0, 0.45 - energy) + max(0.0, 0.45 - money)
    context_pressure = (0.50 * stress) + (0.30 * resource_pressure) + (0.20 * fatigue)
    return 1.0 + EMX_DEPENDENCY_CONTEXT_GAIN * context_pressure

def compute_npc_dyad(emx: dict) -> Optional[str]:
    """
    Detect a Plutchik primary dyad from the NPC's current EMX state.
    Returns the dyad name if the top-2 elevated emotions form an adjacent pair,
    else None (NPC is in an undifferentiated or single-emotion state).
    """
    # Only consider emotions elevated above neutral (0.5)
    elevated = {e: v for e, v in emx.items() if v > 0.55}
    if len(elevated) < 2:
        return None
    # Top 2 by intensity
    top2 = sorted(elevated.keys(), key=lambda k: elevated[k], reverse=True)[:2]
    pair = frozenset(top2)
    return NPC_PRIMARY_DYADS.get(pair)


def compute_emx_archetype(emx: dict) -> str:
    """Return the archetype whose emotional signature best matches current EMX state (cosine similarity)."""
    emx_vals = [emx.get(e, 0.0) for e in EMX_EMOTIONS]
    emx_mag = math.sqrt(sum(v * v for v in emx_vals)) or 1.0
    best, best_score = "Anchor", -1.0
    for archetype, signature in EMX_PERSONALITY_BASE.items():
        sig_vals = [signature[e] for e in EMX_EMOTIONS]
        sig_mag = math.sqrt(sum(v * v for v in sig_vals)) or 1.0
        score = sum(a * b for a, b in zip(emx_vals, sig_vals)) / (emx_mag * sig_mag)
        if score > best_score:
            best_score = score
            best = archetype
    return best


def compute_emx_sensitivity_from_traits(traits: dict) -> dict:
    sensitivity = {emotion: 1.0 for emotion in EMX_EMOTIONS}
    boosts = {
        "Joy": 1.0 + 0.3 * traits.get("sociability", 0.5),
        "Love": 1.0 + 0.35 * traits.get("sociability", 0.5),
        "Calm": 1.0 + 0.4 * traits.get("stability_bias", 0.5),
        "Pride": 1.0 + 0.35 * traits.get("ambition", 0.5),
        "Anger": 0.7 + 0.7 * traits.get("ambition", 0.5),
        "Fear": 0.7 + 0.7 * (1.0 - traits.get("resilience", 0.5)),
        "Panic": 0.8 + 0.6 * traits.get("reactivity", 0.5),
        "Sadness": 0.7 + 0.6 * (1.0 - traits.get("resilience", 0.5)),
        "Shame": 0.8 + 0.4 * traits.get("reactivity", 0.5),
        "Exhaustion": 0.8 + 0.3 * (1.0 - traits.get("resilience", 0.5)),
    }
    for emotion, value in boosts.items():
        if emotion in sensitivity:
            sensitivity[emotion] = value
    return sensitivity

def compute_emx_base_from_traits(traits: dict) -> dict:
    base = {emotion: 0.28 for emotion in EMX_EMOTIONS}
    boosts = {
        "Joy": 0.45 + 0.2 * traits.get("sociability", 0.5),
        "Love": 0.40 + 0.25 * traits.get("sociability", 0.5),
        "Calm": 0.42 + 0.25 * traits.get("stability_bias", 0.5),
        "Pride": 0.40 + 0.25 * traits.get("ambition", 0.5),
        "Anger": 0.18 + 0.45 * traits.get("ambition", 0.5),
        "Fear": 0.22 + 0.45 * (1.0 - traits.get("resilience", 0.5)),
        "Panic": 0.18 + 0.30 * traits.get("reactivity", 0.5),
        "Sadness": 0.25 + 0.35 * (1.0 - traits.get("resilience", 0.5)),
        "Shame": 0.20 + 0.25 * traits.get("reactivity", 0.5),
        "Exhaustion": 0.18 + 0.30 * (1.0 - traits.get("resilience", 0.5)),
    }
    for emotion, value in boosts.items():
        if emotion in base:
            base[emotion] = value
    return base

def _trait_label(traits: dict) -> str:
    # Assign a label based on dominant trait
    keys = ["reactivity", "resilience", "ambition", "sociability", "stability_bias"]
    dominant = max(keys, key=lambda k: traits.get(k, 0.5))
    label_map = {
        "reactivity": "Connector",
        "resilience": "Survivor",
        "ambition": "Climber",
        "sociability": "Anchor",
        "stability_bias": "Anchor"
    }
    return label_map.get(dominant, "Unknown")


def update_emx(world, env_state: EnvironmentState, tick: int = 0):
    """Drive Plutchik emotions from existing NPC stats — foundation for dependency system."""
    # ENHANCED: Calculate zone populations for emotional contagion
    zone_populations = {}
    for zone in WORK_ZONES:
        count = sum(1 for npc in world.npcs if npc.zone == zone and npc.state == "AT_WORK")
        zone_populations[zone] = count

    for npc in world.npcs:
        e = npc.emx
        s  = npc.stress_endured / 100.0
        en = npc.energy / 100.0
        m  = min(1.0, npc.money / 200.0)
        fat = min(1.0, npc.zone_fatigue.get(npc.zone, 0) / 500.0)

        # NEW: Dramatic emotion spike when NPCs stay too long in place
        # When fatigue exceeds 60% of threshold, emotions become uncontrollable
        fatigue_ratio = fat  # fat is already normalized (fatigue / 500.0)
        emotion_override = max(0.0, (fatigue_ratio - 0.6) * 2.5)  # Spikes above 60% fatigue

        # ENHANCED: Crowding amplifies fatigue effect on emotions
        zone_pop = zone_populations.get(npc.zone, 1)
        crowding_amplifier = 1.0 + max(0.0, (zone_pop - 4) * 0.1)  # Each NPC beyond 4 adds 10% amplification
        crowding_amplifier = min(2.5, crowding_amplifier)  # Cap at 2.5x amplification
        emotion_override *= crowding_amplifier

        targets = {emotion: 0.18 for emotion in EMX_EMOTIONS}
        target_updates = {
            "Joy": en * 0.6 + m * 0.4,
            "Love": (1.0 - s) * 0.45 + (1.0 - fat) * 0.25 + (1.0 - getattr(npc, "social_need", 0.5)) * 0.30,
            "Calm": (1.0 - fat) * 0.6 + (1.0 - s) * 0.4,
            "Pride": en * 0.4 + (1.0 - s) * 0.35 + m * 0.25,
            "Anger": s * 0.8 + fat * 0.5 + emotion_override * 0.8,
            "Fear": max(0.0, 0.6 - m) * 0.6 + max(0.0, 0.4 - en) * 0.4,
            "Panic": max(0.0, s - 0.45) * 0.6 + emotion_override * 0.5,
            "Sadness": (1.0 - en) * 0.5 + (1.0 - m) * 0.5,
            "Shame": fat * 0.35 + s * 0.25,
            "Exhaustion": fat * 0.75 + (1.0 - en) * 0.45,
        }
        for emotion, value in target_updates.items():
            if emotion in targets:
                targets[emotion] = value

        # Apathy suppresses all EMX targets — collapsed NPC feels less of everything
        if npc.apathy > 0.05:
            apathy_suppress = 1.0 - npc.apathy * 0.3
            targets = {em: targets[em] * apathy_suppress for em in targets}

        # Cap EMX targets
        targets = {em: min(0.85, targets[em]) for em in targets}
        # Personality sensitivity — each archetype amplifies/dampens emotions differently
        sensitivity = getattr(npc, 'emx_sensitivity', None)
        if sensitivity is None:
            personality = getattr(npc, 'personality', {})
            sensitivity = compute_emx_sensitivity_from_traits(personality)
        targets = {em: clamp01(targets[em] * sensitivity.get(em, 1.0)) for em in targets}

        if EMX_DEPENDENCY_ENABLED:
            field = env_state.zone_atmosphere.get(npc.zone, {}) if npc.zone in env_state.zone_atmosphere else {}
            context_multiplier = compute_emx_context_multiplier(s, en, m, fat)

            for emotion in EMX_EMOTIONS:
                dependency_shift = compute_emx_dependency_shift(emotion, e)
                # TEMPORAL INTERLEAVING: Only update field influence every N ticks
                # No scaling multiplier needed as 'targets' is a goal for gradual drift.
                field_shift = 0.0
                if (tick + npc.id) % INTERLEAVE_WINDOW == 0:
                    field_shift = (field.get(emotion, 0.0) - 0.15) * EMX_DEPENDENCY_FIELD_GAIN
                
                targets[emotion] = clamp01(
                    targets[emotion]
                    + (dependency_shift * EMX_DEPENDENCY_COUPLING * context_multiplier)
                    + field_shift
                )

        volatility = sum(abs(e.get(emotion, 0.5) - 0.5) for emotion in EMX_EMOTIONS) / len(EMX_EMOTIONS)
        rate = EMX_DEPENDENCY_DRIFT_BASE * (1.0 + EMX_DEPENDENCY_VOLATILITY_GAIN * volatility * 2.0)
        rate *= compute_emx_context_multiplier(s, en, m, fat)
        rate = min(EMX_DEPENDENCY_DRIFT_MAX, rate)

        for emotion, target in targets.items():
            current = e.get(emotion, 0.5)
            e[emotion] = clamp01(current + (target - current) * rate)

        calm_val = e.get("Calm", 0.0)
        love_val = e.get("Love", 0.0)
        exhaustion_val = e.get("Exhaustion", 0.0)
        panic_val = e.get("Panic", 0.0)
        home_regulation = 1.12 if npc.zone == "HOME" else 1.0
        stress_relief = max(0.0, calm_val * 0.020 + love_val * 0.010 - panic_val * 0.004) * home_regulation
        npc.stress_endured = max(0.0, npc.stress_endured - stress_relief)
        energy_recovery = max(0.0, calm_val * 0.008 + love_val * 0.004 - exhaustion_val * 0.004) * home_regulation
        npc.energy = min(100.0, npc.energy + energy_recovery)

        # NEW: Calibrated Plutchik Self-Looping (Resonance & Dissonance)
        zone_atm = env_state.zone_atmosphere.get(npc.zone, {})
        
        # 1. Resonance Magnification
        for em in EMX_EMOTIONS:
            npc_val = e.get(em, 0.0)
            cell_val = zone_atm.get(em, 0.0)
            if npc_val > 0.3 and cell_val > 0.3:
                e[em] = clamp01(e[em] + npc_val * 0.10)
                
        # 2. Dissonance Denial
        for pos, neg in EMX_OPPOSITE_PAIRS:
            if e.get(pos, 0.0) > 0.2 and zone_atm.get(neg, 0.0) > 0.2:
                e[pos] = max(0.0, e[pos] - 0.15)
            if e.get(neg, 0.0) > 0.2 and zone_atm.get(pos, 0.0) > 0.2:
                e[neg] = max(0.0, e[neg] - 0.15)

        # Homeostasis — very weak pull toward born archetype; prevents full collapse to one type
        p_name = getattr(npc, 'personality_name', 'Anchor')
        p_base = EMX_PERSONALITY_BASE.get(p_name, {})
        for emotion in EMX_EMOTIONS:
            home = p_base.get(emotion, 0.5)
            e[emotion] = clamp01(e[emotion] + (home - e[emotion]) * EMX_DEPENDENCY_HOMEOSTASIS)

        # Surprise spike on zone change
        if npc.last_zone is not None and npc.last_zone != npc.zone:
            if "Panic" in e:
                e["Panic"] = min(1.0, e.get("Panic", 0.0) + 0.3)

            # NEW: RAPID EMOTION DECAY when leaving crowded zones
            # When NPCs escape the zone cluster, their amplified emotions rapidly decrease
            # Emotions that were boosted by contagion (Anger, Disgust, etc.) fade quickly
            last_zone_pop = sum(1 for npc_check in world.npcs
                               if npc_check.zone == npc.last_zone and npc_check.state == "AT_WORK")

            if last_zone_pop >= 3:  # They just left a crowded zone
                decay_strength = 0.15 + (last_zone_pop - 3) * 0.05  # Stronger decay for larger crowds

                # Dampen negative/intense emotions rapidly
                for neg_emotion in ["Anger", "Fear", "Panic", "Sadness", "Shame"]:
                    if neg_emotion in e:
                        e[neg_emotion] = clamp01(e[neg_emotion] * (1.0 - decay_strength))

                # Boost positive emotions when escaping
                for pos_emotion in ["Joy", "Calm", "Pride", "Love"]:
                    if pos_emotion in e:
                        e[pos_emotion] = clamp01(e[pos_emotion] + decay_strength * 0.2)

        # ── OVERDRIVE COLLAPSE → degenerate emotion states ───────────────────
        tracked_emotions = set(OVERDRIVE_COLLAPSE_MAP) | set(EMX_OVERUSE_BACKLASH_MAP)
        for emotion in tracked_emotions:
            degen_attr = OVERDRIVE_COLLAPSE_MAP.get(emotion)
            val = e.get(emotion, 0.0)
            if val > OVERDRIVE_THRESHOLD:
                ticks = npc.emx_overdrive_ticks.get(emotion, 0) + 1
                npc.emx_overdrive_ticks[emotion] = ticks

                backlash_target = EMX_OVERUSE_BACKLASH_MAP.get(emotion)
                if backlash_target and ticks >= EMX_OVERUSE_BACKLASH_START:
                    span = max(1, OVERDRIVE_DURATION - EMX_OVERUSE_BACKLASH_START)
                    buildup = min(1.0, (ticks - EMX_OVERUSE_BACKLASH_START + 1) / span)
                    e[emotion] = clamp01(e[emotion] - EMX_OVERUSE_BACKLASH_DRAIN * buildup)
                    e[backlash_target] = clamp01(
                        e.get(backlash_target, 0.0) + EMX_OVERUSE_BACKLASH_GAIN * buildup
                    )

                if degen_attr and npc.emx_overdrive_ticks[emotion] >= OVERDRIVE_DURATION:
                    # Collapse: bleed into degenerate state, reset emotion
                    current_degen = getattr(npc, degen_attr, 0.0)
                    setattr(npc, degen_attr, min(1.0, current_degen + DEGEN_COLLAPSE_ADD))
                    e[emotion] = DEGEN_EMOTION_RESET
                    npc.emx_overdrive_ticks[emotion] = 0
            else:
                npc.emx_overdrive_ticks[emotion] = 0

        # ── Degenerate state slow decay ───────────────────────────────────────
        sync_emotion_aliases(e)
        for dattr in ("apathy", "exhaustion", "dissociation", "numbness", "cynicism"):
            v = getattr(npc, dattr, 0.0)
            if v > 0.0:
                setattr(npc, dattr, max(0.0, v - DEGEN_DECAY_RATE))

        # Update per-NPC dyad state
        npc.emx_dyad = compute_npc_dyad(npc.emx)


def update_atmosphere(world, env_state: EnvironmentState, tick: int):
    """Emotional weather: NPCs emit, opposites cancel, complexes emerge."""
    atm = env_state.zone_atmosphere

    # Store complexes for visualization in EnvironmentState
    zone_complexes = env_state.zone_complexes
    weather_history = env_state.zone_weather_history

    # ENHANCED: Calculate zone densities for emotional resonance
    zone_density = {}
    for zone in atm:
        count = sum(1 for npc in world.npcs if npc.zone == zone and npc.state == "AT_WORK")
        zone_density[zone] = count

    # 1. Decay
    for zone in atm:
        for e in EMX_EMOTIONS:
            atm[zone][e] *= EMX_WEATHER_DECAY

    # 2. ENHANCED: Emit with DENSITY-BASED RESONANCE AMPLIFICATION
    # INTERLEAVED: Only 1/5 NPCs emit per tick, scaled by 5.0
    for npc in world.npcs:
        if npc.state == "AT_WORK" and npc.zone in atm:
            if (tick + npc.id) % INTERLEAVE_WINDOW == 0:
                density = zone_density.get(npc.zone, 1)
                resonance_multiplier = 1.0 + (density - 1) * 0.3
                resonance_multiplier = min(2.5, resonance_multiplier)

                for e in EMX_EMOTIONS:
                    emission = npc.emx.get(e, 0.0) * EMX_WEATHER_EMIT_RATE * resonance_multiplier * INTERLEAVE_WINDOW
                    atm[npc.zone][e] = min(1.0, atm[npc.zone][e] + emission)

    # 3. Plutchik cancellation
    for zone in atm:
        for pos, neg in EMX_OPPOSITE_PAIRS:
            cancel = min(atm[zone][pos], atm[zone][neg]) * EMX_WEATHER_CANCEL_RATIO
            atm[zone][pos] = max(0.0, atm[zone][pos] - cancel)
            atm[zone][neg] = max(0.0, atm[zone][neg] - cancel)

    # 4. NEW: Detect emotional complexes
    for zone in atm:
        # Find the top two emotions
        emotions = sorted(atm[zone].items(), key=lambda x: x[1], reverse=True)
        if len(emotions) < 2:
            zone_complexes[zone] = None
            continue

        top_emotion, top_value = emotions[0]
        second_emotion, second_value = emotions[1]
        zone_threshold_scale = EMX_COMPLEX_ZONE_THRESHOLD_SCALE.get(zone, 1.0)
        zone_top_threshold = EMX_COMPLEX_TOP_THRESHOLD * zone_threshold_scale
        zone_second_threshold = EMX_COMPLEX_SECOND_THRESHOLD * zone_threshold_scale

        # Check if they form a complex (both reasonably strong)
        complex_key = tuple(sorted([top_emotion, second_emotion]))
        if (
            complex_key in EMOTIONAL_COMPLEXES
            and top_value > zone_top_threshold
            and second_value > zone_second_threshold
        ):
            zone_complexes[zone] = EMOTIONAL_COMPLEXES[complex_key]
        else:
            zone_complexes[zone] = None

        zone_complex_data = zone_complexes.get(zone)
        complex_name = None
        if zone_complex_data is not None:
            complex_name = str(zone_complex_data.get("name", ""))

        history_bucket = weather_history.get(zone)
        if history_bucket is None:
            history_bucket = deque(maxlen=EMX_WEATHER_HISTORY_LENGTH)
            weather_history[zone] = history_bucket

        history_bucket.append({
            "tick": tick,
            "dominant": top_emotion,
            "dominant_intensity": top_value,
            "second": second_emotion,
            "second_intensity": second_value,
            "complex": complex_name,
            "complex_strength": min(top_value, second_value) if zone_complex_data is not None else 0.0,
        })

    # 5. Absorb - now with complex effects
    # INTERLEAVED: Only 1/5 NPCs absorb per tick, scaled by 5.0
    for npc in world.npcs:
        if npc.state == "AT_WORK" and npc.zone in atm:
            if (tick + npc.id) % INTERLEAVE_WINDOW == 0:
                # Normal absorption
                for e in EMX_EMOTIONS:
                    npc.emx[e] = max(0.0, min(1.0, npc.emx[e] + atm[npc.zone].get(e, 0.0) * EMX_WEATHER_ABSORB_RATE * INTERLEAVE_WINDOW))

                if EMX_CELL_GRID_ENABLED and env_state.cell_atmosphere:
                    cols = EMX_CELL_GRID_COLS
                    rows = EMX_CELL_GRID_ROWS
                    cell_idx = cell_index_for_position(npc.x, npc.y, cols, rows)
                    if 0 <= cell_idx < len(env_state.cell_atmosphere):
                        local_atm = env_state.cell_atmosphere[cell_idx]
                        for e in EMX_EMOTIONS:
                            npc.emx[e] = max(0.0, min(1.0, npc.emx[e] + local_atm.get(e, 0.0) * EMX_CELL_ABSORB_RATE * INTERLEAVE_WINDOW))

            # Complex effects on NPCs (Subtle behavioral nudges, keep every tick as they are tiny)
            complex_data = zone_complexes.get(npc.zone)
            if complex_data:
                behavior = complex_data["behavior"]
                if behavior == "reflective":
                    npc.energy = max(0, npc.energy - 0.0002)  # Slows down
                elif behavior == "erratic":
                    pass
                elif behavior == "competitive":
                    npc.stress_endured = min(100, npc.stress_endured + 0.0005)
                elif behavior == "passive":
                    npc.energy = min(100, npc.energy + 0.00015)
                elif behavior == "curious":
                    npc.energy = min(100, npc.energy + 0.00025)
                elif behavior == "withdrawn":
                    pass
                elif behavior == "rejecting":
                    pass
                elif behavior == "proactive":
                    npc.energy = min(100, npc.energy + 0.0002)


def cell_index_for_position(x, y, cols, rows):
    col = max(0, min(cols - 1, int((x / max(1, WIDTH)) * cols)))
    row = max(0, min(rows - 1, int((y / max(1, HEIGHT)) * rows)))
    return row * cols + col


def ensure_cell_grid_state(env_state: EnvironmentState):
    if not EMX_CELL_GRID_ENABLED:
        return
    cell_count = EMX_CELL_GRID_COLS * EMX_CELL_GRID_ROWS
    if len(env_state.cell_atmosphere) != cell_count:
        env_state.cell_atmosphere = [
            {emotion: 0.0 for emotion in EMX_EMOTIONS}
            for _ in range(cell_count)
        ]
    if len(env_state.cell_population) != cell_count:
        env_state.cell_population = [0 for _ in range(cell_count)]

    # Size pre-allocated buffers:
    num_emotions = len(EMX_EMOTIONS)
    if len(env_state._cell_sums) != cell_count:
        env_state._cell_sums = [[0.0] * num_emotions for _ in range(cell_count)]
    if len(env_state._cell_new) != cell_count:
        env_state._cell_new = [[0.0] * num_emotions for _ in range(cell_count)]
    if len(env_state._cell_old) != cell_count:
        env_state._cell_old = [[0.0] * num_emotions for _ in range(cell_count)]
    if len(env_state._cell_populations) != cell_count:
        env_state._cell_populations = [0] * cell_count
    if len(env_state._anchor_field_sums) != cell_count:
        env_state._anchor_field_sums = [[0.0] * num_emotions for _ in range(cell_count)]


def compute_anchor_field(zone_name: str, world, zone_stats: dict, tick: int) -> tuple:
    """
    Returns (radius: float, intensity: float, color: tuple) for each anchor's dynamic field.
    """
    from config.constants import (
        TRADE_COLOR, DEV_COLOR, FLEX_COLOR, WHITE, _CENTRAL_COL, _PANTHEON_COL,
        SHARED_ZONE_CONFIG, PANTHEON, PANTHEON_TIERS
    )
    BASE  = 28.0
    npcs  = world.npcs
    total = max(1, len(npcs))
    zs    = zone_stats.get(zone_name)

    if zone_name == "SCIENCE":
        eff   = zs.efficiency_rating if zs else 0.5
        know  = sum(n.factor_skills.get("KNOWLEDGE", 0.0)
                    for n in npcs if n.zone == zone_name and n.state == "AT_WORK")
        pop   = zs.current_population if zs else 0
        know_avg = know / max(1, pop)
        intensity = eff * 0.6 + know_avg * 0.4
        radius    = BASE + eff * 38 + know_avg * 22
        color     = (100, 200, 255)

    elif zone_name == "TRADE":
        eff   = zs.efficiency_rating  if zs else 0.5
        mkt   = zs.market_demand      if zs else 1.0
        cong  = zs.congestion_level   if zs else 0.0
        intensity = mkt * 0.5 + cong * 0.5
        radius    = BASE + mkt * 30 + cong * 28
        color     = TRADE_COLOR

    elif zone_name == "DEVELOPMENT":
        eff   = zs.efficiency_rating if zs else 0.5
        occ   = (zs.current_population / max(1, total)) if zs else 0.0
        energy_out = sum(n.factor_skills.get("ENERGY", 0.0)
                         for n in npcs if n.zone == zone_name and n.state == "AT_WORK")
        pop   = zs.current_population if zs else 0
        e_avg = energy_out / max(1, pop)
        intensity = eff * 0.5 + occ * 0.3 + e_avg * 0.2
        radius    = BASE + eff * 32 + occ * 30 + e_avg * 14
        color     = DEV_COLOR

    elif zone_name == "FLEX":
        pop      = zs.current_population if zs else 0
        stresses = [n.stress_endured for n in npcs
                    if n.zone == zone_name and n.state == "AT_WORK"]
        avg_s    = (sum(stresses) / len(stresses)) if stresses else 50.0
        relief   = max(0.0, 1.0 - avg_s / 100.0)
        occ      = min(1.0, pop / max(1, total * 0.25))
        intensity = relief * 0.65 + occ * 0.35
        radius    = BASE + relief * 42 + occ * 24
        color     = FLEX_COLOR

    elif zone_name == "HOME":
        home_pop = sum(1 for n in npcs if n.state == "AT_HOME")
        frac     = home_pop / total
        avg_e    = (sum(n.energy for n in npcs if n.state == "AT_HOME") /
                    max(1, home_pop))
        intensity = frac * 0.6 + (avg_e / 100.0) * 0.4
        radius    = BASE + frac * 55 + (avg_e / 100.0) * 20
        color     = WHITE

    elif zone_name == "CENTRAL":
        central_npcs = [n for n in npcs if n.zone == "CENTRAL" and n.state == "AT_WORK"]
        crowd        = len(central_npcs)
        cfg          = SHARED_ZONE_CONFIG["CENTRAL"]
        avg_sn       = (sum(getattr(n, 'social_need', 0.5) for n in central_npcs)
                        / max(1, crowd))
        satisfaction = max(0.0, 1.0 - avg_sn)
        crowd_norm   = min(1.0, crowd / cfg["crowd_sweet_spot"])
        intensity    = crowd_norm * 0.5 + satisfaction * 0.5
        radius       = BASE + crowd_norm * 46 + satisfaction * 28
        color        = _CENTRAL_COL

    elif zone_name == "PANTHEON":
        tier         = PANTHEON.tier
        mat_prog     = PANTHEON.material_progress
        contributors = sum(1 for n in npcs if n.zone == "PANTHEON" and n.state == "AT_WORK")
        tier_norm    = min(1.0, tier / max(1, len(PANTHEON_TIERS)))
        contrib_norm = min(1.0, contributors / 6.0)
        intensity    = tier_norm * 0.5 + mat_prog * 0.3 + contrib_norm * 0.2
        radius       = BASE + tier * 12 + mat_prog * 22 + contrib_norm * 16
        color        = _PANTHEON_COL

    else:
        intensity = 0.3
        radius    = BASE
        color     = WHITE

    return float(radius), float(max(0.0, min(1.0, intensity))), color


def _build_anchor_cell_cache(env_state: EnvironmentState):
    if env_state._anchor_cache_valid:
        return

    cols = EMX_CELL_GRID_COLS
    rows = EMX_CELL_GRID_ROWS
    cell_count = cols * rows
    cell_w = WIDTH / cols
    cell_h = HEIGHT / rows

    _anchors_now = ANCHORS if ANCHORS else anchors_to_pixels(ANCHORS_NORM, WIDTH, HEIGHT)

    env_state._anchor_field_cache = []
    for idx in range(cell_count):
        r = idx // cols
        c = idx % cols
        cx = c * cell_w + cell_w * 0.5
        cy = r * cell_h + cell_h * 0.5

        cell_anchors = []
        for zone_name, (ax, ay) in _anchors_now.items():
            sig = ANCHOR_EMX_SIGNATURE.get(zone_name)
            if not sig:
                continue
            dist = math.sqrt((cx - ax) ** 2 + (cy - ay) ** 2)
            cell_anchors.append((zone_name, dist, sig))
        env_state._anchor_field_cache.append(cell_anchors)

    env_state._central_cell_idxs = set()
    if "CENTRAL" in _anchors_now:
        _cax, _cay = _anchors_now["CENTRAL"]
        _central_r = min(WIDTH, HEIGHT) * 0.15
        for idx in range(cell_count):
            r = idx // cols
            c = idx % cols
            cx = c * cell_w + cell_w * 0.5
            cy = r * cell_h + cell_h * 0.5
            if math.sqrt((cx - _cax) ** 2 + (cy - _cay) ** 2) < _central_r:
                env_state._central_cell_idxs.add(idx)

    env_state._anchor_cache_valid = True


def update_cell_atmosphere(world, env_state: EnvironmentState, tick: int = 0):
    if not EMX_CELL_GRID_ENABLED:
        return

    ensure_cell_grid_state(env_state)
    cols = EMX_CELL_GRID_COLS
    rows = EMX_CELL_GRID_ROWS
    cell_count = cols * rows

    _build_anchor_cell_cache(env_state)

    # Reset pre-allocated buffers in-place:
    num_emotions = len(EMX_EMOTIONS)
    for idx in range(cell_count):
        env_state._cell_populations[idx] = 0
        for e_idx in range(num_emotions):
            env_state._cell_sums[idx][e_idx] = 0.0
            env_state._cell_new[idx][e_idx] = 0.0
            env_state._cell_old[idx][e_idx] = 0.0
            env_state._anchor_field_sums[idx][e_idx] = 0.0

    # Populating cell_old buffer from env_state.cell_atmosphere:
    for idx in range(cell_count):
        cell_dict = env_state.cell_atmosphere[idx]
        for e_idx, emotion in enumerate(EMX_EMOTIONS):
            env_state._cell_old[idx][e_idx] = cell_dict.get(emotion, 0.0)

    # INTERLEAVED: Only 1/5 NPCs contribute to cell atmosphere per tick
    for npc in world.npcs:
        if npc.state == "AT_HOME":
            continue
        if (tick + npc.id) % INTERLEAVE_WINDOW == 0:
            idx = cell_index_for_position(npc.x, npc.y, cols, rows)
            env_state._cell_populations[idx] += 1
            for e_idx, emotion in enumerate(EMX_EMOTIONS):
                env_state._cell_sums[idx][e_idx] += float(npc.emx.get(emotion, 0.0))

    # Pre-calculate active anchor fields once per tick
    _anchors_now = ANCHORS if ANCHORS else anchors_to_pixels(ANCHORS_NORM, WIDTH, HEIGHT)
    env_state._cached_anchor_fields.clear()
    for zone_name in _anchors_now:
        if zone_name in ANCHOR_EMX_SIGNATURE:
            radius, intensity, _ = compute_anchor_field(zone_name, world, world.zone_stats, 0)
            env_state._cached_anchor_fields[zone_name] = (radius, intensity)

    # Accumulate anchor fields using cached distances and signatures:
    for idx in range(cell_count):
        for zone_name, dist, sig in env_state._anchor_field_cache[idx]:
            if zone_name not in env_state._cached_anchor_fields:
                continue
            radius, intensity = env_state._cached_anchor_fields[zone_name]
            if intensity < 0.03 or radius < 4:
                continue
            if dist >= radius:
                continue
            weight = (1.0 - dist / radius) * intensity * ANCHOR_FIELD_EMIT_RATE
            for emotion, w in sig:
                e_idx = EMOTION_TO_IDX[emotion]
                env_state._anchor_field_sums[idx][e_idx] += weight * w

    # Process grid cells:
    for row in range(rows):
        for col in range(cols):
            idx = row * cols + col
            pop = env_state._cell_populations[idx] * INTERLEAVE_WINDOW
            occupancy = min(1.0, pop / EMX_CELL_OCCUPANCY_REF)
            decay = EMX_CELL_BASE_DECAY + EMX_CELL_EMPTY_EXTRA_DECAY * (1.0 - occupancy)

            neighbors = []
            if col > 0:
                neighbors.append(idx - 1)
            if col < cols - 1:
                neighbors.append(idx + 1)
            if row > 0:
                neighbors.append(idx - cols)
            if row < rows - 1:
                neighbors.append(idx + cols)

            for e_idx in range(num_emotions):
                local_avg = (env_state._cell_sums[idx][e_idx] / env_state._cell_populations[idx]) if env_state._cell_populations[idx] > 0 else 0.0
                neighbor_avg = 0.0
                if neighbors:
                    neighbor_avg = sum(env_state._cell_old[n][e_idx] for n in neighbors) / len(neighbors)

                if idx in env_state._central_cell_idxs:
                    value = env_state._cell_old[idx][e_idx]
                    value += env_state._anchor_field_sums[idx][e_idx]
                    if pop > 0:
                        value += EMX_CELL_EMIT_RATE * occupancy * local_avg
                        value += EMX_CELL_NEIGHBOR_MIX * neighbor_avg
                else:
                    value = env_state._cell_old[idx][e_idx] * (1.0 - decay)
                    value += EMX_CELL_EMIT_RATE * occupancy * local_avg
                    value += EMX_CELL_NEIGHBOR_MIX * neighbor_avg
                    value += env_state._anchor_field_sums[idx][e_idx]

                value *= (1.0 - EMX_CELL_ABSORB_RATE)
                env_state._cell_new[idx][e_idx] = max(0.0, min(1.0, value))

    # Cancel opposite pairs:
    for idx in range(cell_count):
        ratio = EMX_CELL_CANCEL_RATIO * 0.1 if idx in env_state._central_cell_idxs else EMX_CELL_CANCEL_RATIO
        for pos, neg in EMX_OPPOSITE_PAIRS:
            pos_idx = EMOTION_TO_IDX[pos]
            neg_idx = EMOTION_TO_IDX[neg]
            cancel = min(env_state._cell_new[idx][pos_idx], env_state._cell_new[idx][neg_idx]) * ratio
            env_state._cell_new[idx][pos_idx] = max(0.0, env_state._cell_new[idx][pos_idx] - cancel)
            env_state._cell_new[idx][neg_idx] = max(0.0, env_state._cell_new[idx][neg_idx] - cancel)

    # CENTRAL snapshot load:
    _SNAPSHOT_RATE = 1.0
    for npc in world.npcs:
        if npc.state == "AT_HOME":
            continue
        if (tick + npc.id) % INTERLEAVE_WINDOW == 0:
            idx = cell_index_for_position(npc.x, npc.y, cols, rows)
            if idx not in env_state._central_cell_idxs:
                continue
            for e_idx, e in enumerate(EMX_EMOTIONS):
                npc.emx[e] = max(
                    0.0,
                    min(
                        1.0,
                        npc.emx[e]
                        + env_state._cell_old[idx][e_idx]
                        * _SNAPSHOT_RATE
                        * INTERLEAVE_WINDOW,
                    ),
                )

    # Copy new cells back to cell_atmosphere and cell_population in-place:
    for idx in range(cell_count):
        cell_dict = env_state.cell_atmosphere[idx]
        env_state.cell_population[idx] = env_state._cell_populations[idx] * INTERLEAVE_WINDOW
        for e_idx, emotion in enumerate(EMX_EMOTIONS):
            cell_dict[emotion] = env_state._cell_new[idx][e_idx]


_SOLITARY_ZONES = {"HOME", "SCIENCE", "DEVELOPMENT"}

def update_social_need(world):
    """Tick-level social_need drift: grows in solitary zones, decays at CENTRAL."""
    for npc in world.npcs:
        if npc.state == "AT_WORK":
            if npc.zone in _SOLITARY_ZONES:
                npc.social_need = min(1.0, npc.social_need + 0.002)
            elif npc.zone == "CENTRAL":
                pass
        elif npc.state == "AT_HOME":
            npc.social_need = max(0.0, npc.social_need - 0.001)
