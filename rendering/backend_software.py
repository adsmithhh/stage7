from __future__ import annotations

import pygame

from .backend_base import RendererBackend
from . import rendering as projection_runtime
from .render_types import RenderBackendStatus


class SoftwareRendererBackend(RendererBackend):
    def __init__(self, status: RenderBackendStatus) -> None:
        super().__init__(status)

    def create_display(self, size: tuple[int, int]) -> pygame.Surface:
        pygame.display.set_mode(size, pygame.RESIZABLE)
        return pygame.Surface(size).convert_alpha()

    def present(self, frame_surface: pygame.Surface, packet=None, overlay_surface: pygame.Surface | None = None) -> None:
        display_surface = pygame.display.get_surface()
        if display_surface is None:
            raise RuntimeError("Software display surface is unavailable")

        composed = frame_surface.copy()
        if overlay_surface is not None:
            composed.blit(overlay_surface, (0, 0))

        scroll_x = 0
        scroll_y = 0
        if packet is not None and getattr(packet, "ui_state", None) is not None:
            scroll_x = max(0, int(getattr(packet.ui_state, "render_scroll_x", 0)))
            scroll_y = max(0, int(getattr(packet.ui_state, "render_scroll_y", 0)))

        display_rect = display_surface.get_rect()
        max_scroll_x = max(0, composed.get_width() - display_rect.width)
        max_scroll_y = max(0, composed.get_height() - display_rect.height)
        scroll_x = min(scroll_x, max_scroll_x)
        scroll_y = min(scroll_y, max_scroll_y)
        source_rect = pygame.Rect(
            scroll_x,
            scroll_y,
            min(display_rect.width, max(0, composed.get_width() - scroll_x)),
            min(display_rect.height, max(0, composed.get_height() - scroll_y)),
        )

        display_surface.fill((0, 0, 0))
        if source_rect.width > 0 and source_rect.height > 0:
            display_surface.blit(composed, (0, 0), area=source_rect)
        if packet is not None and getattr(packet, "ui_state", None) is not None:
            metrics = projection_runtime.build_horizontal_scrollbar_metrics(
                logical_size=composed.get_size(),
                display_size=display_rect.size,
                scroll_x=scroll_x,
            )
            if metrics is not None:
                track_rect = metrics["track_rect"]
                thumb_rect = metrics["thumb_rect"]
                pygame.draw.rect(display_surface, (16, 20, 30), track_rect, border_radius=8)
                pygame.draw.rect(display_surface, (90, 104, 128), track_rect, width=1, border_radius=8)
                thumb_fill = (220, 228, 240) if packet.ui_state.horizontal_scroll_dragging else (184, 194, 214)
                pygame.draw.rect(display_surface, thumb_fill, thumb_rect, border_radius=7)
                pygame.draw.rect(display_surface, (70, 84, 106), thumb_rect, width=1, border_radius=7)
        pygame.display.flip()
