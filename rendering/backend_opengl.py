from __future__ import annotations

from array import array
from importlib import metadata

import pygame

from .backend_base import RendererBackend
from .render_types import CirclePrimitive, RectPrimitive, RenderBackendStatus

try:
    import moderngl
except ModuleNotFoundError:
    moderngl = None


class OpenGLRendererBackend(RendererBackend):
    def __init__(self, status: RenderBackendStatus) -> None:
        super().__init__(status)
        self._ctx = None
        self._texture_program = None
        self._circle_program = None
        self._rect_program = None
        self._texture_quad = None
        self._texture_quad_buffer = None
        self._circle_quad_buffer = None
        self._texture = None
        self._logical_size: tuple[int, int] | None = None

    def create_display(self, size: tuple[int, int]) -> pygame.Surface:
        if moderngl is None:
            raise RuntimeError("moderngl is not installed")

        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(
            pygame.GL_CONTEXT_PROFILE_MASK,
            pygame.GL_CONTEXT_PROFILE_CORE,
        )
        pygame.display.set_mode(size, pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE)

        self._ctx = moderngl.create_context()
        self._ctx.enable(moderngl.BLEND)
        self._texture_program = self._ctx.program(
            vertex_shader="""
                #version 330
                in vec2 in_pos;
                in vec2 in_uv;
                out vec2 v_uv;
                void main() {
                    v_uv = in_uv;
                    gl_Position = vec4(in_pos, 0.0, 1.0);
                }
            """,
            fragment_shader="""
                #version 330
                uniform sampler2D frame_tex;
                in vec2 v_uv;
                out vec4 f_color;
                void main() {
                    f_color = texture(frame_tex, v_uv);
                }
            """,
        )
        self._texture_quad_buffer = self._ctx.buffer(
            array(
                "f",
                [
                    -1.0, -1.0, 0.0, 0.0,
                    1.0, -1.0, 1.0, 0.0,
                    -1.0, 1.0, 0.0, 1.0,
                    1.0, 1.0, 1.0, 1.0,
                ],
            ).tobytes()
        )
        self._texture_quad = self._ctx.vertex_array(
            self._texture_program,
            [(self._texture_quad_buffer, "2f 2f", "in_pos", "in_uv")],
        )
        self._circle_program = self._ctx.program(
            vertex_shader="""
                #version 330
                uniform vec2 logical_size;
                in vec2 in_unit;
                in vec2 in_center;
                in float in_radius;
                in vec4 in_color;
                in float in_inner_ratio;
                out vec2 v_unit;
                out vec4 v_color;
                out float v_inner_ratio;
                void main() {
                    vec2 pixel_pos = in_center + (in_unit * in_radius);
                    vec2 clip_pos = vec2(
                        (pixel_pos.x / logical_size.x) * 2.0 - 1.0,
                        1.0 - (pixel_pos.y / logical_size.y) * 2.0
                    );
                    gl_Position = vec4(clip_pos, 0.0, 1.0);
                    v_unit = in_unit;
                    v_color = in_color;
                    v_inner_ratio = in_inner_ratio;
                }
            """,
            fragment_shader="""
                #version 330
                in vec2 v_unit;
                in vec4 v_color;
                in float v_inner_ratio;
                out vec4 f_color;
                void main() {
                    float dist = length(v_unit);
                    if (dist > 1.0) {
                        discard;
                    }
                    if (v_inner_ratio > 0.0 && dist < v_inner_ratio) {
                        discard;
                    }
                    float outer_alpha = smoothstep(1.0, 0.92, dist);
                    float inner_alpha = 1.0;
                    if (v_inner_ratio > 0.0) {
                        inner_alpha = smoothstep(v_inner_ratio, min(1.0, v_inner_ratio + 0.05), dist);
                    }
                    float alpha = v_color.a * outer_alpha * inner_alpha;
                    if (alpha <= 0.001) {
                        discard;
                    }
                    f_color = vec4(v_color.rgb, alpha);
                }
            """,
        )
        self._rect_program = self._ctx.program(
            vertex_shader="""
                #version 330
                uniform vec2 logical_size;
                in vec2 in_unit;
                in vec2 in_rect_pos;
                in vec2 in_rect_size;
                in vec4 in_color;
                out vec4 v_color;
                void main() {
                    vec2 normalized = (in_unit + vec2(1.0, 1.0)) * 0.5;
                    vec2 pixel_pos = in_rect_pos + (normalized * in_rect_size);
                    vec2 clip_pos = vec2(
                        (pixel_pos.x / logical_size.x) * 2.0 - 1.0,
                        1.0 - (pixel_pos.y / logical_size.y) * 2.0
                    );
                    gl_Position = vec4(clip_pos, 0.0, 1.0);
                    v_color = in_color;
                }
            """,
            fragment_shader="""
                #version 330
                in vec4 v_color;
                out vec4 f_color;
                void main() {
                    f_color = v_color;
                }
            """,
        )
        self._circle_quad_buffer = self._ctx.buffer(
            array(
                "f",
                [
                    -1.0, -1.0,
                    1.0, -1.0,
                    -1.0, 1.0,
                    1.0, 1.0,
                ],
            ).tobytes()
        )
        self._logical_size = size
        self._texture = self._ctx.texture(size, 4)
        self._texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self._texture_program["frame_tex"] = 0

        return pygame.Surface(size).convert_alpha()

    def present(self, frame_surface: pygame.Surface, packet=None, overlay_surface: pygame.Surface | None = None) -> None:
        if self._ctx is None or self._texture_program is None or self._texture_quad is None:
            raise RuntimeError("OpenGL renderer backend not initialized")

        size = frame_surface.get_size()
        if self._logical_size != size:
            self._recreate_texture(size)

        assert self._texture is not None
        frame_bytes = pygame.image.tobytes(frame_surface, "RGBA", True)
        self._texture.write(frame_bytes)
        self._texture.use(0)

        display_surface = pygame.display.get_surface()
        if display_surface is None:
            raise RuntimeError("OpenGL display surface is unavailable")

        self._ctx.viewport = (0, 0, *display_surface.get_size())
        self._ctx.clear(0.0, 0.0, 0.0, 1.0)
        self._texture_quad.render(moderngl.TRIANGLE_STRIP)
        if packet is not None and getattr(packet, "gpu_rect_primitives", None):
            self._draw_gpu_rects(packet.gpu_rect_primitives, size)
        if packet is not None and getattr(packet, "gpu_primitives_enabled", False):
            self._draw_gpu_circles(packet.gpu_circle_primitives, size)
        if overlay_surface is not None:
            overlay_bytes = pygame.image.tobytes(overlay_surface, "RGBA", True)
            self._texture.write(overlay_bytes)
            self._texture.use(0)
            self._texture_quad.render(moderngl.TRIANGLE_STRIP)
        pygame.display.flip()

    def shutdown(self) -> None:
        for resource in (
            self._texture,
            self._texture_quad,
            self._texture_quad_buffer,
            self._circle_quad_buffer,
            self._texture_program,
            self._circle_program,
            self._rect_program,
            self._ctx,
        ):
            if resource is not None:
                resource.release()
        self._texture = None
        self._texture_quad = None
        self._texture_quad_buffer = None
        self._circle_quad_buffer = None
        self._texture_program = None
        self._circle_program = None
        self._rect_program = None
        self._ctx = None
        self._logical_size = None

    def _recreate_texture(self, size: tuple[int, int]) -> None:
        if self._ctx is None:
            raise RuntimeError("OpenGL renderer backend not initialized")
        if self._texture is not None:
            self._texture.release()
        self._texture = self._ctx.texture(size, 4)
        self._texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self._logical_size = size

    def _draw_gpu_circles(self, primitives: list[CirclePrimitive], logical_size: tuple[int, int]) -> None:
        if (
            self._ctx is None
            or self._circle_program is None
            or self._circle_quad_buffer is None
            or not primitives
        ):
            return

        self._circle_program["logical_size"] = logical_size
        instance_data = array("f")
        for primitive in primitives:
            radius = max(1.0, primitive.radius)
            inner_ratio = 0.0 if primitive.inner_radius <= 0 else min(0.98, primitive.inner_radius / radius)
            instance_data.extend(
                [
                    primitive.center_x,
                    primitive.center_y,
                    radius,
                    primitive.color[0] / 255.0,
                    primitive.color[1] / 255.0,
                    primitive.color[2] / 255.0,
                    primitive.color[3] / 255.0,
                    inner_ratio,
                ]
            )

        instance_buffer = self._ctx.buffer(instance_data.tobytes())
        vao = self._ctx.vertex_array(
            self._circle_program,
            [
                (self._circle_quad_buffer, "2f", "in_unit"),
                (instance_buffer, "2f 1f 4f 1f /i", "in_center", "in_radius", "in_color", "in_inner_ratio"),
            ],
        )
        try:
            vao.render(moderngl.TRIANGLE_STRIP, instances=len(primitives))
        finally:
            vao.release()
            instance_buffer.release()

    def _draw_gpu_rects(self, primitives: list[RectPrimitive], logical_size: tuple[int, int]) -> None:
        if (
            self._ctx is None
            or self._rect_program is None
            or self._circle_quad_buffer is None
            or not primitives
        ):
            return

        self._rect_program["logical_size"] = logical_size
        instance_data = array("f")
        for primitive in primitives:
            instance_data.extend(
                [
                    primitive.x,
                    primitive.y,
                    primitive.width,
                    primitive.height,
                    primitive.color[0] / 255.0,
                    primitive.color[1] / 255.0,
                    primitive.color[2] / 255.0,
                    primitive.color[3] / 255.0,
                ]
            )

        instance_buffer = self._ctx.buffer(instance_data.tobytes())
        vao = self._ctx.vertex_array(
            self._rect_program,
            [
                (self._circle_quad_buffer, "2f", "in_unit"),
                (instance_buffer, "2f 2f 4f /i", "in_rect_pos", "in_rect_size", "in_color"),
            ],
        )
        try:
            vao.render(moderngl.TRIANGLE_STRIP, instances=len(primitives))
        finally:
            vao.release()
            instance_buffer.release()


def probe_moderngl(requested_backend: str) -> RenderBackendStatus:
    try:
        version = metadata.version("moderngl")
    except metadata.PackageNotFoundError:
        return RenderBackendStatus(
            requested=requested_backend,
            active="software",
            moderngl_available=False,
            moderngl_version=None,
            reason="moderngl unavailable; software renderer kept active",
        )

    active = "opengl" if requested_backend in {"auto", "opengl"} else "software"
    if active == "opengl":
        reason = "moderngl detected; OpenGL presentation path enabled"
    else:
        reason = "moderngl detected; software renderer selected"

    return RenderBackendStatus(
        requested=requested_backend,
        active=active,
        moderngl_available=True,
        moderngl_version=version,
        reason=reason,
    )


def create_opengl_renderer_backend(status: RenderBackendStatus) -> OpenGLRendererBackend:
    return OpenGLRendererBackend(status)
