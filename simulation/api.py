"""Stable package facade for simulation-owned types and runtime helpers."""

from .npc_types import (
    NPC,
    World,
    UIState,
    build_snapshot,
    NPCView,
    ZoneStats,
    ZoneStatsView,
    WorkBasin,
    GameEvent,
    WorldSnapshot,
    Commitment,
    CommitmentPlan,
    MovementResult,
    WorkResult,
    InfluenceDelta,
    CollapseFlag,
    TickBuffers,
    CentralDoctrineState,
    WorldRuntimeState,
    SimulationState,
    EnvironmentState,
    anchors_to_pixels,
)

_SIMULATION_EXPORTS = {
    "simtick",
    "configure_runtime",
    "spawn_npc",
    "finalize_arrival",
    "ZONE_MECHANICS",
}

_DOCTRINE_EXPORTS = {"update_central_doctrine"}

__all__ = [
    "NPC",
    "World",
    "UIState",
    "build_snapshot",
    "NPCView",
    "ZoneStats",
    "ZoneStatsView",
    "WorkBasin",
    "GameEvent",
    "WorldSnapshot",
    "Commitment",
    "CommitmentPlan",
    "MovementResult",
    "WorkResult",
    "InfluenceDelta",
    "CollapseFlag",
    "TickBuffers",
    "CentralDoctrineState",
    "WorldRuntimeState",
    "SimulationState",
    "EnvironmentState",
    "anchors_to_pixels",
    "simtick",
    "configure_runtime",
    "spawn_npc",
    "finalize_arrival",
    "ZONE_MECHANICS",
    "update_central_doctrine",
    "reset_npc_id_gen",
    "simulation_runtime",
    "doctrine_runtime",
    "npc_types_runtime",
]


def __getattr__(name: str):
    if name in _SIMULATION_EXPORTS or name == "simulation_runtime":
        from . import simulation as simulation_runtime

        if name == "simulation_runtime":
            return simulation_runtime
        return getattr(simulation_runtime, name)

    if name in _DOCTRINE_EXPORTS or name == "doctrine_runtime":
        from . import doctrine as doctrine_runtime

        if name == "doctrine_runtime":
            return doctrine_runtime
        return getattr(doctrine_runtime, name)

    if name == "npc_types_runtime":
        from . import npc_types as npc_types_runtime

        return npc_types_runtime

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def configure_runtime(*, anchors: dict | None = None) -> None:
    """Historical no-op wrapper for runtime configuration."""
    pass


def reset_npc_id_gen(start_at: int) -> None:
    from itertools import count

    from . import npc_types as npc_types_runtime

    npc_types_runtime.NPC_ID_GEN = count(start_at)
