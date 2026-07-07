from __future__ import annotations

import io
import math
import random
import sys
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

import pygame

import config.constants as constants
import emx.api as emx_system
import rendering.api as rendering
import persistence.api as save_load
import simulation.api as simulation_api
from rendering.backend_base import RendererBackend
from rendering.render_types import RenderBackendStatus
from simulation.api import SimulationState
import tuning.api as tuning
from diagnostics.baseline_validator import BaselineValidator

LOGIC_TICKS_PER_FRAME = 2
WINDOW_RESIZE_EVENTS = tuple(
    event_type
    for event_type in (
        getattr(pygame, "VIDEORESIZE", None),
        getattr(pygame, "WINDOWSIZECHANGED", None),
    )
    if event_type is not None
)


@dataclass
class AppContext:
    screen: pygame.Surface
    clock: pygame.time.Clock
    fonts: dict
    anchors: dict
    zone_bases: dict
    worlds: list[simulation_api.World]
    ui_state: simulation_api.UIState
    validator: BaselineValidator
    renderer_backend: RendererBackend
    render_backend_status: RenderBackendStatus
    global_tick: int = 0


def ensure_daily_summary_folder() -> Path:
    project_root = Path(__file__).resolve().parent.parent
    daily_summary_dir = project_root / "data" / "summaries" / datetime.now().strftime("%m.%d")
    daily_summary_dir.mkdir(parents=True, exist_ok=True)
    return daily_summary_dir


def configure_stdio() -> None:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
        write_through=True,
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer,
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
        write_through=True,
    )


def print_terminal_menu_hint() -> None:
    print("\x1b[92m[M] menu | [X] snapshot | [SPACE] pause | click edge tabs | wheel scroll | bottom/right scrollbars | [ESC] quit\x1b[0m")


def init_pygame(renderer_backend: RendererBackend):
    pygame.init()
    screen = renderer_backend.create_display((constants.WIDTH, constants.HEIGHT))
    pygame.display.set_caption("System Architect - Trust System v2.0")
    clock = pygame.time.Clock()
    return screen, clock


def build_fonts() -> dict:
    return {
        "header": pygame.font.Font(None, constants.config["fonts"]["header"]),
        "large": pygame.font.Font(None, constants.config["fonts"]["large"]),
        "small": pygame.font.Font(None, constants.config["fonts"]["small"]),
        "tiny": pygame.font.Font(None, constants.config["fonts"]["tiny"]),
    }


def build_layout():
    anchors = simulation_api.anchors_to_pixels(constants.ANCHORS_NORM, constants.WIDTH, constants.HEIGHT)
    zone_bases = simulation_api.anchors_to_pixels(constants.ZONE_BASES_NORM, constants.WIDTH, constants.HEIGHT)
    return anchors, zone_bases


def install_runtime_bindings(*, anchors: dict) -> None:
    rendering.configure_runtime(anchors=anchors)
    simulation_api.configure_runtime(anchors=anchors)
    emx_system.configure_runtime(anchors=anchors)


def initialize_zone_mechanics(sim_state: SimulationState) -> list[str]:
    messages = ["Initializing trust system...", ""]
    sim_state.zone_mechanics.clear()
    for zone_name, zone_cfg in constants.config["zones"].items():
        sim_state.zone_mechanics[zone_name] = {
            "name": zone_name,
            "color": constants.COLORS.get(zone_cfg.get("color", "WHITE"), constants.WHITE),
            "description": zone_cfg.get("description", ""),
        }
    messages.append(
        f"Loaded {len(sim_state.zone_mechanics)} zone mechanics: "
        f"{list(sim_state.zone_mechanics.keys())}"
    )
    messages.append("")
    return messages


def summarize_world(world: simulation_api.World, territory_key: str) -> list[str]:
    messages = [f"Territory: '{world.name or territory_key}'", f"  Total: {len(world.npcs)} NPCs"]
    zone_counts: dict[str, int] = {}
    for npc in world.npcs:
        zone_counts[npc.zone] = zone_counts.get(npc.zone, 0) + 1
    for zone_name in sorted(zone_counts):
        messages.append(f"    * {zone_name}: {zone_counts[zone_name]:2d} NPCs")
    messages.append("")
    return messages


