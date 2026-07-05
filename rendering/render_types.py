from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class RenderState:
    """
    Explicit container for rendering-related persistent state and caches.
    Hides internal buffers used for complex field math and visualization.
    """
    # Persistent composite state, keyed by (world_id, zone_name)
    composite_cell_cache: Dict[tuple, List[Dict[str, float]]] = field(default_factory=dict)

    # Active saturation anchors: (world_id, zone_name) -> list of {idx, birth_tick}
    saturation_anchors: Dict[tuple, List[Dict[str, int]]] = field(default_factory=dict)

    # Field results cache for rendering, keyed by (world_id, zone_name)
    field_render_cache: Dict[tuple, object] = field(default_factory=dict)

    def clear(self):
        """Reset all rendering caches."""
        self.composite_cell_cache.clear()
        self.saturation_anchors.clear()
        self.field_render_cache.clear()


@dataclass(frozen=True)
class RenderBackendStatus:
    requested: str
    active: str
    moderngl_available: bool
    reason: str
    moderngl_version: str | None = None

    def summary_line(self) -> str:
        version = self.moderngl_version or "not-installed"
        return (
            f"Renderer backend: requested={self.requested} active={self.active} "
            f"moderngl={version} ({self.reason})"
        )


@dataclass(frozen=True)
class FrameRenderPacket:
    world: Any
    worlds: list[Any]
    ui_state: Any
    fonts: dict[str, Any]
    tick: int
    anchors: dict[str, Any]
    world_label: str
    current_world_index: int
    world_count: int
    paused: bool
    status_message: str | None
    active_backend: str
    gpu_primitives_enabled: bool
    gpu_circle_primitives: list["CirclePrimitive"]
    gpu_rect_primitives: list["RectPrimitive"]


@dataclass(frozen=True)
class CirclePrimitive:
    center_x: float
    center_y: float
    radius: float
    color: tuple[int, int, int, int]
    inner_radius: float = 0.0


@dataclass(frozen=True)
class RectPrimitive:
    x: float
    y: float
    width: float
    height: float
    color: tuple[int, int, int, int]
