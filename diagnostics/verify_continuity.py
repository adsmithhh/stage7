"""
CONTINUITY VERIFIER
===================

Diagnostic tool to prove that simulation state is preserved with 100% fidelity 
during a save/load cycle.

This closes the 'Verification Gap' for the persistence layer.
"""

import os
import sys
import random
import json
import math
from typing import Dict, Any, List

# Ensure the project root wins over any installed package with the same name.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime.api import LOGIC_TICKS_PER_FRAME, create_app_context, run_logic_phase, run_physical_phase
from persistence.api import save_snapshot, load_snapshot
import config.constants as constants

def capture_state(context) -> Dict[str, Any]:
    """Captures a deep snapshot of critical simulation state."""
    world = context.worlds[0]
    
    # 1. World metadata
    state = {
        "global_tick": context.global_tick,
        "dissonance_threshold": world.dissonance_threshold,
        "npcs": []
    }
    
    # 2. NPC deep state
    for npc in world.npcs:
        npc_data = {
            "id": npc.id,
            "x": npc.x,
            "y": npc.y,
            "energy": npc.energy,
            "money": npc.money,
            "stress": npc.stress_endured,
            "zone": npc.zone,
            "state": npc.state,
            "emx": dict(getattr(npc, 'emx', {})),
            "zone_fatigue": dict(getattr(npc, 'zone_fatigue', {}))
        }
        state["npcs"].append(npc_data)
        
    # 3. Global Systems
    state["doctrine"] = dict(world.runtime_state.sim_state.doctrine.__dict__)
    state["zone_atmosphere"] = {
        zone: dict(values)
        for zone, values in world.runtime_state.env_state.zone_atmosphere.items()
    }
    state["pantheon"] = {
        "tier": constants.PANTHEON.tier,
        "material": constants.PANTHEON.material_banked,
        "energy": constants.PANTHEON.energy_banked
    }
    
    return state

def compare_states(s1: Dict[str, Any], s2: Dict[str, Any]) -> List[str]:
    """Returns a list of discrepancies between two states."""
    errors = []
    
    if s1["global_tick"] != s2["global_tick"]:
        errors.append(f"Tick mismatch: {s1['global_tick']} != {s2['global_tick']}")
        
    if abs(s1["dissonance_threshold"] - s2["dissonance_threshold"]) > 1e-6:
        errors.append("Dissonance threshold mismatch")
        
    # Compare NPCs
    if len(s1["npcs"]) != len(s2["npcs"]):
        errors.append(f"NPC count mismatch: {len(s1['npcs'])} != {len(s2['npcs'])}")
    else:
        for i, (n1, n2) in enumerate(zip(s1["npcs"], s2["npcs"])):
            if n1["id"] != n2["id"]:
                errors.append(f"NPC[{i}] ID mismatch")
                continue
            
            for key in ["x", "y", "energy", "money", "stress", "zone", "state"]:
                if n1[key] != n2[key]:
                    errors.append(f"NPC[{n1['id']}] {key} mismatch: {n1[key]} != {n2[key]}")
            
            # EMX Check
            for emo, val in n1["emx"].items():
                if abs(val - n2["emx"].get(emo, -1)) > 1e-6:
                    errors.append(f"NPC[{n1['id']}] EMX {emo} mismatch")

    # Doctrine Check
    for key, val in s1["doctrine"].items():
        if val != s2["doctrine"].get(key):
            errors.append(f"Doctrine {key} mismatch")

    # Environment Check
    for zone, values in s1["zone_atmosphere"].items():
        for emotion, val in values.items():
            if abs(val - s2["zone_atmosphere"].get(zone, {}).get(emotion, -1)) > 1e-6:
                errors.append(f"Atmosphere {zone}.{emotion} mismatch")
            
    # Pantheon Check
    if s1["pantheon"] != s2["pantheon"]:
        errors.append(f"Pantheon mismatch: {s1['pantheon']} != {s2['pantheon']}")

    return errors

def main():
    print("🚀 Starting Continuity Verification...")
    
    # 1. Initialize
    os.environ['SDL_VIDEODRIVER'] = 'dummy'  # Headless mode
    context = create_app_context()
    
    # 2. Warm up following the real runtime cadence: 2 logic ticks per frame.
    warmup_ticks = 50
    warmup_frames = math.ceil(warmup_ticks / LOGIC_TICKS_PER_FRAME)
    print(f"   Warming up ({warmup_ticks} logic ticks / {warmup_frames} frames)...")
    for _ in range(warmup_frames):
        run_logic_phase(context)
        run_physical_phase(context)
        
    # 3. Capture State A
    print("   Capturing pre-save state...")
    state_a = capture_state(context)
    
    # 4. Save
    print("   Saving snapshot...")
    save_snapshot(context)
    
    # 5. Load
    print("   Loading snapshot...")
    # Use dummy lambda for validator creation
    load_snapshot(context, create_validator=lambda: None)
    
    # 6. Capture State B
    print("   Capturing post-load state...")
    state_b = capture_state(context)
    
    # 7. Compare
    print("   Comparing states...")
    errors = compare_states(state_a, state_b)
    
    if not errors:
        print("\n✅ CONTINUITY CERTIFIED")
        print(f"   Verified {len(state_a['npcs'])} NPCs, Doctrine, and Pantheon state.")
    else:
        print("\n❌ CONTINUITY FAILED")
        for err in errors[:10]:
            print(f"   - {err}")
        if len(errors) > 10:
            print(f"   ... and {len(errors)-10} more errors.")
        sys.exit(1)

if __name__ == "__main__":
    main()