def build_worlds(anchors: dict, zone_mechanics_template: dict) -> tuple[list[simulation_api.World], list[str]]:
    worlds: list[simulation_api.World] = []
    startup_messages: list[str] = []
    home_x, home_y = anchors["HOME"]

    for territory_key, territory_cfg in constants.config["territories"].items():
        world = simulation_api.World(name=territory_cfg.get("name", territory_key))
        world.runtime_state.sim_state.zone_mechanics = {
            zone_name: dict(zone_data)
            for zone_name, zone_data in zone_mechanics_template.items()
        }
        raw_modifiers = territory_cfg.get("zone_modifiers", {})
        world.zone_modifiers = {
            zone: {key: float(value) for key, value in mods.items()}
            for zone, mods in raw_modifiers.items()
        }
        world.dissonance_threshold = float(
            territory_cfg.get("dissonance_threshold", constants.DISS_THRESHOLD)
        )

        for zone_name, zone_spawn_cfg in territory_cfg.get("zones", {}).items():
            if zone_name not in world.runtime_state.sim_state.zone_mechanics:
                continue

            npc_count = int(zone_spawn_cfg.get("npc_count", 0))
            raw_resources = zone_spawn_cfg.get("initial_resources", {})
            stability = float(raw_resources.get("STABILITY", 50.0))
            initial_resources = {
                "money": float(raw_resources.get("MATERIAL", random.uniform(50, 150))),
                "energy": float(raw_resources.get("ENERGY", random.uniform(50, 100))),
                "stress": max(0.0, 100.0 - stability),
            }

            for _ in range(npc_count):
                npc = simulation_api.spawn_npc(zone_name, initial_resources, home_x, home_y)
                world.npcs.append(npc)

        worlds.append(world)
        startup_messages.extend(summarize_world(world, territory_key))

    startup_messages.append(
        f"Total: {len(worlds)} territories, {sum(len(world.npcs) for world in worlds)} NPCs"
    )
    startup_messages.append("All systems initialized.")
    startup_messages.append("Press M for the full controls list.")
    startup_messages.append("Quick keys: X snapshot | C CENTRAL | E EMX | V weather | SPACE pause | click edge tabs | wheel scroll | bottom/right scrollbars")
    startup_messages.append("")
    return worlds, startup_messages


def create_validator() -> BaselineValidator:
    return BaselineValidator(observation_window=500)


def wrap_text(text: str, font: pygame.font.Font, max_width: int) -> list[str]:
    if not text:
        return [""]
    words = text.split()
    if not words:
        return [""]
    lines = [words[0]]
    for word in words[1:]:
        candidate = f"{lines[-1]} {word}"
        if font.size(candidate)[0] <= max_width:
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines


def startup_line_color(message: str) -> tuple[int, int, int]:
    if message.startswith("⚠️"):
        return constants.YELLOW_TEXT
    if message.startswith("✅") or message.startswith("All systems initialized") or message.startswith("Total:"):
        return constants.LIME
    if message.startswith("Keys:"):
        return (160, 200, 255)
    return constants.WHITE


