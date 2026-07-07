from __future__ import annotations

import pygame

from .backend_base import RendererBackend
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

        display_surface.fill((0, 0, 0))
        display_surface.blit(composed, (0, 0))
        pygame.display.flip()
