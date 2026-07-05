from __future__ import annotations

from abc import ABC, abstractmethod

import pygame

from .render_types import RenderBackendStatus


class RendererBackend(ABC):
    def __init__(self, status: RenderBackendStatus) -> None:
        self._status = status

    @property
    def status(self) -> RenderBackendStatus:
        return self._status

    @abstractmethod
    def create_display(self, size: tuple[int, int]) -> pygame.Surface:
        raise NotImplementedError

    def present(self, frame_surface: pygame.Surface, packet=None, overlay_surface: pygame.Surface | None = None) -> None:
        pygame.display.flip()

    def shutdown(self) -> None:
        return None