def draw_startup_overlay(context: AppContext, surface: pygame.Surface | None = None) -> None:
    if (
        not context.ui_state.startup_messages
        or pygame.time.get_ticks() >= context.ui_state.startup_until_ms
    ):
        return
    target_surface = surface or context.screen

    panel_w = min(constants.WIDTH - 80, 980)
    panel_h = min(constants.HEIGHT - 80, 660)
    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel.fill((10, 14, 26, 156))
    pygame.draw.rect(panel, (90, 135, 205, 190), panel.get_rect(), width=2, border_radius=14)

    title = context.fonts["header"].render("System initialization", True, constants.LIME)
    hint = context.fonts["small"].render("Press any key to dismiss", True, (180, 200, 220))
    panel.blit(title, (24, 18))
    panel.blit(hint, (24, 18 + title.get_height() + 4))

    text_font = context.fonts["tiny"]
    text_width = panel_w - 48
    y = 18 + title.get_height() + hint.get_height() + 22
    for message in context.ui_state.startup_messages:
        if not message:
            y += 8
            continue
        for line in wrap_text(message, text_font, text_width):
            line_surf = text_font.render(line, True, startup_line_color(message))
            panel.blit(line_surf, (24, y))
            y += line_surf.get_height() + 4
            if y > panel_h - 24:
                break
        if y > panel_h - 24:
            break

    panel_rect = panel.get_rect(center=(constants.WIDTH // 2, constants.HEIGHT // 2))
    target_surface.blit(panel, panel_rect)


def create_app_context() -> AppContext:
    ensure_daily_summary_folder()
    configure_stdio()
    renderer_backend, render_backend_status = rendering.create_renderer_backend(constants.RENDER_BACKEND)
    try:
        screen, clock = init_pygame(renderer_backend)
    except Exception as exc:
        renderer_backend, render_backend_status = rendering.create_software_renderer_backend(
            reason=f"OpenGL initialization failed; software fallback active ({exc})",
            requested_backend=constants.RENDER_BACKEND,
        )
        screen, clock = init_pygame(renderer_backend)
    fonts = build_fonts()
    anchors, zone_bases = build_layout()
    install_runtime_bindings(anchors=anchors)

    simulation_template = SimulationState()
    startup_messages = [constants.describe_weather_preset(), *constants.describe_factor_profiles(), ""]
    startup_messages.extend(initialize_zone_mechanics(simulation_template))
    worlds, world_messages = build_worlds(anchors, simulation_template.zone_mechanics)
    startup_messages.extend(world_messages)
    tuning.configure_runtime(worlds=worlds, anchors=anchors)
    ui_state = simulation_api.UIState()
    validator = create_validator()
    startup_messages.append("Baseline validator initialized (metrics start after tick 100)")
    startup_messages.append(render_backend_status.summary_line())
    ui_state.startup_messages = startup_messages
    ui_state.startup_until_ms = pygame.time.get_ticks() + 10000
    return AppContext(
        screen=screen,
        clock=clock,
        fonts=fonts,
        anchors=anchors,
        zone_bases=zone_bases,
        worlds=worlds,
        ui_state=ui_state,
        validator=validator,
        renderer_backend=renderer_backend,
        render_backend_status=render_backend_status,
    )


def cycle_weather_preset() -> None:
    if not constants.EMX_WEATHER_PRESET_ORDER:
        return
    current_idx = constants.EMX_WEATHER_PRESET_ORDER.index(constants.EMX_WEATHER_CURRENT_PRESET)
    next_idx = (current_idx + 1) % len(constants.EMX_WEATHER_PRESET_ORDER)
    constants.EMX_WEATHER_CURRENT_PRESET = constants.EMX_WEATHER_PRESET_ORDER[next_idx]
    constants.apply_weather_preset(constants.EMX_WEATHER_CURRENT_PRESET)
    emx_system.EMX_WEATHER_DECAY = constants.EMX_WEATHER_DECAY
    emx_system.EMX_WEATHER_EMIT_RATE = constants.EMX_WEATHER_EMIT_RATE
    emx_system.EMX_WEATHER_CANCEL_RATIO = constants.EMX_WEATHER_CANCEL_RATIO
    emx_system.EMX_WEATHER_ABSORB_RATE = constants.EMX_WEATHER_ABSORB_RATE
    print(
        f"Weather preset switched: {constants.EMX_WEATHER_CURRENT_PRESET} "
        f"(decay={constants.EMX_WEATHER_DECAY}, emit={constants.EMX_WEATHER_EMIT_RATE}, "
        f"cancel={constants.EMX_WEATHER_CANCEL_RATIO}, absorb={constants.EMX_WEATHER_ABSORB_RATE})"
    )


def event_has_shift(event) -> bool:
    mods = getattr(event, "mod", None)
    if mods is None:
        try:
            mods = pygame.key.get_mods()
        except pygame.error:
            mods = 0
    return bool(mods & pygame.KMOD_SHIFT)


def show_status(ui_state: simulation_api.UIState, message: str, duration_ms: int = 1400) -> None:
    ui_state.status_message = message
    ui_state.status_until_ms = pygame.time.get_ticks() + duration_ms


def clamp_render_scroll(
    ui_state: simulation_api.UIState,
    *,
    logical_width: int,
    logical_height: int,
    display_width: int,
    display_height: int,
) -> tuple[int, int]:
    max_scroll_x = max(0, logical_width - max(1, display_width))
    max_scroll_y = max(0, logical_height - max(1, display_height))
    ui_state.render_scroll_x = max(0, min(ui_state.render_scroll_x, max_scroll_x))
    ui_state.render_scroll_y = max(0, min(ui_state.render_scroll_y, max_scroll_y))
    return max_scroll_x, max_scroll_y


def handle_mouse_wheel_delta(delta_y: int, context: AppContext) -> bool:
    if delta_y == 0:
        return True

    display_surface = pygame.display.get_surface()
    if display_surface is None:
        return True

    _, max_scroll_y = clamp_render_scroll(
        context.ui_state,
        logical_width=context.screen.get_width(),
        logical_height=context.screen.get_height(),
        display_width=display_surface.get_width(),
        display_height=display_surface.get_height(),
    )
    if max_scroll_y <= 0:
        return True

    scroll_step = 48
    context.ui_state.render_scroll_y = max(
        0,
        min(max_scroll_y, context.ui_state.render_scroll_y - int(delta_y) * scroll_step),
    )
    return True


def _horizontal_scrollbar_metrics(context: AppContext):
    display_surface = pygame.display.get_surface()
    if display_surface is None:
        return None
    return rendering.build_horizontal_scrollbar_metrics(
        logical_size=context.screen.get_size(),
        display_size=display_surface.get_size(),
        scroll_x=context.ui_state.render_scroll_x,
    )


def _vertical_scrollbar_metrics(context: AppContext):
    display_surface = pygame.display.get_surface()
    if display_surface is None:
        return None
    return rendering.build_vertical_scrollbar_metrics(
        logical_size=context.screen.get_size(),
        display_size=display_surface.get_size(),
        scroll_y=context.ui_state.render_scroll_y,
    )


def handle_horizontal_scrollbar_click(event, context: AppContext) -> bool:
    metrics = _horizontal_scrollbar_metrics(context)
    if metrics is None:
        return False

    thumb_rect = metrics["thumb_rect"]
    track_rect = metrics["track_rect"]
    if thumb_rect.collidepoint(event.pos):
        context.ui_state.horizontal_scroll_dragging = True
        context.ui_state.horizontal_scroll_drag_offset_x = int(event.pos[0] - thumb_rect.x)
        return True
    if track_rect.collidepoint(event.pos):
        context.ui_state.render_scroll_x = rendering.horizontal_scroll_from_thumb_left(
            int(event.pos[0] - thumb_rect.width // 2),
            metrics,
        )
        context.ui_state.horizontal_scroll_dragging = True
        refreshed = _horizontal_scrollbar_metrics(context)
        if refreshed is not None:
            context.ui_state.horizontal_scroll_drag_offset_x = int(event.pos[0] - refreshed["thumb_rect"].x)
        return True
    return False


def handle_vertical_scrollbar_click(event, context: AppContext) -> bool:
    metrics = _vertical_scrollbar_metrics(context)
    if metrics is None:
        return False

    thumb_rect = metrics["thumb_rect"]
    track_rect = metrics["track_rect"]
    if thumb_rect.collidepoint(event.pos):
        context.ui_state.vertical_scroll_dragging = True
        context.ui_state.vertical_scroll_drag_offset_y = int(event.pos[1] - thumb_rect.y)
        return True
    if track_rect.collidepoint(event.pos):
        context.ui_state.render_scroll_y = rendering.vertical_scroll_from_thumb_top(
            int(event.pos[1] - thumb_rect.height // 2),
            metrics,
        )
        context.ui_state.vertical_scroll_dragging = True
        refreshed = _vertical_scrollbar_metrics(context)
        if refreshed is not None:
            context.ui_state.vertical_scroll_drag_offset_y = int(event.pos[1] - refreshed["thumb_rect"].y)
        return True
    return False


def handle_mouse_button_up(event, context: AppContext) -> bool:
    if getattr(event, "button", None) == 1:
        context.ui_state.horizontal_scroll_dragging = False
        context.ui_state.vertical_scroll_dragging = False
    return True


def handle_mouse_motion(event, context: AppContext) -> bool:
    if context.ui_state.horizontal_scroll_dragging:
        metrics = _horizontal_scrollbar_metrics(context)
        if metrics is None:
            context.ui_state.horizontal_scroll_dragging = False
        else:
            context.ui_state.render_scroll_x = rendering.horizontal_scroll_from_thumb_left(
                int(event.pos[0] - context.ui_state.horizontal_scroll_drag_offset_x),
                metrics,
            )
    if context.ui_state.vertical_scroll_dragging:
        metrics = _vertical_scrollbar_metrics(context)
        if metrics is None:
            context.ui_state.vertical_scroll_dragging = False
        else:
            context.ui_state.render_scroll_y = rendering.vertical_scroll_from_thumb_top(
                int(event.pos[1] - context.ui_state.vertical_scroll_drag_offset_y),
                metrics,
            )
    return True


def handle_mouse_button_down(event, context: AppContext) -> bool:
    if getattr(event, "button", None) == 4:
        return handle_mouse_wheel_delta(1, context)
    if getattr(event, "button", None) == 5:
        return handle_mouse_wheel_delta(-1, context)
    if getattr(event, "button", None) != 1:
        return True

    if handle_horizontal_scrollbar_click(event, context):
        return True
    if handle_vertical_scrollbar_click(event, context):
        return True

    display_surface = pygame.display.get_surface()
    display_size = display_surface.get_size() if display_surface is not None else context.screen.get_size()
    viewport_origin = (
        context.ui_state.render_scroll_x,
        context.ui_state.render_scroll_y,
    )
    action = rendering.resolve_edge_tab_action(
        event.pos,
        logical_size=context.screen.get_size(),
        display_size=display_size,
        viewport_origin=viewport_origin,
        worlds=context.worlds,
        ui_state=context.ui_state,
        fonts=context.fonts,
    )
    if action is None:
        return True

    action_type, value = action
    ui_state = context.ui_state

    if pygame.time.get_ticks() < ui_state.startup_until_ms:
        ui_state.startup_until_ms = 0

    if action_type == "world":
        new_index = int(value)
        if new_index != ui_state.current_world_idx:
            ui_state.current_world_idx = new_index
            ui_state.inspector_page = 0
            print(f"Switched to: {context.worlds[new_index].name}")
            show_status(ui_state, f"WORLD: {context.worlds[new_index].name}")
        return True

    if action_type == "view":
        new_zone = value
        if ui_state.focused_zone == new_zone:
            ui_state.focused_zone = None
        else:
            ui_state.focused_zone = new_zone

        label = "MAP" if ui_state.focused_zone is None else ui_state.focused_zone
        print(f"View: {label}")
        show_status(ui_state, f"VIEW: {label}")
    return True


def handle_keydown(event, context: AppContext) -> bool:
    ui_state = context.ui_state
    worlds = context.worlds
    if pygame.time.get_ticks() < ui_state.startup_until_ms:
        ui_state.startup_until_ms = 0

    # --- Load Menu Sub-input ---
    if ui_state.show_load_menu:
        if event.key in (pygame.K_ESCAPE, pygame.K_6):
            ui_state.show_load_menu = False
        elif event.key == pygame.K_1:
            try:
                save_load.load_snapshot(context, slot=1, create_validator=create_validator)
                ui_state.show_load_menu = False
                show_status(ui_state, "LOADED SLOT 1")
            except Exception as e:
                show_status(ui_state, f"LOAD FAILED: {e}")
        elif event.key == pygame.K_2:
            try:
                save_load.load_snapshot(context, slot=2, create_validator=create_validator)
                ui_state.show_load_menu = False
                show_status(ui_state, "LOADED SLOT 2")
            except Exception as e:
                show_status(ui_state, f"LOAD FAILED: {e}")
        elif event.key == pygame.K_3:
            try:
                save_load.load_snapshot(context, slot=3, create_validator=create_validator)
                ui_state.show_load_menu = False
                show_status(ui_state, "LOADED SLOT 3")
            except Exception as e:
                show_status(ui_state, f"LOAD FAILED: {e}")
        return True

    if event.key == pygame.K_SPACE:
        ui_state.paused = not ui_state.paused
        print(f"Simulation: {'PAUSED' if ui_state.paused else 'RUNNING'}")
        show_status(ui_state, "PAUSED" if ui_state.paused else "RUNNING")
    elif event.key == pygame.K_q:
        ui_state.active_save_slot = (ui_state.active_save_slot % 3) + 1
        show_status(ui_state, f"ACTIVE SLOT: {ui_state.active_save_slot}")
    elif event.key == pygame.K_5:
        snapshot_path = save_load.save_snapshot(context, slot=ui_state.active_save_slot)
        print(f"Snapshot saved: {snapshot_path}")
        show_status(ui_state, f"SAVED SLOT {ui_state.active_save_slot}")
        # Auto-cycle for convenience similar to 'chinn'
        ui_state.active_save_slot = (ui_state.active_save_slot % 3) + 1
    elif event.key == pygame.K_6:
        ui_state.show_load_menu = True
        ui_state.show_tuning_panel = False
        ui_state.show_npc_inspector = False
        ui_state.show_statistics = False
    elif event.key == pygame.K_l:
        ui_state.show_tuning_panel = not ui_state.show_tuning_panel
        print(f"Live Tuning: {'ON' if ui_state.show_tuning_panel else 'OFF'}")
    elif ui_state.show_tuning_panel and event.key in (
        pygame.K_UP,
        pygame.K_DOWN,
        pygame.K_LEFT,
        pygame.K_RIGHT,
        pygame.K_PAGEUP,
        pygame.K_PAGEDOWN,
        pygame.K_HOME,
        pygame.K_END,
    ):
        category_count = len(tuning.categories())
        if category_count == 0:
            ui_state.tuning_category_idx = 0
            ui_state.tuning_item_idx = 0
            return True

        ui_state.tuning_category_idx = max(0, min(category_count - 1, ui_state.tuning_category_idx))

        if event.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN):
            direction = -1 if event.key == pygame.K_PAGEUP else 1
            ui_state.tuning_category_idx = max(
                0,
                min(category_count - 1, ui_state.tuning_category_idx + direction),
            )
            ui_state.tuning_item_idx = 0
            return True

        page = tuning.page_for(ui_state.tuning_category_idx)
        if not page:
            ui_state.tuning_item_idx = 0
            return True
        ui_state.tuning_item_idx = max(0, min(len(page) - 1, ui_state.tuning_item_idx))

        if event.key == pygame.K_UP:
            ui_state.tuning_item_idx = max(0, ui_state.tuning_item_idx - 1)
        elif event.key == pygame.K_DOWN:
            ui_state.tuning_item_idx = min(len(page) - 1, ui_state.tuning_item_idx + 1)
        elif event.key == pygame.K_HOME:
            ui_state.tuning_item_idx = 0
        elif event.key == pygame.K_END:
            ui_state.tuning_item_idx = len(page) - 1
        elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
            knob = page[ui_state.tuning_item_idx]
            fast = event_has_shift(event)
            knob.adjust(-1 if event.key == pygame.K_LEFT else 1, fast=fast)
            print(f"Tuned {knob.label}: {knob.value():.6g} amp={knob.amplitude():.3f}")
        ui_state.tuning_item_idx = max(0, min(len(page) - 1, ui_state.tuning_item_idx))
    elif event.key == pygame.K_TAB:
        ui_state.current_world_idx = (ui_state.current_world_idx + 1) % len(worlds)
        ui_state.inspector_page = 0
        print(f"Switched to: {worlds[ui_state.current_world_idx].name}")
    elif event.key == pygame.K_e:
        ui_state.show_emx_panel = not ui_state.show_emx_panel
        print(f"EMX Panel: {'ON' if ui_state.show_emx_panel else 'OFF'}")
    elif event.key == pygame.K_b:
        ui_state.show_npc_inspector = not ui_state.show_npc_inspector
        print(f"Inspector: {'ON' if ui_state.show_npc_inspector else 'OFF'}")
    elif event.key == pygame.K_i:
        ui_state.show_statistics = not ui_state.show_statistics
        print(f"Statistics: {'ON' if ui_state.show_statistics else 'OFF'}")
    elif event.key == pygame.K_x:
        ui_state.show_game_snapshot_panel = not ui_state.show_game_snapshot_panel
        print(f"Game Snapshot: {'ON' if ui_state.show_game_snapshot_panel else 'OFF'}")
    elif event.key == pygame.K_m:
        ui_state.show_controls_panel = not ui_state.show_controls_panel
        print(f"Controls Help: {'ON' if ui_state.show_controls_panel else 'OFF'}")
    elif event.key == pygame.K_s:
        ui_state.focused_zone = "SCIENCE" if ui_state.focused_zone != "SCIENCE" else None
        print(f"SCIENCE Detail: {'ON' if ui_state.focused_zone else 'OFF'}")
    elif event.key == pygame.K_t:
        ui_state.focused_zone = "TRADE" if ui_state.focused_zone != "TRADE" else None
        print(f"TRADE Detail: {'ON' if ui_state.focused_zone else 'OFF'}")
    elif event.key == pygame.K_d:
        ui_state.focused_zone = "DEVELOPMENT" if ui_state.focused_zone != "DEVELOPMENT" else None
        print(f"DEVELOPMENT Detail: {'ON' if ui_state.focused_zone else 'OFF'}")
    elif event.key == pygame.K_f:
        ui_state.focused_zone = "FLEX" if ui_state.focused_zone != "FLEX" else None
        print(f"FLEX Detail: {'ON' if ui_state.focused_zone else 'OFF'}")
    elif event.key == pygame.K_h:
        ui_state.focused_zone = "HOME" if ui_state.focused_zone != "HOME" else None
        print(f"HOME Detail: {'ON' if ui_state.focused_zone else 'OFF'}")
    elif event.key == pygame.K_p:
        ui_state.focused_zone = "PANTHEON" if ui_state.focused_zone != "PANTHEON" else None
        print(f"PANTHEON Detail: {'ON' if ui_state.focused_zone else 'OFF'}")
    elif event.key == pygame.K_c:
        ui_state.focused_zone = "CENTRAL" if ui_state.focused_zone != "CENTRAL" else None
        print(f"CENTRAL Detail: {'ON' if ui_state.focused_zone else 'OFF'}")
    elif event.key == pygame.K_v:
        ui_state.show_weather_panel = not ui_state.show_weather_panel
        print(f"Weather Screen: {'ON' if ui_state.show_weather_panel else 'OFF'}")
    elif event.key == pygame.K_g:
        ui_state.show_cell_grid = not ui_state.show_cell_grid
        print(f"Cell Grid: {'ON' if ui_state.show_cell_grid else 'OFF'}")
    elif event.key == pygame.K_u:
        cycle_weather_preset()
    elif event.key == pygame.K_PAGEUP:
        if ui_state.show_npc_inspector:
            ui_state.inspector_page -= 1
        elif ui_state.show_statistics:
            ui_state.statistics_page -= 1
        elif ui_state.show_emx_panel:
            ui_state.emx_page -= 1
    elif event.key == pygame.K_PAGEDOWN:
        if ui_state.show_npc_inspector:
            ui_state.inspector_page += 1
        elif ui_state.show_statistics:
            ui_state.statistics_page += 1
        elif ui_state.show_emx_panel:
            ui_state.emx_page += 1
    elif event.key == pygame.K_HOME:
        if ui_state.show_statistics:
            ui_state.statistics_page = 0
    elif event.key == pygame.K_END:
        if ui_state.show_statistics:
            page_size = 20
            npc_count = len(worlds[ui_state.current_world_idx].npcs)
            total_pages = max(1, math.ceil(npc_count / page_size))
            ui_state.statistics_page = total_pages - 1
    elif event.key == pygame.K_0:
        ui_state.inspector_zone_idx = None
        ui_state.inspector_page = 0
    elif event.key == pygame.K_1:
        ui_state.inspector_zone_idx = 0
        ui_state.inspector_page = 0
    elif event.key == pygame.K_2:
        ui_state.inspector_zone_idx = 1
        ui_state.inspector_page = 0
    elif event.key == pygame.K_3:
        ui_state.inspector_zone_idx = 2
        ui_state.inspector_page = 0
    elif event.key == pygame.K_4:
        ui_state.inspector_zone_idx = 3
        ui_state.inspector_page = 0
    elif event.key == pygame.K_ESCAPE:
        return False
    return True


def process_events(context: AppContext) -> bool:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return False
        if event.type in WINDOW_RESIZE_EVENTS:
            continue
        if event.type == pygame.KEYDOWN and not handle_keydown(event, context):
            return False
        if event.type == pygame.MOUSEWHEEL and not handle_mouse_wheel_delta(event.y, context):
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and not handle_mouse_button_down(event, context):
            return False
        if event.type == pygame.MOUSEBUTTONUP and not handle_mouse_button_up(event, context):
            return False
        if event.type == pygame.MOUSEMOTION and not handle_mouse_motion(event, context):
            return False
    return True


def run_logic_phase(context: AppContext) -> None:
    for _ in range(LOGIC_TICKS_PER_FRAME):
        run_logic_tick(context)


def run_logic_tick(context: AppContext) -> None:
    # List of zones that need background field computing
    SHADOW_ZONES = ["CENTRAL", "SCIENCE", "TRADE", "DEVELOPMENT", "FLEX", "PANTHEON"]

    context.global_tick += 1

    # Periodic date-rollover check (every 5000 ticks)
    if context.global_tick % 5000 == 0:
        ensure_daily_summary_folder()

    active_world = context.worlds[context.ui_state.current_world_idx]
    focused_zone = context.ui_state.focused_zone

    for world in context.worlds:
        world.step(context.global_tick, context.anchors)

        # Background "shadow computing" for emotional fields
        if world is active_world:
            if focused_zone:
                rendering.update_field_logic(
                    world,
                    context.global_tick,
                    focused_zone,
                )

            zone_to_update = SHADOW_ZONES[context.global_tick % len(SHADOW_ZONES)]
            if zone_to_update != focused_zone:
                rendering.update_field_logic(
                    world,
                    context.global_tick,
                    zone_to_update,
                )

        rendering.save_start_npc_state_once(world, context.global_tick)
        if context.global_tick >= 100:
            context.validator.tick(world, context.global_tick)


def run_physical_phase(context: AppContext) -> None:
    for world in context.worlds:
        for npc in world.npcs:
            if npc.state == "TRAVELING":
                if npc.visual_step(speed=3.5):
                    # Logical transition is now unified in simulation_api.finalize_arrival
                    simulation_api.finalize_arrival(npc, npc.target, context.global_tick)


def render_frame(context: AppContext) -> None:
    display_surface = pygame.display.get_surface()
    display_width = display_surface.get_width() if display_surface is not None else context.screen.get_width()
    display_height = display_surface.get_height() if display_surface is not None else context.screen.get_height()
    clamp_render_scroll(
        context.ui_state,
        logical_width=context.screen.get_width(),
        logical_height=context.screen.get_height(),
        display_width=display_width,
        display_height=display_height,
    )
    active_world = context.worlds[context.ui_state.current_world_idx]
    frame_packet = rendering.build_frame_render_packet(
        world=active_world,
        worlds=context.worlds,
        ui_state=context.ui_state,
        fonts=context.fonts,
        tick=context.global_tick,
        anchors=context.anchors,
        active_backend=context.render_backend_status.active,
    )
    overlay_surface = None
    if frame_packet.gpu_primitives_enabled and (
        frame_packet.gpu_circle_primitives or frame_packet.gpu_rect_primitives
    ):
        overlay_surface = pygame.Surface(context.screen.get_size(), pygame.SRCALPHA)
    overlay_target = overlay_surface if overlay_surface is not None else context.screen
    context.screen.fill(constants.PURPLE)
    rendering.render_view(screen=context.screen, packet=frame_packet, overlay_surface=overlay_surface)
    world_name_text = context.fonts["header"].render(
        frame_packet.world_label,
        True,
        constants.LIME,
    )
    overlay_target.blit(
        world_name_text,
        (constants.WIDTH // 2 - world_name_text.get_width() // 2, 10),
    )
    if frame_packet.paused:
        paused_text = context.fonts["large"].render("PAUSED", True, constants.WHITE)
        paused_bg = pygame.Rect(0, 0, paused_text.get_width() + 28, paused_text.get_height() + 16)
        paused_bg.center = (constants.WIDTH // 2, 52)
        pygame.draw.rect(overlay_target, constants.BLACK, paused_bg, border_radius=8)
        pygame.draw.rect(overlay_target, constants.LIME, paused_bg, width=2, border_radius=8)
        overlay_target.blit(
            paused_text,
            (
                paused_bg.x + (paused_bg.width - paused_text.get_width()) // 2,
                paused_bg.y + (paused_bg.height - paused_text.get_height()) // 2,
            ),
        )
    if (
        frame_packet.status_message
        and pygame.time.get_ticks() < context.ui_state.status_until_ms
    ):
        status_text = context.fonts["small"].render(frame_packet.status_message, True, constants.WHITE)
        status_bg = pygame.Rect(0, 0, status_text.get_width() + 24, status_text.get_height() + 12)
        status_bg.center = (constants.WIDTH // 2, 92 if frame_packet.paused else 60)
        pygame.draw.rect(overlay_target, constants.BLACK, status_bg, border_radius=8)
        pygame.draw.rect(overlay_target, constants.LIME, status_bg, width=2, border_radius=8)
        overlay_target.blit(
            status_text,
            (
                status_bg.x + (status_bg.width - status_text.get_width()) // 2,
                status_bg.y + (status_bg.height - status_text.get_height()) // 2,
            ),
        )
    elif frame_packet.status_message:
        context.ui_state.status_message = None
        context.ui_state.status_until_ms = 0
    draw_startup_overlay(context, surface=overlay_target)

    viewport_origin_x = context.ui_state.render_scroll_x
    viewport_origin_y = context.ui_state.render_scroll_y
    source_rect = pygame.Rect(
        viewport_origin_x,
        viewport_origin_y,
        min(display_width, max(0, context.screen.get_width() - viewport_origin_x)),
        min(display_height, max(0, context.screen.get_height() - viewport_origin_y)),
    )

    frame_viewport = pygame.Surface((display_width, display_height)).convert_alpha()
    frame_viewport.fill(constants.PURPLE)
    if source_rect.width > 0 and source_rect.height > 0:
        frame_viewport.blit(context.screen, (0, 0), area=source_rect)

    overlay_viewport = pygame.Surface((display_width, display_height), pygame.SRCALPHA)
    if overlay_surface is not None and source_rect.width > 0 and source_rect.height > 0:
        overlay_viewport.blit(overlay_surface, (0, 0), area=source_rect)

    rendering.draw_viewport_scrollbars(
        overlay_viewport,
        logical_size=context.screen.get_size(),
        display_size=(display_width, display_height),
        scroll_x=context.ui_state.render_scroll_x,
        scroll_y=context.ui_state.render_scroll_y,
        horizontal_dragging=context.ui_state.horizontal_scroll_dragging,
        vertical_dragging=context.ui_state.vertical_scroll_dragging,
    )

    context.renderer_backend.present(frame_viewport, frame_packet, overlay_viewport)
    context.clock.tick(constants.FPS)


def finalize_run(context: AppContext) -> None:
    print("\n" + "=" * 60)
    print("BASELINE VALIDATION SUMMARY")
    print("=" * 60)
    try:
        report = context.validator.finalize()
        report_path = constants.BASE_DIR / "standard_registry" / "baseline_validation_run_001.yaml"
        context.validator.save_report(str(report_path))
        
        status = report.get("status")
        if status == "VALID":
            print("BASELINE PLATEAU CERTIFIED")
            print(f"   All {report['summary']['total_metrics_checked']} metrics in acceptable range")
        elif status == "INSUFFICIENT_DATA":
            print("BASELINE PLATEAU VALIDATION: INSUFFICIENT DATA")
            print(f"   {report.get('message', 'No metrics collected')}")
        else:
            print("BASELINE PLATEAU VALIDATION INCOMPLETE")
            summary = report.get("summary")
            if summary:
                print(f"   {summary['invalid_metric_count']} metrics out of range:")
                for metric_name, result in report["metric_results"].items():
                    if not result["is_valid"]:
                        print(
                            f"      - {metric_name}: {result['observed_value']:.3f} "
                            f"(range [{result['acceptable_min']:.3f}, {result['acceptable_max']:.3f}])"
                        )
            else:
                print("   Validation failed but no summary was provided.")
    except Exception as exc:
        print(f"Error during final validation sequence: {exc}")

    if constants.NPC_STATE_EXPORT_ENABLED:
        out_dir = constants.BASE_DIR / constants.NPC_STATE_EXPORT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
    for world in context.worlds:
        safe_name = world.name.replace(" ", "_").replace("-", "_")
        if constants.NPC_STATE_EXPORT_ENABLED:
            final_path = constants.BASE_DIR / constants.NPC_STATE_EXPORT_DIR / f"final_{safe_name}.json"
        else:
            final_path = f"final_{safe_name}.json"
        rendering.save_npc_state(world, tick=context.global_tick, filename=str(final_path))
    context.renderer_backend.shutdown()
    pygame.quit()


def run_application() -> None:
    context = create_app_context()
    print_terminal_menu_hint()
    running = True
    try:
        while running:
            running = process_events(context)
            if not running:
                break
            if not context.ui_state.paused:
                run_logic_phase(context)
                run_physical_phase(context)
            render_frame(context)
    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl+C). Saving final snapshots and baseline validation...")
    finally:
        finalize_run(context)
