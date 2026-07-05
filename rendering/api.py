from .backend_base import RendererBackend
from .backend_opengl import create_opengl_renderer_backend, probe_moderngl
from .backend_software import SoftwareRendererBackend
from .render_types import RenderBackendStatus
from . import render_central2 as field_runtime
from .rendering import (
    build_horizontal_scrollbar_metrics,
    build_frame_render_packet,
    horizontal_scroll_from_thumb_left,
    render_view,
    resolve_edge_tab_action,
    save_start_npc_state_once,
    save_npc_state,
)
from . import rendering as projection_runtime


def configure_runtime(*, anchors: dict | None = None) -> None:
    projection_runtime.configure_runtime(anchors=anchors)
    field_runtime.configure_runtime(anchors=anchors)


def reset_runtime_state() -> None:
    field_runtime.reset_runtime_state()
    projection_runtime.reset_runtime_state()


def create_renderer_backend(requested_backend: str) -> tuple[RendererBackend, RenderBackendStatus]:
    normalized = (requested_backend or "software").strip().lower()
    if normalized not in {"software", "auto", "opengl"}:
        normalized = "software"

    if normalized == "software":
        status = RenderBackendStatus(
            requested=normalized,
            active="software",
            moderngl_available=False,
            moderngl_version=None,
            reason="software renderer selected",
        )
        return SoftwareRendererBackend(status), status

    status = probe_moderngl(normalized)
    if status.active == "opengl":
        return create_opengl_renderer_backend(status), status
    return SoftwareRendererBackend(status), status


def create_software_renderer_backend(reason: str, requested_backend: str = "software") -> tuple[RendererBackend, RenderBackendStatus]:
    probe_status = probe_moderngl("auto")
    status = RenderBackendStatus(
        requested=requested_backend,
        active="software",
        moderngl_available=probe_status.moderngl_available,
        moderngl_version=probe_status.moderngl_version,
        reason=reason,
    )
    return SoftwareRendererBackend(status), status


update_field_logic = field_runtime.update_field_logic
draw_zone_detail = field_runtime.draw_zone_detail
