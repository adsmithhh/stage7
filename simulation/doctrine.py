from __future__ import annotations

import random
from typing import Dict

from config.constants import (
    DOCTRINE_DECAY,
    DOCTRINE_DEEPEN,
    DOCTRINE_PERSONALITY_VOTE,
    DOCTRINE_PERSONALITY_VULN,
    DOCTRINE_RECOVERY_TICKS,
    DOCTRINE_SHORT,
    DOCTRINE_THRESHOLD,
    DOCTRINE_TYPES,
    DOCTRINE_VOTE_RATE,
)


def _apply_doctrine_effects(npc):
    """Tick-level stat effects from an active doctrine.
    Rates are tiny — this fires every sim tick (90fps), not once per second."""
    doc = npc.doctrine
    s   = npc.doctrine_strength
    if doc == "MERITOCRATIC":
        if npc.money < 30:
            npc.stress_endured = min(100.0, npc.stress_endured + 0.012 * s)
    elif doc == "TRANSCENDENT":
        # Mild stress relief — positive doctrine
        npc.stress_endured = max(0.0, npc.stress_endured - 0.008 * s)
    elif doc == "CONSPIRATORIAL":
        npc.stress_endured = min(100.0, npc.stress_endured + 0.006 * s)
    elif doc == "REVOLUTIONARY":
        npc.stress_endured = min(100.0, npc.stress_endured + 0.004 * s)
    elif doc == "LIBERTARIAN_CULT":
        if npc.money <= 150:
            npc.stress_endured = min(100.0, npc.stress_endured + 0.005 * s)


def _check_doctrine_escape(npc) -> bool:
    """True when NPC should begin de-radicalization."""
    doc = npc.doctrine
    if doc == "MERITOCRATIC":
        return npc.stress_endured > 75 and npc.money < 20
    elif doc == "TRANSCENDENT":
        return npc.is_collapsing and npc.apathy > 0.3
    elif doc == "CONSPIRATORIAL":
        return npc.stress_endured < 25 and random.random() < 0.005
    elif doc == "REVOLUTIONARY":
        return npc.stress_endured < 40 and npc.money > 100
    elif doc == "LIBERTARIAN_CULT":
        return npc.stress_endured > 70 or npc.exhaustion > 0.3
    return False


def update_central_doctrine(world, tick, sim_state):
    """Accumulate doctrine pressure from CENTRAL visitors; expose and influence NPCs."""
    doctrine = sim_state.doctrine
    central_npcs = [n for n in world.npcs
                    if n.state == "AT_WORK" and n.zone == "CENTRAL"]

    # ── Vote phase: each visitor emits doctrine pressure ─────────────────────
    votes: Dict[str, int] = {d: 0 for d in DOCTRINE_TYPES}
    for npc in central_npcs:
        pname    = getattr(npc, 'personality_name', 'Anchor')
        vote_doc = DOCTRINE_PERSONALITY_VOTE.get(pname, "TRANSCENDENT")
        # Stress and cynicism shift the vote
        if npc.stress_endured > 65:
            vote_doc = "CONSPIRATORIAL"
        elif npc.cynicism > 0.4:
            vote_doc = "LIBERTARIAN_CULT"
        votes[vote_doc] += 1

    for d in DOCTRINE_TYPES:
        if votes[d] > 0:
            doctrine.pressure[d] = min(DOCTRINE_THRESHOLD * 1.5,
                                       doctrine.pressure[d] + votes[d] * DOCTRINE_VOTE_RATE)
        else:
            doctrine.pressure[d] = max(0.0, doctrine.pressure[d] - DOCTRINE_DECAY)

    # ── Breaking point: activate if any doctrine crosses threshold ────────────
    if doctrine.active is None:
        for d in DOCTRINE_TYPES:
            if doctrine.pressure[d] >= DOCTRINE_THRESHOLD:
                doctrine.active = d
                doctrine.active_since_tick = tick
                doctrine.history.append((tick, d, "ACTIVATED"))
                print(f"🔱 DOCTRINE ACTIVATED: {DOCTRINE_SHORT[d]} at tick {tick}")
                break
    else:
        # Collapse active doctrine if pressure drops too low
        if doctrine.pressure[doctrine.active] < DOCTRINE_THRESHOLD * 0.35:
            doctrine.history.append((tick, doctrine.active, "COLLAPSED"))
            print(f"💀 DOCTRINE COLLAPSED: {DOCTRINE_SHORT[doctrine.active]} at tick {tick}")
            doctrine.active = None

    # ── Exposure: visiting NPCs tested against active doctrine ────────────────
    if doctrine.active:
        for npc in central_npcs:
            if npc.deradicalization_timer > 0:
                continue
            if npc.doctrine == doctrine.active:
                # Already on it — deepen
                npc.doctrine_strength = min(1.0, npc.doctrine_strength + DOCTRINE_DEEPEN)
                continue
            if npc.doctrine is not None:
                continue  # already holds a different doctrine
            pname       = getattr(npc, 'personality_name', 'Anchor')
            base_vuln   = DOCTRINE_PERSONALITY_VULN.get(pname, {}).get(doctrine.active, 0.5)
            stress_f    = min(1.0, npc.stress_endured / 80.0)
            degen_load  = (npc.apathy + npc.exhaustion + npc.dissociation
                           + npc.numbness + npc.cynicism)
            coh_f       = min(1.0, degen_load / 2.0) * 0.5
            suscept     = base_vuln * 0.6 + stress_f * 0.2 + coh_f * 0.2
            if suscept > random.random():
                npc.doctrine = doctrine.active
                npc.doctrine_strength = 0.1
                doctrine.indoctrinated[doctrine.active] = (
                    doctrine.indoctrinated.get(doctrine.active, 0) + 1
                )

    # ── Apply effects + escape check for all indoctrinated NPCs ──────────────
    for npc in world.npcs:
        if npc.deradicalization_timer > 0:
            npc.deradicalization_timer -= 1
            if npc.deradicalization_timer == 0:
                npc.doctrine = None
                npc.doctrine_strength = 0.0
            continue
        if npc.doctrine is None:
            continue
        npc.doctrine_strength = min(1.0, npc.doctrine_strength + DOCTRINE_DEEPEN * 0.3)
        _apply_doctrine_effects(npc)
        if _check_doctrine_escape(npc):
            npc.deradicalization_timer = DOCTRINE_RECOVERY_TICKS.get(npc.doctrine, 200)
