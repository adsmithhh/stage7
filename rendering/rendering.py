from __future__ import annotations

import math
import os
import json
from datetime import datetime

import pygame

import config.constants as constants
import tuning.api as tuning
from config.constants import (
    BLUE,
    DEV_COLOR,
    EMOTIONAL_COMPLEXES,
    EMX_CELL_GRID_COLS,
    EMX_CELL_GRID_ENABLED,
    EMX_CELL_GRID_ROWS,
    EMX_CELL_OVERLAY_ALPHA,
    EMX_COLORS,
    EMX_COMPLEX_SECOND_THRESHOLD,
    EMX_COMPLEX_TOP_THRESHOLD,
    EMX_COMPLEX_ZONE_THRESHOLD_SCALE,
    EMX_EMOTIONS,
    EMX_WEATHER_PANEL_SECTORS,
    FLEX_COLOR,
    HEIGHT,
    INSPECTOR_ZONE_ORDER,
    LIME,
    NPC_DYAD_COLORS,
    NPC_STATE_EXPORT_DIR,
    NPC_STATE_EXPORT_ENABLED,
    NPC_STATE_START_SNAPSHOT_TICK,
    ORANGE,
    PANTHEON,
    PANTHEON_TIERS,
    PERSONALITY_COLORS,
    RED_ORANGE,
    SHARED_ZONE_CONFIG,
    SHARED_ZONES,
    TEAL,
    TRADE_COLOR,
    WHITE,
    WIDTH,
    YELLOW_TEXT,
    _CENTRAL_COL,
    _PANTHEON_COL,
    color_map,
)
from emx.api import compute_emx_archetype, compute_anchor_field
from . import render_central2 as render_central
from .render_types import CirclePrimitive, FrameRenderPacket, RectPrimitive

# Module-level state injected by app runtime at startup (avoids circular import)
ANCHORS: dict = {}

# C-key panel cell constants and emotional field logic live in render_central2.py.


def draw_anchor_fields(surface, world, anchors, zone_stats, tick):
    """Soft dynamic field disc + outer ring around every anchor — drawn before NPCs."""
    _draw_circle_primitives_software(surface, _build_anchor_field_primitives(world, anchors, tick))


def configure_runtime(*, anchors: dict | None = None) -> None:
    global ANCHORS
    if anchors is not None:
        ANCHORS = anchors


def _runtime_state(world):
    return world.runtime_state


def _env_state(world):
    return _runtime_state(world).env_state


def _sim_state(world):
    return _runtime_state(world).sim_state


def _stable_phase_seed(name: str) -> float:
    return float(sum((index + 1) * ord(ch) for index, ch in enumerate(name)))


def _build_anchor_field_primitives(world, anchors, tick) -> list[CirclePrimitive]:
    primitives: list[CirclePrimitive] = []
    for zone_name, (ax, ay) in anchors.items():
        radius, intensity, color = compute_anchor_field(zone_name, world, world.zone_stats, tick)
        if intensity < 0.02:
            continue

        phase = (tick * 0.018 + _stable_phase_seed(zone_name) * 0.007) % (2 * math.pi)
        pulse = 0.88 + 0.12 * math.sin(phase)
        outer_radius = int(radius * pulse)
        if outer_radius < 4:
            continue

        fill_alpha = int(intensity * 42 * pulse)
        ring_alpha = int(intensity * 130 * pulse)
        inner_ring_radius = max(4, int(outer_radius * 0.6))

        primitives.append(
            CirclePrimitive(float(ax), float(ay), float(outer_radius), (*color, fill_alpha))
        )
        primitives.append(
            CirclePrimitive(
                float(ax),
                float(ay),
                float(outer_radius),
                (*color, ring_alpha),
                inner_radius=max(0.0, float(outer_radius - 2)),
            )
        )
        primitives.append(
            CirclePrimitive(
                float(ax),
                float(ay),
                float(inner_ring_radius),
                (*color, int(ring_alpha * 0.45)),
                inner_radius=max(0.0, float(inner_ring_radius - 1)),
            )
        )
    return primitives


def _build_atmosphere_halo_primitives(world, anchors) -> list[CirclePrimitive]:
    primitives: list[CirclePrimitive] = []
    atm = _env_state(world).zone_atmosphere
    for zone, pos in anchors.items():
        if zone not in atm:
            continue
        zone_atm = atm[zone]
        dominant = max(zone_atm, key=zone_atm.get)
        intensity = zone_atm[dominant]
        if intensity < 0.05:
            continue
        color = EMX_COLORS[dominant]
        radius = int(30 + intensity * 60)
        alpha = int(intensity * 90)
        primitives.append(
            CirclePrimitive(float(pos[0]), float(pos[1]), float(radius), (*color, alpha))
        )
    return primitives


def _build_npc_primitives(world, tick) -> list[CirclePrimitive]:
    primitives: list[CirclePrimitive] = []
    for npc in world.npcs:
        pos_x = float(int(npc.x))
        pos_y = float(int(npc.y))
        color = getattr(npc, "trait_color", WHITE)

        total_degen = (
            npc.apathy + npc.exhaustion + npc.dissociation + npc.numbness + npc.cynicism
        )
        if total_degen > 0.15:
            degen_strength = min(1.0, total_degen / 2.5)
            grey = 128
            color = (
                int(color[0] + (grey - color[0]) * degen_strength * 0.75),
                int(color[1] + (grey - color[1]) * degen_strength * 0.75),
                int(color[2] + (grey - color[2]) * degen_strength * 0.75),
            )
            ring_alpha = int(80 + degen_strength * 120)
            ring_radius = 9.0
            primitives.append(
                CirclePrimitive(
                    pos_x,
                    pos_y,
                    ring_radius,
                    (160, 160, 160, ring_alpha),
                    inner_radius=max(0.0, ring_radius - 1.0),
                )
            )

        emx = getattr(npc, "emx", {})
        emotion_spike = sum(abs(emx.get(e, 0.5) - 0.5) for e in EMX_EMOTIONS) / len(EMX_EMOTIONS)
        base_radius = 5.0

        if emotion_spike > 0.25:
            pulse_phase = (tick * 0.15 + npc.id) % (2 * math.pi)
            pulse_factor = 0.5 + 0.5 * math.sin(pulse_phase)
            flash_intensity = min(1.0, emotion_spike * 2.0)
            current_radius = base_radius + (3 * flash_intensity * pulse_factor)
            flash_alpha = int(100 + 155 * pulse_factor)
            outer_radius = current_radius * 0.8
            primitives.append(
                CirclePrimitive(
                    pos_x,
                    pos_y,
                    float(outer_radius),
                    (*color, int(flash_alpha * 0.6)),
                    inner_radius=max(0.0, float(outer_radius - 2.0)),
                )
            )

        display_radius = base_radius + (emotion_spike * 2.0) if emotion_spike > 0.15 else base_radius
        primitives.append(
            CirclePrimitive(pos_x, pos_y, float(display_radius), (*color, 255))
        )
    return primitives


def _draw_circle_primitives_software(surface, primitives: list[CirclePrimitive]) -> None:
    for primitive in primitives:
        radius = max(1, int(round(primitive.radius)))
        inner_radius = max(0, int(round(primitive.inner_radius)))
        color = primitive.color
        target = (int(round(primitive.center_x)), int(round(primitive.center_y)))

        if inner_radius <= 0 and color[3] >= 255:
            pygame.draw.circle(surface, color[:3], target, radius)
            continue

        temp_surface = pygame.Surface((radius * 2 + 4, radius * 2 + 4), pygame.SRCALPHA)
        temp_center = (radius + 2, radius + 2)
        width = 0 if inner_radius <= 0 else max(1, radius - inner_radius)
        pygame.draw.circle(temp_surface, color, temp_center, radius, width)
        surface.blit(temp_surface, (target[0] - temp_center[0], target[1] - temp_center[1]))


def build_frame_render_packet(world, worlds, ui_state, fonts, tick, anchors, active_backend) -> FrameRenderPacket:
    gpu_primitives_enabled = active_backend == "opengl"
    gpu_circle_primitives: list[CirclePrimitive] = []
    gpu_rect_primitives: list[RectPrimitive] = []
    if gpu_primitives_enabled and not ui_state.focused_zone:
        gpu_circle_primitives.extend(_build_anchor_field_primitives(world, anchors, tick))
        gpu_circle_primitives.extend(_build_atmosphere_halo_primitives(world, anchors))
        gpu_circle_primitives.extend(_build_npc_primitives(world, tick))
    if gpu_primitives_enabled and ui_state.focused_zone:
        gpu_rect_primitives.extend(render_central.build_zone_detail_rect_primitives(world, ui_state.focused_zone))

    return FrameRenderPacket(
        world=world,
        worlds=worlds,
        ui_state=ui_state,
        fonts=fonts,
        tick=tick,
        anchors=anchors,
        world_label=f"Active World: {world.name} ({ui_state.current_world_idx + 1}/{len(worlds)})",
        current_world_index=ui_state.current_world_idx,
        world_count=len(worlds),
        paused=ui_state.paused,
        status_message=ui_state.status_message,
        active_backend=active_backend,
        gpu_primitives_enabled=gpu_primitives_enabled,
        gpu_circle_primitives=gpu_circle_primitives,
        gpu_rect_primitives=gpu_rect_primitives,
    )


def draw_atmosphere(surface, world, anchors, fonts):
    """Draw emotional weather — now with complex detection!"""
    _draw_circle_primitives_software(surface, _build_atmosphere_halo_primitives(world, anchors))
    draw_atmosphere_indicators(surface, world, anchors, fonts)


def draw_atmosphere_indicators(surface, world, anchors, fonts):
    zone_complexes = _env_state(world).zone_complexes
    for zone, pos in anchors.items():
        complex_data = zone_complexes.get(zone)
        if complex_data:
            # Draw a distinctive ring showing the complex
            pygame.draw.circle(surface, complex_data["color"], (int(pos[0]), int(pos[1])), 40, 4)
            # Add label
            font_tiny = fonts.get("tiny")
            if font_tiny:
                label = font_tiny.render(complex_data["name"], True, complex_data["color"])
                surface.blit(label, (int(pos[0]) - label.get_width()//2, int(pos[1]) - 60))

def draw_npcs(surface, world, anchors, fonts, tick):
    """Draw NPCs with emotional spike visualization - flashing when emotions are intense"""
    _draw_circle_primitives_software(surface, _build_npc_primitives(world, tick))


def draw_anchors(surface, anchors, font_small, zone_stats):
    for name, (x, y) in anchors.items():
        ix, iy = int(x), int(y)

        if name == "PANTHEON":
            # Pyramid construction: concentric diamonds, one per completed tier
            tier      = PANTHEON.tier
            mat_prog  = PANTHEON.material_progress
            col       = _PANTHEON_COL
            # Completed tiers — solid rings
            for t in range(min(tier, len(PANTHEON_TIERS))):
                r = 7 + t * 6
                pts = [(ix, iy-r), (ix+r, iy), (ix, iy+r), (ix-r, iy)]
                pygame.draw.polygon(surface, col, pts, 1)
            # Current tier in-progress ring — partial alpha brightness
            r_cur = 7 + tier * 6
            prog_col = tuple(int(c * (0.35 + 0.65 * mat_prog)) for c in col)
            pts_cur  = [(ix, iy-r_cur), (ix+r_cur, iy), (ix, iy+r_cur), (ix-r_cur, iy)]
            pygame.draw.polygon(surface, prog_col, pts_cur, 2)
            # Tier label + progress bar below
            tier_name = PANTHEON_TIERS[min(tier, len(PANTHEON_TIERS)-1)]["name"]
            label     = font_small.render(f"PANTHEON T{tier}:{tier_name}", True, col)
            surface.blit(label, (ix + r_cur + 4, iy - 10))
            # Thin progress bar
            bar_w = 60
            pygame.draw.rect(surface, (60, 60, 40), (ix - bar_w//2, iy + r_cur + 3, bar_w, 4))
            pygame.draw.rect(surface, col, (ix - bar_w//2, iy + r_cur + 3,
                                             max(1, int(bar_w * mat_prog)), 4))

        elif name in SHARED_ZONES:
            zone_col = color_map.get(name, TEAL)
            pts = [(ix, iy-11), (ix+11, iy), (ix, iy+11), (ix-11, iy)]
            pygame.draw.polygon(surface, zone_col, pts, 2)
            label = font_small.render(name, True, zone_col)
            surface.blit(label, (ix + 15, iy - 10))

        else:
            pygame.draw.circle(surface, TEAL, (ix, iy), 8, 2)
            label = font_small.render(name, True, WHITE)
            surface.blit(label, (ix + 15, iy - 10))

        # NPC count if available
        if name in zone_stats:
            pop      = zone_stats[name].current_population
            pop_text = font_small.render(str(pop), True, LIME)
            surface.blit(pop_text, (ix - 10, iy - 30))

def draw_statistics_dashboard(surface, world, zone_stats, fonts, ui_state):
    """Comprehensive analytics overlay with pagination"""
    panel_w, panel_h = 1280, 740
    panel_x = WIDTH // 2 - panel_w // 2
    panel_y = HEIGHT // 2 - panel_h // 2

    panel_surf = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel_surf.fill((10, 10, 40, 230))

    # Title with pagination info
    npc_count = len(world.npcs)
    page_size = 20  # NPCs per page
    total_pages = max(1, math.ceil(npc_count / page_size))

    # Clamp current page
    if ui_state.statistics_page >= total_pages:
        ui_state.statistics_page = total_pages - 1
    if ui_state.statistics_page < 0:
        ui_state.statistics_page = 0

    title = fonts['header'].render(f"📊 WORLD ANALYTICS - Page {ui_state.statistics_page + 1}/{total_pages}", True, LIME)
    panel_surf.blit(title, (40, 20))

    # Page navigation hint
    hint = fonts['small'].render("(Use PAGE UP/DOWN to navigate, TAB to switch worlds)", True, YELLOW_TEXT)
    panel_surf.blit(hint, (40, 70))

    y_offset = 120

    # Fixed pixel column positions — header and rows use identical x coords + same font
    COL_X  = [20,  80,  240, 390, 600, 720, 840, 960]
    headers = ["ID", "Personality", "Zone", "State", "Stress", "$", "E", "Fatigue"]

    for hdr, cx in zip(headers, COL_X):
        h_render = fonts['small'].render(hdr, True, YELLOW_TEXT)
        panel_surf.blit(h_render, (cx, y_offset))
    y_offset += 25
    pygame.draw.line(panel_surf, YELLOW_TEXT, (20, y_offset), (panel_w - 20, y_offset), 1)
    y_offset += 25

    # Get statistics
    total_money = sum(npc.money for npc in world.npcs)
    avg_stress = sum(npc.stress_endured for npc in world.npcs) / len(world.npcs) if world.npcs else 0

    # Calculate page indices
    start_idx = ui_state.statistics_page * page_size
    end_idx = min(start_idx + page_size, npc_count)

    # Always render exactly page_size rows — empty slots show placeholder
    for idx in range(start_idx, start_idx + page_size):
        row_bg = pygame.Surface((panel_w - 40, 22), pygame.SRCALPHA)
        row_bg.fill((25, 25, 45, 80) if (idx % 2 == 0) else (18, 18, 35, 80))
        panel_surf.blit(row_bg, (20, y_offset))

        if idx < len(world.npcs):
            npc = world.npcs[idx]

            if npc.stress_endured > 85:
                row_color = (255, 0, 0)
            elif npc.stress_endured > 70:
                row_color = (255, 150, 0)
            elif npc.stress_endured > 50:
                row_color = (200, 200, 0)
            elif npc.is_collapsing:
                row_color = (255, 0, 150)
            else:
                row_color = WHITE

            fatigue = getattr(npc, 'current_zone_ticks', 0)
            columns = [
                str(npc.id),
                getattr(npc, 'personality_name', '?'),
                npc.zone,
                npc.state,
                f"{npc.stress_endured:.0f}",
                f"{npc.money:.0f}",
                f"{npc.energy:.0f}",
                f"{fatigue}"
            ]
            for value, cx in zip(columns, COL_X):
                panel_surf.blit(fonts['small'].render(value, True, row_color), (cx, y_offset))
        else:
            panel_surf.blit(fonts['small'].render("—", True, (60, 60, 80)), (COL_X[0], y_offset))

        y_offset += 24

    # ===== OVERVIEW STATS PANEL =====
    overview_x = 1100
    overview_y = 20
    line_height = 28
    OVERVIEW_W, OVERVIEW_H = 260, 160  # fixed — enough for longest line

    overview_stats = [
        f"💰 Total Wealth: ${total_money:.1f}",
        f"😰 Avg Stress: {avg_stress:.1f}",
        f"📄 Page: {ui_state.statistics_page + 1}/{total_pages}",
        f"👁️ Showing: {start_idx+1}-{end_idx} of {npc_count}"
    ]

    pygame.draw.rect(panel_surf, (20, 20, 50, 200), (overview_x - 20, overview_y - 20, OVERVIEW_W, OVERVIEW_H))
    pygame.draw.rect(panel_surf, YELLOW_TEXT, (overview_x - 20, overview_y - 20, OVERVIEW_W, OVERVIEW_H), 2)

    for i, stat in enumerate(overview_stats):
        text = fonts['small'].render(stat, True, YELLOW_TEXT)
        panel_surf.blit(text, (overview_x, overview_y + i * line_height))

    # ===== ZONE STATS PANEL =====
    zone_x = 1100
    zone_y = overview_y + OVERVIEW_H + 20  # fixed gap below overview
    ZONE_W, ZONE_H = 260, 380  # fixed — 4 zones × 70px + title + padding

    pygame.draw.rect(panel_surf, (20, 20, 50, 200), (zone_x - 20, zone_y - 20, ZONE_W, ZONE_H))
    pygame.draw.rect(panel_surf, BLUE, (zone_x - 20, zone_y - 20, ZONE_W, ZONE_H), 2)

    zone_title = fonts['small'].render("📍 ZONE STATISTICS:", True, BLUE)
    panel_surf.blit(zone_title, (zone_x, zone_y))
    zone_y += 30

    for zone_name, stats in zone_stats.items():
        # Zone header with color
        zone_header = f"{zone_name}:"
        _color_map = {
            "SCIENCE": (100, 200, 255),
            "TRADE": TRADE_COLOR,
            "DEVELOPMENT": DEV_COLOR,
            "FLEX": FLEX_COLOR,
        }
        color = _color_map.get(zone_name, WHITE)

        header_text = fonts['tiny'].render(zone_header, True, color)
        panel_surf.blit(header_text, (zone_x, zone_y))

        # Zone stats
        stats_line1 = f"  👥 Pop: {stats.current_population:2d} | 📊 Eff: {stats.efficiency_rating:.2f}"
        stats_line2 = f"  💼 Work: {stats.total_work_done:4d} | 💰 $: {stats.total_money_generated:.1f}"
        stats_line3 = f"  🏭 Cong: {stats.congestion_level:.2f} | 📈 Demand: {stats.market_demand:.2f}"

        line1_text = fonts['tiny'].render(stats_line1, True, WHITE)
        line2_text = fonts['tiny'].render(stats_line2, True, WHITE)
        line3_text = fonts['tiny'].render(stats_line3, True, WHITE)

        panel_surf.blit(line1_text, (zone_x + 20, zone_y + 15))
        panel_surf.blit(line2_text, (zone_x + 20, zone_y + 30))
        panel_surf.blit(line3_text, (zone_x + 20, zone_y + 45))
        zone_y += 70

    # ===== PAGE NAVIGATION AT BOTTOM =====
    bottom_y = panel_h - 80
    page_info = fonts['small'].render(f"📄 Page {ui_state.statistics_page + 1}/{total_pages} | 👥 NPCs {start_idx+1}-{end_idx} of {npc_count}", True, LIME)
    panel_surf.blit(page_info, (panel_w // 2 - page_info.get_width() // 2, bottom_y))

    if total_pages > 1:
        nav_hint = fonts['tiny'].render("PAGE UP/DOWN: Navigate | HOME/END: First/Last Page", True, YELLOW_TEXT)
        panel_surf.blit(nav_hint, (panel_w // 2 - nav_hint.get_width() // 2, bottom_y + 30))

    surface.blit(panel_surf, (panel_x, panel_y))


def draw_npc_inspector(surface, world, inspector_zone_idx, fonts, zone_stats, ui_state, active_events=None):
    """Enhanced NPC inspector with consolidated data table and trait descriptions at bottom."""

    panel_w, panel_h = 1270, 740
    panel_x = WIDTH // 2 - panel_w // 2
    panel_y = HEIGHT // 2 - panel_h // 2

    panel_surf = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel_surf.fill((20, 20, 40, 230))

    # Title with personality legend
    y_offset = 55

    # Personality legend on single line
    personality_title = fonts['small'].render("Personality Archetypes:", True, YELLOW_TEXT)
    panel_surf.blit(personality_title, (20, y_offset))

    arch_colors = {
        "Anchor": (80, 180, 255),
        "Climber": (255, 180, 80),
        "Connector": (180, 80, 255),
        "Survivor": (80, 255, 120),
    }
    arch_x = 250
    for arch, color in arch_colors.items():
        pygame.draw.circle(panel_surf, color, (arch_x + 8, y_offset + 8), 5)
        pygame.draw.circle(panel_surf, WHITE, (arch_x + 8, y_offset + 8), 5, 1)
        text = fonts['tiny'].render(f"= {arch}", True, color)
        panel_surf.blit(text, (arch_x + 20, y_offset))
        arch_x += 160

    y_offset += 25
    pygame.draw.line(panel_surf, YELLOW_TEXT, (20, y_offset), (panel_w - 20, y_offset), 1)
    y_offset += 25

    # Fixed pixel column positions — headers and rows both use these
    INSP_COL_X = [40, 100, 240, 390, 540, 620, 700, 800]
    INSP_HEADERS = ["ID", "Personality", "Zone", "State", "Stress", "$", "E", "Ticks"]

    # Main table headers
    for col_i, (hdr, cx) in enumerate(zip(INSP_HEADERS, INSP_COL_X)):
        h_render = fonts['small'].render(hdr, True, YELLOW_TEXT)
        panel_surf.blit(h_render, (cx, y_offset))
    y_offset += 25
    pygame.draw.line(panel_surf, YELLOW_TEXT, (20, y_offset), (panel_w - 20, y_offset), 1)
    y_offset += 25

    # All NPCs sorted by ID — stable order, never changes regardless of state
    npcs = sorted(world.npcs, key=lambda n: n.id)

    PAGE_SIZE = 6
    total_npcs = len(npcs)
    total_pages = max(1, math.ceil(total_npcs / PAGE_SIZE))

    # Looping pagination
    ui_state.inspector_page = ui_state.inspector_page % total_pages

    start_idx = ui_state.inspector_page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    displayed_npcs = npcs[start_idx:end_idx]

    zone_colors = {
        "SCIENCE": (100, 200, 255),
        "TRADE": TRADE_COLOR,
        "DEVELOPMENT": DEV_COLOR,
        "FLEX": FLEX_COLOR,
        "HOME": ORANGE,
    }

    # Draw NPC rows — always PAGE_SIZE rows, empty slots show placeholder
    for row_idx in range(PAGE_SIZE):
        npc_y_pos = y_offset + (row_idx * 45)

        row_bg_color = (25, 25, 45, 120) if row_idx % 2 == 0 else (18, 18, 35, 120)
        row_bg = pygame.Surface((panel_w - 60, 42), pygame.SRCALPHA)
        row_bg.fill(row_bg_color)
        panel_surf.blit(row_bg, (30, npc_y_pos - 2))

        if row_idx < len(displayed_npcs):
            npc = displayed_npcs[row_idx]
            ticks_in_zone = getattr(npc, 'current_zone_ticks', 0)
            personality_name = getattr(npc, 'personality_name', '?')

            if npc.stress_endured > 85:
                row_color = (255, 80, 80)
            elif npc.stress_endured > 70:
                row_color = (255, 150, 0)
            elif npc.stress_endured > 50:
                row_color = (200, 200, 0)
            elif npc.is_collapsing:
                row_color = (255, 0, 150)
            else:
                row_color = WHITE

            p_color = PERSONALITY_COLORS.get(personality_name, WHITE)
            row_cells = [
                (str(npc.id),                   row_color),
                (personality_name,               p_color),
                (npc.zone[:14],                  row_color),
                (npc.state[:14],                 row_color),
                (f"{npc.stress_endured:.0f}",    row_color),
                (f"{npc.money:.0f}",             row_color),
                (f"{npc.energy:.0f}",            row_color),
                (str(ticks_in_zone),             row_color),
            ]
            for (cell_text, cell_color), cx in zip(row_cells, INSP_COL_X):
                cell_render = fonts['small'].render(cell_text, True, cell_color)
                panel_surf.blit(cell_render, (cx, npc_y_pos))

            # Dyad label — second line of the row
            dyad = getattr(npc, 'emx_dyad', None)
            if dyad:
                dyad_color = NPC_DYAD_COLORS.get(dyad, (200, 200, 200))
                dyad_surf = fonts['tiny'].render(f"[{dyad}]", True, dyad_color)
                panel_surf.blit(dyad_surf, (INSP_COL_X[1], npc_y_pos + 22))
            # Dominant single emotion when no dyad
            else:
                dom_e = max(npc.emx, key=npc.emx.get)
                dom_v = npc.emx[dom_e]
                if dom_v > 0.6:
                    dom_color = EMX_COLORS.get(dom_e, WHITE)
                    dom_surf = fonts['tiny'].render(f"{dom_e} {dom_v:.2f}", True, dom_color)
                    panel_surf.blit(dom_surf, (INSP_COL_X[1], npc_y_pos + 22))

            zones_visited = getattr(npc, 'zones_visited_this_cycle', [])
            recent_zones = zones_visited[-4:] if zones_visited else []
            ball_x = 1220
            ball_y = npc_y_pos + 8
            ball_size = 5
            ball_spacing = 13
            for ball_idx, zone in enumerate(recent_zones):
                color = zone_colors.get(zone, WHITE)
                x_pos = ball_x - (ball_idx * ball_spacing)
                pygame.draw.circle(panel_surf, color, (x_pos, ball_y), ball_size)
                pygame.draw.circle(panel_surf, WHITE, (x_pos, ball_y), ball_size, 1)

        else:
            # Empty placeholder row — keeps layout stable
            empty_text = fonts['small'].render("—", True, (60, 60, 80))
            panel_surf.blit(empty_text, (40, npc_y_pos))

    # ===== ACTIVE EVENTS — semi-transparent strip above pagination =====
    if active_events:
        ev_x, ev_y = 20, panel_h - 100
        ev_w, ev_h = panel_w - 40, 60
        ev_surf = pygame.Surface((ev_w, ev_h), pygame.SRCALPHA)
        ev_surf.fill((35, 10, 10, 102))
        pygame.draw.rect(ev_surf, RED_ORANGE, (0, 0, ev_w, ev_h), 1)
        ev_title = fonts['tiny'].render("⚡ EVENTS:", True, RED_ORANGE)
        ev_surf.blit(ev_title, (10, 8))
        x_cur = 10 + ev_title.get_width() + 16
        for ev in active_events:
            label = f"{ev.name}"
            if ev.zone_affected:
                label += f"/{ev.zone_affected}"
            label += f" [{ev.remaining_ticks}t]"
            pill = fonts['tiny'].render(label, True, YELLOW_TEXT)
            # pill background
            pygame.draw.rect(ev_surf, (60, 30, 10, 180), (x_cur - 4, 4, pill.get_width() + 8, ev_h - 8))
            pygame.draw.rect(ev_surf, RED_ORANGE, (x_cur - 4, 4, pill.get_width() + 8, ev_h - 8), 1)
            ev_surf.blit(pill, (x_cur, (ev_h - pill.get_height()) // 2))
            x_cur += pill.get_width() + 20
            if x_cur > ev_w - 120:
                break
        panel_surf.blit(ev_surf, (ev_x, ev_y))

    # Pagination Indicator
    page_text = fonts['small'].render(f"NPCs: {total_npcs} | PAGE {ui_state.inspector_page + 1} / {total_pages}", True, LIME)
    hint_text = fonts['tiny'].render("(PAGE UP / PAGE DOWN to browse)", True, WHITE)
    panel_surf.blit(page_text, (panel_w - page_text.get_width() - 20, 20))
    panel_surf.blit(hint_text, (panel_w - hint_text.get_width() - 20, 45))

    surface.blit(panel_surf, (panel_x, panel_y))

def draw_alpha_rect(surface, color_rgb, alpha, rect):
    """Draw a filled rectangle with true alpha on any surface."""
    s = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
    s.fill((*color_rgb, alpha))
    surface.blit(s, (rect[0], rect[1]))

def draw_atmosphere_legend_corner(surface, x, y, corner_size, world, font_small, font_tiny):
    """Bottom-left: atmospheric emotional weather legend — shows dominant emotion per zone."""
    draw_alpha_rect(surface, (173, 216, 230), 51, (x, y, corner_size, corner_size))
    env_state = _env_state(world)
    zone_atmosphere = env_state.zone_atmosphere
    zone_complexes = env_state.zone_complexes
    zone_weather_history = env_state.zone_weather_history

    title = font_small.render("Atmosphere", True, (180, 200, 255))
    surface.blit(title, (x + 12, y + 10))

    row_y = y + 34
    spacing = 22

    for zone, zone_atm in list(zone_atmosphere.items())[:8]:
        dominant = max(zone_atm, key=zone_atm.get)
        intensity = zone_atm[dominant]
        col = EMX_COLORS[dominant]
        complex_data = zone_complexes.get(zone)

        complex_name = "-"
        complex_strength = 0.0
        if complex_data:
            complex_name = str(complex_data.get("name", "-"))[:4]
            if zone_weather_history.get(zone):
                latest = zone_weather_history[zone][-1]
                complex_strength = float(latest.get("complex_strength", 0.0))

        # Intensity bar (max width = 80px)
        bar_w = int(intensity * 80)
        pygame.draw.rect(surface, (50, 50, 60), (x + 12, row_y + 4, 80, 10))
        if bar_w > 0:
            pygame.draw.rect(surface, col,          (x + 12, row_y + 4, bar_w, 10))

        # Zone name + dominant emotion + complex snapshot
        label = font_tiny.render(
            f"{zone[:8]} {dominant[:4]} {complex_name} {complex_strength:.2f}",
            True,
            col,
        )
        surface.blit(label, (x + 98, row_y))

        row_y += spacing


def save_npc_state(world, tick=None, filename=None, verbose=True):
    """Save current NPC state to JSON."""
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = str(constants.BASE_DIR / f"npc_state_{timestamp}.json")
    npc_data = []
    for npc in world.npcs:
        data = {
            "tick": tick,
            "id": npc.id,
            "personality_name": getattr(npc, 'personality_name', 'Unknown'),
            "personality": getattr(npc, 'personality', {}),
            "derived": getattr(npc, 'derived', {}),
            "state": npc.state,
            "zone": npc.zone,
            "x": npc.x,
            "y": npc.y,
        }
        npc_data.append(data)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(npc_data, f, indent=2)
    if verbose:
        print(f"💾 Saved {len(npc_data)} NPCs → {filename}")
    return filename


START_SNAPSHOT_DONE = set()


def reset_runtime_state() -> None:
    START_SNAPSHOT_DONE.clear()


def save_start_npc_state_once(world, tick):
    if not NPC_STATE_EXPORT_ENABLED:
        return
    if NPC_STATE_START_SNAPSHOT_TICK < 0:
        return
    if tick < NPC_STATE_START_SNAPSHOT_TICK:
        return

    safe_name = world.name.replace(" ", "_").replace("-", "_")
    if safe_name in START_SNAPSHOT_DONE:
        return

    os.makedirs(constants.BASE_DIR / constants.NPC_STATE_EXPORT_DIR, exist_ok=True)
    start_filename = str(
        constants.BASE_DIR / 
        constants.NPC_STATE_EXPORT_DIR / 
        f"start_t{NPC_STATE_START_SNAPSHOT_TICK}_{safe_name}.json"
    )
    save_npc_state(world, tick=tick, filename=start_filename, verbose=False)
    START_SNAPSHOT_DONE.add(safe_name)


def resolve_weather_timeline_zone(world, ui_state):
    idx = ui_state.inspector_zone_idx
    if isinstance(idx, int) and 0 <= idx < len(INSPECTOR_ZONE_ORDER):
        return INSPECTOR_ZONE_ORDER[idx]

    env_state = _env_state(world)
    zone_weather_history = env_state.zone_weather_history
    zone_atmosphere = env_state.zone_atmosphere

    best_zone = None
    best_intensity = -1.0
    for zone, hist in zone_weather_history.items():
        if hist:
            current = float(hist[-1].get("dominant_intensity", 0.0))
            if current > best_intensity:
                best_intensity = current
                best_zone = zone
    if best_zone:
        return best_zone

    best_zone = None
    best_intensity = -1.0
    for zone, zone_atm in zone_atmosphere.items():
        if not zone_atm:
            continue
        intensity = max(zone_atm.values())
        if intensity > best_intensity:
            best_intensity = intensity
            best_zone = zone
    return best_zone or INSPECTOR_ZONE_ORDER[0]


def compute_zone_sector_weather(world, zone, sector_count):
    if zone not in ANCHORS:
        return []

    center_x, center_y = ANCHORS[zone]
    tau = 2.0 * math.pi
    sector_totals = [
        {"count": 0, "emotions": {emotion: 0.0 for emotion in EMX_EMOTIONS}}
        for _ in range(sector_count)
    ]

    for npc in world.npcs:
        if npc.state != "AT_WORK" or npc.zone != zone:
            continue
        angle = math.atan2(npc.y - center_y, npc.x - center_x)
        normalized = (angle + tau) % tau
        sector_idx = int((normalized / tau) * sector_count) % sector_count
        bucket = sector_totals[sector_idx]
        bucket["count"] += 1
        for emotion in EMX_EMOTIONS:
            bucket["emotions"][emotion] += float(npc.emx.get(emotion, 0.0))

    zone_threshold_scale = EMX_COMPLEX_ZONE_THRESHOLD_SCALE.get(zone, 1.0)
    zone_top_threshold = EMX_COMPLEX_TOP_THRESHOLD * zone_threshold_scale
    zone_second_threshold = EMX_COMPLEX_SECOND_THRESHOLD * zone_threshold_scale

    rows = []
    for idx, bucket in enumerate(sector_totals):
        count = int(bucket["count"])
        if count > 0:
            avg_emotions = {emotion: bucket["emotions"][emotion] / count for emotion in EMX_EMOTIONS}
        else:
            avg_emotions = {emotion: 0.0 for emotion in EMX_EMOTIONS}

        ranked = sorted(avg_emotions.items(), key=lambda item: item[1], reverse=True)
        dominant, dominant_value = ranked[0]
        second, second_value = ranked[1]

        complex_name = "-"
        complex_strength = 0.0
        complex_key = tuple(sorted([dominant, second]))
        if (
            complex_key in EMOTIONAL_COMPLEXES
            and dominant_value > zone_top_threshold
            and second_value > zone_second_threshold
        ):
            complex_name = str(EMOTIONAL_COMPLEXES[complex_key]["name"])
            complex_strength = min(dominant_value, second_value)

        rows.append({
            "sector": idx + 1,
            "population": count,
            "dominant": dominant,
            "dominant_intensity": dominant_value,
            "second": second,
            "second_intensity": second_value,
            "complex": complex_name,
            "complex_strength": complex_strength,
        })

    return rows

# C-key rendering is handled by render_central2.draw_central_view.


def draw_corner_panels(screen, world, fonts, tick):
    """Draw info panels in all four corners"""
    corner_size = 220

    total_wealth = sum(npc.money for npc in world.npcs)
    collapsed    = sum(1 for npc in world.npcs if npc.is_collapsing)
    info_lines = [
        f"Tick:      {tick}",
        f"World:     {world.name}",
        f"NPCs:      {len(world.npcs)}",
        f"Active:    {sum(1 for n in world.npcs if n.state != 'AT_HOME')}",
        f"Wealth:    ${total_wealth:.0f}",
        f"Collapsed: {collapsed}",
    ]

    # Top-left: empty
    # Bottom-left: Atmospheric weather legend
    draw_atmosphere_legend_corner(
        screen,
        0,
        HEIGHT - corner_size,
        corner_size,
        world,
        fonts['small'],
        fonts['tiny'],
    )

    # Bottom-right: world info
    draw_alpha_rect(screen, (173, 216, 230), 51, (WIDTH - corner_size, HEIGHT - corner_size, corner_size, corner_size))
    y = HEIGHT - corner_size + 10
    for line in info_lines:
        screen.blit(fonts['tiny'].render(line, True, (80, 220, 100)), (WIDTH - corner_size + 10, y))
        y += 25


def draw_emx_panel(surface, world, ui_state, fonts):
    """EMX emotion matrix panel — E key. Fixed columns per emotion, rows per NPC."""
    panel_w, panel_h = 1300, 740
    panel_x = surface.get_width() // 2 - panel_w // 2
    panel_y = surface.get_height() // 2 - panel_h // 2

    panel_surf = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel_surf.fill((15, 15, 35, 235))

    PAGE_SIZE = 14
    npcs = sorted(world.npcs, key=lambda n: n.id)
    total_npcs = len(npcs)
    total_pages = max(1, math.ceil(total_npcs / PAGE_SIZE))
    ui_state.emx_page = ui_state.emx_page % total_pages
    start_idx = ui_state.emx_page * PAGE_SIZE

    # Dynamic column positions: ID | Born→Current | emotions...
    id_col_x = 20
    born_col_x = 70
    emotion_start_x = 260
    emotion_area_w = panel_w - emotion_start_x - 30
    emotion_col_w = max(72, emotion_area_w // max(1, len(EMX_EMOTIONS)))
    EMX_COL_X = [id_col_x, born_col_x] + [
        emotion_start_x + idx * emotion_col_w for idx in range(len(EMX_EMOTIONS))
    ]
    EMX_COL_HEADERS = ["ID", "Born → Current"] + EMX_EMOTIONS

    # Title
    title = fonts['header'].render("🧠 EMOTIONAL MATRIX (EMX)", True, (255, 230, 50))
    panel_surf.blit(title, (20, 12))

    y = 50
    # Headers
    for hdr, cx in zip(EMX_COL_HEADERS, EMX_COL_X):
        col = EMX_COLORS.get(hdr, YELLOW_TEXT)
        h_surf = fonts['small'].render(hdr[:14], True, col)
        panel_surf.blit(h_surf, (cx, y))
    y += 22
    pygame.draw.line(panel_surf, YELLOW_TEXT, (10, y), (panel_w - 10, y), 1)
    y += 10

    row_h = (panel_h - y - 60) // PAGE_SIZE

    for row_idx in range(PAGE_SIZE):
        npc_idx = start_idx + row_idx
        ry = y + row_idx * row_h

        bg_color = (22, 22, 48, 120) if row_idx % 2 == 0 else (15, 15, 35, 120)
        row_bg = pygame.Surface((panel_w - 20, row_h - 2), pygame.SRCALPHA)
        row_bg.fill(bg_color)
        panel_surf.blit(row_bg, (10, ry))

        if npc_idx < total_npcs:
            npc = npcs[npc_idx]
            pname = getattr(npc, 'personality_name', '?')
            p_color = PERSONALITY_COLORS.get(pname, WHITE)
            current_type = compute_emx_archetype(npc.emx)
            c_color = PERSONALITY_COLORS.get(current_type, WHITE)
            shifted = current_type != pname

            # ID
            panel_surf.blit(fonts['small'].render(str(npc.id), True, WHITE), (EMX_COL_X[0], ry + 4))
            # Born → Current (highlight if drifted)
            born_surf = fonts['small'].render(pname[:8], True, p_color)
            panel_surf.blit(born_surf, (EMX_COL_X[1], ry + 4))
            arrow_col = (255, 80, 80) if shifted else (80, 80, 80)
            panel_surf.blit(fonts['small'].render("→", True, arrow_col), (EMX_COL_X[1] + 75, ry + 4))
            panel_surf.blit(fonts['small'].render(current_type[:8], True, c_color), (EMX_COL_X[1] + 100, ry + 4))

            # 8 emotion values as value + mini bar
            for e_idx, emotion in enumerate(EMX_EMOTIONS):
                cx = EMX_COL_X[2 + e_idx]
                val = npc.emx.get(emotion, 0.0)
                col = EMX_COLORS[emotion]
                # Value text
                val_surf = fonts['small'].render(f"{val:.2f}", True, col)
                panel_surf.blit(val_surf, (cx, ry + 4))
                # Mini bar below value
                bar_max_w = max(24, emotion_col_w - 12)
                bar_w = int(val * bar_max_w)
                bar_y = ry + row_h - 8
                pygame.draw.rect(panel_surf, (40, 40, 60), (cx, bar_y, bar_max_w, 5))
                pygame.draw.rect(panel_surf, col,          (cx, bar_y, bar_w, 5))
        else:
            panel_surf.blit(fonts['small'].render("—", True, (50, 50, 70)), (EMX_COL_X[0], ry + 4))

    # Page info
    page_surf = fonts['small'].render(
        f"Page {ui_state.emx_page + 1}/{total_pages}  |  NPCs {start_idx+1}–{min(start_idx+PAGE_SIZE, total_npcs)} of {total_npcs}  |  PgUp/PgDn to scroll",
        True, LIME
    )
    panel_surf.blit(page_surf, (20, panel_h - 30))

    surface.blit(panel_surf, (panel_x, panel_y))


def draw_weather_panel(surface, world, ui_state, fonts):
    """Weather timeline panel — V key. Separate screen-style overlay."""
    panel_w, panel_h = 1300, 740
    panel_x = surface.get_width() // 2 - panel_w // 2
    panel_y = surface.get_height() // 2 - panel_h // 2

    panel_surf = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel_surf.fill((18, 18, 42, 235))

    zone = resolve_weather_timeline_zone(world, ui_state)
    entries = []
    zone_weather_history = _env_state(world).zone_weather_history
    if zone in zone_weather_history:
        entries = list(zone_weather_history[zone])[-20:]
    sector_rows = compute_zone_sector_weather(world, zone, EMX_WEATHER_PANEL_SECTORS)

    title = fonts['header'].render(
        f"🌦️ WEATHER TIMELINE | {world.name} | {zone}",
        True,
        (180, 220, 255)
    )
    panel_surf.blit(title, (20, 14))

    subtitle = fonts['small'].render(
        f"V: toggle panel | 1-4: zone focus | TAB: world switch | sectors: {EMX_WEATHER_PANEL_SECTORS}",
        True,
        YELLOW_TEXT
    )
    panel_surf.blit(subtitle, (20, 52))

    col_x = [20, 110, 230, 400, 520, 700, 820, 1000, 1140]
    headers = ["Sec", "Pop", "Dominant", "Val", "Second", "Val", "Complex", "C.Str", "LastTick"]
    y = 88

    for header, x in zip(headers, col_x):
        panel_surf.blit(fonts['small'].render(header, True, YELLOW_TEXT), (x, y))
    y += 24
    pygame.draw.line(panel_surf, YELLOW_TEXT, (20, y), (panel_w - 20, y), 1)
    y += 10

    max_rows = len(sector_rows)
    row_h = 30
    for idx in range(max_rows):
        ry = y + idx * row_h
        bg = pygame.Surface((panel_w - 40, row_h - 2), pygame.SRCALPHA)
        bg.fill((25, 25, 50, 110) if idx % 2 == 0 else (16, 16, 38, 110))
        panel_surf.blit(bg, (20, ry))

        if idx < len(sector_rows):
            row = sector_rows[idx]
            dominant = str(row.get("dominant", "-"))
            second = str(row.get("second", "-"))
            dom_col = EMX_COLORS.get(dominant, WHITE)
            sec_col = EMX_COLORS.get(second, WHITE)
            last_tick = entries[-1].get("tick", "-") if entries else "-"

            values = [
                f"{row.get('sector', '-')}",
                f"{row.get('population', '-')}",
                dominant,
                f"{float(row.get('dominant_intensity', 0.0)):.2f}",
                second,
                f"{float(row.get('second_intensity', 0.0)):.2f}",
                f"{(row.get('complex') or '-')}",
                f"{float(row.get('complex_strength', 0.0)):.2f}",
                f"{last_tick}",
            ]
            colors = [WHITE, WHITE, dom_col, dom_col, sec_col, sec_col, WHITE, WHITE, WHITE]
            for value, x, color in zip(values, col_x, colors):
                panel_surf.blit(fonts['small'].render(value, True, color), (x, ry + 4))
        else:
            panel_surf.blit(fonts['small'].render("—", True, (70, 70, 95)), (20, ry + 4))

    surface.blit(panel_surf, (panel_x, panel_y))


def draw_cell_grid_overlay(surface, world, fonts, enabled=True):
    if not EMX_CELL_GRID_ENABLED or not enabled:
        return
    env_state = _env_state(world)
    cell_atmosphere = env_state.cell_atmosphere
    cell_population = env_state.cell_population
    if not cell_atmosphere:
        return

    cols = EMX_CELL_GRID_COLS
    rows = EMX_CELL_GRID_ROWS
    cell_w = WIDTH / cols
    cell_h = HEIGHT / rows

    grid_layer = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)

    for row in range(rows):
        for col in range(cols):
            idx = row * cols + col
            cell = cell_atmosphere[idx]
            dominant = max(cell, key=cell.get)
            intensity = float(cell.get(dominant, 0.0))
            pop = cell_population[idx] if idx < len(cell_population) else 0

            rect = pygame.Rect(int(col * cell_w), int(row * cell_h), max(1, int(cell_w)), max(1, int(cell_h)))
            central_idxs = getattr(world, '_central_cell_idxs', set())
            vis_threshold = 0.001 if idx in central_idxs else 0.01
            if intensity > vis_threshold:
                base_col = EMX_COLORS.get(dominant, WHITE)
                alpha = min(130, int(EMX_CELL_OVERLAY_ALPHA + intensity * 95))
                pygame.draw.rect(grid_layer, (*base_col, alpha), rect)

            pygame.draw.rect(grid_layer, (140, 170, 220, 28), rect, 1)

            if pop > 0:
                short = dominant[:3]
                txt = fonts['tiny'].render(f"{pop}|{short}:{intensity:.2f}", True, (210, 230, 255))
                grid_layer.blit(txt, (rect.x + 3, rect.y + 2))

    surface.blit(grid_layer, (0, 0))


def draw_tuning_panel(surface, ui_state, fonts):
    panel_w, panel_h = 1180, 650
    panel_x = surface.get_width() // 2 - panel_w // 2
    panel_y = surface.get_height() // 2 - panel_h // 2
    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel.fill((8, 10, 20, 238))

    categories = tuning.categories()
    if categories:
        ui_state.tuning_category_idx = max(0, min(len(categories) - 1, ui_state.tuning_category_idx))
    page = tuning.page_for(ui_state.tuning_category_idx)
    if page:
        ui_state.tuning_item_idx = max(0, min(len(page) - 1, ui_state.tuning_item_idx))

    title = fonts["header"].render(
        f"LIVE TUNING | {tuning.category_name(ui_state.tuning_category_idx)}",
        True,
        (180, 220, 255),
    )
    panel.blit(title, (24, 18))
    hint = fonts["small"].render(
        "L close | PgUp/PgDn category | Up/Down select | Left/Right adjust | Shift fast",
        True,
        YELLOW_TEXT,
    )
    panel.blit(hint, (24, 58))

    x_label = 32
    x_amp = 470
    x_value = 700
    x_range = 850
    y = 108
    row_h = 30
    bar_w = 180
    bar_h = 10

    headers = [("Parameter", x_label), ("Amplitude", x_amp), ("Value", x_value), ("Working range", x_range)]
    for label, x in headers:
        panel.blit(fonts["small"].render(label, True, (130, 160, 190)), (x, y))
    y += 28
    pygame.draw.line(panel, (60, 90, 130), (24, y), (panel_w - 24, y), 1)
    y += 10

    max_rows = min(len(page), 16)
    start = 0
    if page and ui_state.tuning_item_idx >= max_rows:
        start = ui_state.tuning_item_idx - max_rows + 1
    visible = page[start:start + max_rows]

    for offset, knob in enumerate(visible):
        idx = start + offset
        selected = idx == ui_state.tuning_item_idx
        row_y = y + offset * row_h
        if selected:
            bg = pygame.Surface((panel_w - 48, row_h - 2), pygame.SRCALPHA)
            bg.fill((35, 55, 85, 210))
            panel.blit(bg, (24, row_y - 3))

        color = (245, 250, 255) if selected else (190, 205, 220)
        amp = knob.amplitude()
        panel.blit(fonts["small"].render(knob.label[:32], True, color), (x_label, row_y))

        pygame.draw.rect(panel, (35, 45, 60), (x_amp, row_y + 8, bar_w, bar_h))
        fill_w = int(bar_w * amp)
        if fill_w > 0:
            bar_col = (70 + int(150 * amp), 190, 120)
            pygame.draw.rect(panel, bar_col, (x_amp, row_y + 8, fill_w, bar_h))
        pygame.draw.rect(panel, (100, 130, 160), (x_amp, row_y + 8, bar_w, bar_h), 1)
        panel.blit(fonts["tiny"].render(f"{amp:.3f}", True, color), (x_amp + bar_w + 10, row_y + 2))

        panel.blit(fonts["small"].render(f"{knob.value():.6g}", True, color), (x_value, row_y))
        panel.blit(
            fonts["tiny"].render(f"{knob.minimum:g} .. {knob.maximum:g} | step {knob.step:g}", True, (150, 165, 180)),
            (x_range, row_y + 3),
        )

    footer = fonts["tiny"].render(
        "Live knobs update imported module values and mutable config dictionaries. Startup-only geometry/population still needs a restart.",
        True,
        (120, 145, 165),
    )
    panel.blit(footer, (24, panel_h - 28))
    pygame.draw.rect(panel, (80, 120, 170), (0, 0, panel_w, panel_h), 2)
    surface.blit(panel, (panel_x, panel_y))


def _snapshot_wrap(text: str, font, max_width: int) -> list[str]:
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


def _snapshot_line_color(text: str) -> tuple[int, int, int]:
    if text.startswith("⚠️"):
        return YELLOW_TEXT
    if text.startswith("✅") or text.startswith("Total:") or text.startswith("Pantheon:"):
        return LIME
    if text.startswith("Active world:") or text.startswith("Snapshot controls:"):
        return (170, 205, 255)
    return WHITE


def _format_zone_distribution(world) -> str:
    zone_counts: dict[str, int] = {}
    for npc in world.npcs:
        zone_counts[npc.zone] = zone_counts.get(npc.zone, 0) + 1
    return " | ".join(f"{zone}:{zone_counts[zone]}" for zone in sorted(zone_counts))


def _format_zone_complexes(world) -> str:
    complexes = []
    zone_complexes = _env_state(world).zone_complexes
    for zone_name in constants.WORK_ZONES:
        complex_data = zone_complexes.get(zone_name)
        if complex_data:
            complexes.append(f"{zone_name}:{str(complex_data.get('name', '?'))}")
    return " | ".join(complexes) if complexes else "No active zone complexes"


def _build_snapshot_lines(world, worlds, ui_state, tick: int) -> list[str]:
    total_npcs = sum(len(item.npcs) for item in worlds)
    total_collapsed = sum(1 for item in worlds for npc in item.npcs if npc.is_collapsing)
    active_events = [event.name for item in worlds for event in item.active_events]
    moving_npcs = sum(1 for npc in world.npcs if npc.state == "TRAVELING")
    working_npcs = sum(1 for npc in world.npcs if npc.state == "AT_WORK")
    home_npcs = sum(1 for npc in world.npcs if npc.state == "AT_HOME")

    sim_state = _sim_state(world)
    doctrine = sim_state.doctrine
    doctrine_name = doctrine.active or "None"
    doctrine_pressure = max(doctrine.pressure.items(), key=lambda item: item[1], default=("None", 0.0))
    current_tier_name = PANTHEON_TIERS[min(PANTHEON.tier, len(PANTHEON_TIERS) - 1)]["name"]

    lines = [
        constants.describe_weather_preset(),
        *constants.describe_factor_profiles(),
        "",
        f"Total: {len(worlds)} territories, {total_npcs} NPCs | tick {tick} | paused {'YES' if ui_state.paused else 'NO'}",
        f"Active world: {world.name} ({ui_state.current_world_idx + 1}/{len(worlds)}) | home {home_npcs} | working {working_npcs} | traveling {moving_npcs}",
        f"Collapsed across simulation: {total_collapsed} | active events: {len(active_events)}",
        (
            f"Pantheon: tier {PANTHEON.tier} {current_tier_name} | "
            f"material {PANTHEON.material_progress * 100:.1f}% | "
            f"energy {PANTHEON.energy_progress * 100:.1f}% | "
            f"contributors {PANTHEON.total_contributors}"
        ),
        (
            f"Doctrine: active {doctrine_name} | "
            f"strongest pressure {doctrine_pressure[0]} {doctrine_pressure[1]:.1f}"
        ),
        f"Complexes: {_format_zone_complexes(world)}",
        f"Events: {' | '.join(active_events[:6]) if active_events else 'None'}",
        "",
    ]

    for item in worlds:
        wealth = sum(npc.money for npc in item.npcs)
        active = sum(1 for npc in item.npcs if npc.state != "AT_HOME")
        collapsed = sum(1 for npc in item.npcs if npc.is_collapsing)
        lines.append(
            f"Territory: '{item.name}' | NPCs {len(item.npcs)} | active {active} | collapsed {collapsed} | wealth ${wealth:.0f}"
        )
        lines.append(f"  Zones: {_format_zone_distribution(item)}")

    lines.extend([
        "",
        "Snapshot controls: X close | TAB switch active world | C/E/V/L open specialist panels",
    ])
    return lines


def draw_game_snapshot_panel(surface, world, worlds, ui_state, fonts, tick):
    panel_w, panel_h = 1240, 740
    panel_x = surface.get_width() // 2 - panel_w // 2
    panel_y = surface.get_height() // 2 - panel_h // 2
    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel.fill((10, 12, 24, 240))

    title = fonts["header"].render("SYSTEM SNAPSHOT", True, LIME)
    subtitle = fonts["small"].render(
        f"{world.name} | live simulation overview",
        True,
        (170, 205, 255),
    )
    panel.blit(title, (24, 16))
    panel.blit(subtitle, (24, 54))

    lines = _build_snapshot_lines(world, worlds, ui_state, tick)
    text_font = fonts["tiny"]
    text_width = panel_w - 48
    y = 92
    for text in lines:
        if not text:
            y += 8
            continue
        color = _snapshot_line_color(text)
        for wrapped in _snapshot_wrap(text, text_font, text_width):
            line = text_font.render(wrapped, True, color)
            panel.blit(line, (24, y))
            y += line.get_height() + 4
            if y > panel_h - 34:
                break
        if y > panel_h - 34:
            break

    pygame.draw.rect(panel, (80, 120, 170), (0, 0, panel_w, panel_h), 2)
    surface.blit(panel, (panel_x, panel_y))


def draw_controls_panel(surface, fonts):
    panel_w, panel_h = 980, 700
    panel_x = surface.get_width() // 2 - panel_w // 2
    panel_y = surface.get_height() // 2 - panel_h // 2
    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel.fill((10, 12, 24, 242))

    title = fonts["header"].render("CONTROLS", True, LIME)
    subtitle = fonts["small"].render("Active keys and their functions", True, (170, 205, 255))
    panel.blit(title, (24, 16))
    panel.blit(subtitle, (24, 54))

    sections = [
        ("Core", [
            "SPACE  - Pause / resume simulation",
            "TAB    - Switch active territory",
            "MOUSE  - Click edge tabs to switch territory or view",
            "WHEEL  - Scroll vertically when the window is shorter than the canvas",
            "DRAG   - Bottom scrollbar pans horizontally; right scrollbar pans vertically",
            "ESC    - Exit simulation",
        ]),
        ("Panels", [
            "M      - Toggle this controls panel",
            "X      - Toggle system snapshot overlay",
            "I      - Statistics dashboard",
            "B      - NPC inspector",
            "E      - EMX panel",
            "V      - Weather panel",
            "L      - Live tuning panel",
        ]),
        ("Zone views", [
            "C      - CENTRAL detail view",
            "S      - SCIENCE detail view",
            "T      - TRADE detail view",
            "D      - DEVELOPMENT detail view",
            "F      - FLEX detail view",
            "H      - HOME detail view",
            "P      - PANTHEON detail view",
        ]),
        ("State and presets", [
            "U      - Cycle weather preset",
            "G      - Toggle cell grid overlay",
            "5      - Save snapshot",
            "6      - Load snapshot",
        ]),
        ("Navigation", [
            "PAGE UP / PAGE DOWN - Change page in inspector, stats, EMX, or tuning category",
            "HOME / END          - Jump to first/last page in stats or first/last tuning item",
            "0 / 1 / 2 / 3 / 4   - Filter inspector zone",
            "UP / DOWN           - Move selection in live tuning",
            "LEFT / RIGHT        - Adjust live tuning value",
            "SHIFT + LEFT/RIGHT  - Fast tuning adjust",
        ]),
    ]

    y = 98
    for section_title, lines in sections:
        section = fonts["small"].render(section_title, True, YELLOW_TEXT)
        panel.blit(section, (24, y))
        y += section.get_height() + 8
        for line in lines:
            text = fonts["tiny"].render(line, True, WHITE)
            panel.blit(text, (44, y))
            y += text.get_height() + 6
        y += 10

    footer = fonts["tiny"].render(
        "Tip: M closes this help, and overlays can stay open together when useful.",
        True,
        (130, 160, 185),
    )
    panel.blit(footer, (24, panel_h - 28))
    pygame.draw.rect(panel, (80, 120, 170), (0, 0, panel_w, panel_h), 2)
    surface.blit(panel, (panel_x, panel_y))


def _compact_world_tab_label(name: str, index: int) -> str:
    cleaned = (name or "").replace("-", " ").strip()
    if cleaned:
        parts = [part for part in cleaned.split() if part]
        if parts:
            return parts[-1].upper()
    return f"W{index + 1}"


def _view_tab_color(zone_name: str | None) -> tuple[int, int, int]:
    if zone_name is None:
        return (220, 220, 220)
    return {
        "SCIENCE": (100, 200, 255),
        "TRADE": TRADE_COLOR,
        "DEVELOPMENT": DEV_COLOR,
        "FLEX": FLEX_COLOR,
        "HOME": (235, 220, 130),
        "CENTRAL": _CENTRAL_COL,
        "PANTHEON": (214, 190, 120),
    }.get(zone_name, WHITE)


def _build_vertical_edge_tabs(surface_size: tuple[int, int], entries, *, side: str, fonts):
    text_font = fonts["small"]
    tab_width = 34
    outer_margin = 8
    spacing = 6
    tab_specs = []
    total_height = 0

    for entry in entries:
        text_surface = text_font.render(entry["label"], True, entry["text_color"])
        rotated = pygame.transform.rotate(text_surface, 90 if side == "left" else -90)
        rect = pygame.Rect(0, 0, tab_width, rotated.get_height() + 18)
        tab_specs.append(
            {
                **entry,
                "label_surface": rotated,
                "rect": rect,
            }
        )
        total_height += rect.height

    if not tab_specs:
        return []

    total_height += spacing * (len(tab_specs) - 1)
    start_y = max(84, (surface_size[1] - total_height) // 2)

    for spec in tab_specs:
        rect = spec["rect"]
        rect.y = start_y
        rect.x = outer_margin if side == "left" else surface_size[0] - outer_margin - rect.width
        start_y += rect.height + spacing

    return tab_specs


def _build_edge_tabs(surface_size: tuple[int, int], worlds, ui_state, fonts):
    world_entries = []
    for index, world in enumerate(worlds):
        active = ui_state.current_world_idx == index
        world_entries.append(
            {
                "label": _compact_world_tab_label(getattr(world, "name", ""), index),
                "fill": (36, 46, 66, 218) if active else (18, 24, 38, 190),
                "border": LIME if active else (94, 116, 152),
                "text_color": WHITE if active else (205, 215, 230),
                "action": ("world", index),
            }
        )

    view_entries = []
    for zone_name, label in [
        (None, "MAP"),
        ("SCIENCE", "SCIENCE"),
        ("TRADE", "TRADE"),
        ("DEVELOPMENT", "DEVELOPMENT"),
        ("FLEX", "FLEX"),
        ("HOME", "HOME"),
        ("CENTRAL", "CENTRAL"),
        ("PANTHEON", "PANTHEON"),
    ]:
        active = ui_state.focused_zone == zone_name if zone_name is not None else ui_state.focused_zone is None
        accent = _view_tab_color(zone_name)
        view_entries.append(
            {
                "label": label,
                "fill": (*accent, 86) if active else (24, 20, 34, 204),
                "border": accent,
                "text_color": WHITE if active else accent,
                "action": ("view", zone_name),
            }
        )

    return [
        *_build_vertical_edge_tabs(surface_size, world_entries, side="left", fonts=fonts),
        *_build_vertical_edge_tabs(surface_size, view_entries, side="right", fonts=fonts),
    ]


def draw_edge_tabs(surface, worlds, ui_state, fonts) -> None:
    for tab in _build_edge_tabs(surface.get_size(), worlds, ui_state, fonts):
        rect = tab["rect"]
        pygame.draw.rect(surface, tab["fill"], rect, border_radius=10)
        pygame.draw.rect(surface, tab["border"], rect, width=2, border_radius=10)
        label_surface = tab["label_surface"]
        surface.blit(
            label_surface,
            (
                rect.x + (rect.width - label_surface.get_width()) // 2,
                rect.y + (rect.height - label_surface.get_height()) // 2,
            ),
        )


def build_horizontal_scrollbar_metrics(
    *,
    logical_size: tuple[int, int],
    display_size: tuple[int, int],
    scroll_x: int,
):
    display_width, display_height = display_size
    logical_width, logical_height = logical_size
    if display_width <= 0 or display_height <= 0:
        return None

    max_scroll_x = max(0, logical_width - display_width)
    if max_scroll_x <= 0:
        return None

    margin_x = 14
    bottom_margin = 8
    track_height = 16
    reserve_right = track_height + 6 if logical_height > display_height else 0
    track_width = max(96, display_width - margin_x * 2 - reserve_right)
    track_rect = pygame.Rect(
        margin_x,
        max(6, display_height - bottom_margin - track_height),
        track_width,
        track_height,
    )

    thumb_width = max(52, int(round(track_rect.width * (display_width / logical_width))))
    thumb_width = min(track_rect.width, thumb_width)
    travel_width = max(0, track_rect.width - thumb_width)
    clamped_scroll_x = max(0, min(int(scroll_x), max_scroll_x))
    thumb_left = track_rect.x
    if travel_width > 0 and max_scroll_x > 0:
        thumb_left += int(round((clamped_scroll_x / max_scroll_x) * travel_width))

    thumb_rect = pygame.Rect(
        thumb_left,
        track_rect.y + 2,
        thumb_width,
        max(8, track_rect.height - 4),
    )
    return {
        "track_rect": track_rect,
        "thumb_rect": thumb_rect,
        "max_scroll_x": max_scroll_x,
        "travel_width": travel_width,
    }


def build_vertical_scrollbar_metrics(
    *,
    logical_size: tuple[int, int],
    display_size: tuple[int, int],
    scroll_y: int,
):
    display_width, display_height = display_size
    logical_width, logical_height = logical_size
    if display_width <= 0 or display_height <= 0:
        return None

    max_scroll_y = max(0, logical_height - display_height)
    if max_scroll_y <= 0:
        return None

    margin_y = 14
    right_margin = 8
    track_width = 16
    reserve_bottom = track_width + 6 if logical_width > display_width else 0
    track_height = max(96, display_height - margin_y * 2 - reserve_bottom)
    track_rect = pygame.Rect(
        max(6, display_width - right_margin - track_width),
        margin_y,
        track_width,
        track_height,
    )

    thumb_height = max(52, int(round(track_rect.height * (display_height / logical_height))))
    thumb_height = min(track_rect.height, thumb_height)
    travel_height = max(0, track_rect.height - thumb_height)
    clamped_scroll_y = max(0, min(int(scroll_y), max_scroll_y))
    thumb_top = track_rect.y
    if travel_height > 0 and max_scroll_y > 0:
        thumb_top += int(round((clamped_scroll_y / max_scroll_y) * travel_height))

    thumb_rect = pygame.Rect(
        track_rect.x + 2,
        thumb_top,
        max(8, track_rect.width - 4),
        thumb_height,
    )
    return {
        "track_rect": track_rect,
        "thumb_rect": thumb_rect,
        "max_scroll_y": max_scroll_y,
        "travel_height": travel_height,
    }


def horizontal_scroll_from_thumb_left(thumb_left: int, metrics) -> int:
    travel_width = int(metrics.get("travel_width", 0))
    max_scroll_x = int(metrics.get("max_scroll_x", 0))
    track_rect = metrics["track_rect"]
    thumb_rect = metrics["thumb_rect"]
    min_left = track_rect.x
    max_left = track_rect.right - thumb_rect.width
    clamped_left = max(min_left, min(int(thumb_left), max_left))
    if travel_width <= 0 or max_scroll_x <= 0:
        return 0
    ratio = (clamped_left - min_left) / travel_width
    return int(round(ratio * max_scroll_x))


def vertical_scroll_from_thumb_top(thumb_top: int, metrics) -> int:
    travel_height = int(metrics.get("travel_height", 0))
    max_scroll_y = int(metrics.get("max_scroll_y", 0))
    track_rect = metrics["track_rect"]
    thumb_rect = metrics["thumb_rect"]
    min_top = track_rect.y
    max_top = track_rect.bottom - thumb_rect.height
    clamped_top = max(min_top, min(int(thumb_top), max_top))
    if travel_height <= 0 or max_scroll_y <= 0:
        return 0
    ratio = (clamped_top - min_top) / travel_height
    return int(round(ratio * max_scroll_y))


def draw_viewport_scrollbars(
    surface,
    *,
    logical_size: tuple[int, int],
    display_size: tuple[int, int],
    scroll_x: int,
    scroll_y: int,
    horizontal_dragging: bool = False,
    vertical_dragging: bool = False,
) -> None:
    horizontal = build_horizontal_scrollbar_metrics(
        logical_size=logical_size,
        display_size=display_size,
        scroll_x=scroll_x,
    )
    vertical = build_vertical_scrollbar_metrics(
        logical_size=logical_size,
        display_size=display_size,
        scroll_y=scroll_y,
    )

    if horizontal is not None:
        track_rect = horizontal["track_rect"]
        thumb_rect = horizontal["thumb_rect"]
        pygame.draw.rect(surface, (16, 20, 30, 220), track_rect, border_radius=8)
        pygame.draw.rect(surface, (90, 104, 128, 235), track_rect, width=1, border_radius=8)
        thumb_fill = (220, 228, 240, 245) if horizontal_dragging else (184, 194, 214, 235)
        pygame.draw.rect(surface, thumb_fill, thumb_rect, border_radius=7)
        pygame.draw.rect(surface, (70, 84, 106, 235), thumb_rect, width=1, border_radius=7)

    if vertical is not None:
        track_rect = vertical["track_rect"]
        thumb_rect = vertical["thumb_rect"]
        pygame.draw.rect(surface, (16, 20, 30, 220), track_rect, border_radius=8)
        pygame.draw.rect(surface, (90, 104, 128, 235), track_rect, width=1, border_radius=8)
        thumb_fill = (220, 228, 240, 245) if vertical_dragging else (184, 194, 214, 235)
        pygame.draw.rect(surface, thumb_fill, thumb_rect, border_radius=7)
        pygame.draw.rect(surface, (70, 84, 106, 235), thumb_rect, width=1, border_radius=7)


def resolve_edge_tab_action(
    display_pos,
    *,
    logical_size: tuple[int, int],
    display_size: tuple[int, int] | None = None,
    viewport_origin: tuple[int, int] = (0, 0),
    worlds,
    ui_state,
    fonts,
):
    logical_pos = (
        int(display_pos[0]) + int(viewport_origin[0]),
        int(display_pos[1]) + int(viewport_origin[1]),
    )

    for tab in _build_edge_tabs(logical_size, worlds, ui_state, fonts):
        if tab["rect"].collidepoint(logical_pos):
            return tab["action"]
    return None


def render_view(screen, packet: FrameRenderPacket, overlay_surface=None):
    """Main rendering orchestrator - dispatches to specific draw functions"""
    world = packet.world
    worlds = packet.worlds
    ui_state = packet.ui_state
    fonts = packet.fonts
    tick = packet.tick
    anchors = packet.anchors
    render_state = _runtime_state(world).render_state
    overlay_target = overlay_surface if packet.gpu_primitives_enabled and overlay_surface is not None else screen
    if ui_state.focused_zone:
        from .api import draw_zone_detail
        draw_zone_detail(
            screen if not packet.gpu_rect_primitives else overlay_target,
            world,
            ui_state.focused_zone,
            fonts,
            tick,
            draw_background=not bool(packet.gpu_rect_primitives),
        )
        
        # Overlay panels that should remain visible in detail view
        if ui_state.show_tuning_panel:
            draw_tuning_panel(overlay_target, ui_state, fonts)
        if ui_state.show_game_snapshot_panel:
            draw_game_snapshot_panel(overlay_target, world, worlds, ui_state, fonts, tick)
        if ui_state.show_controls_panel:
            draw_controls_panel(overlay_target, fonts)
        draw_edge_tabs(overlay_target, worlds, ui_state, fonts)
        return

    if not packet.gpu_primitives_enabled:
        draw_anchor_fields(screen, world, anchors, world.zone_stats, tick)  # dynamic fields, bottom layer
    draw_anchors(screen, anchors, fonts['small'], world.zone_stats)
    if packet.gpu_primitives_enabled:
        draw_atmosphere_indicators(overlay_target, world, anchors, fonts)
    else:
        draw_atmosphere(screen, world, anchors, fonts)  # EMX halos behind NPCs
    draw_cell_grid_overlay(screen, world, fonts, ui_state.show_cell_grid)

    # Draw NPCs on main map
    if not packet.gpu_primitives_enabled:
        draw_npcs(screen, world, anchors, fonts, tick)

    # Draw corner panels (always visible)
    draw_corner_panels(overlay_target, world, fonts, tick)

    if ui_state.show_emx_panel:
        draw_emx_panel(overlay_target, world, ui_state, fonts)

    if ui_state.show_weather_panel:
        draw_weather_panel(overlay_target, world, ui_state, fonts)

    if ui_state.show_statistics:
        draw_statistics_dashboard(
            overlay_target, world, world.zone_stats, fonts, ui_state
        )

    if ui_state.show_npc_inspector:
        draw_npc_inspector(
            overlay_target, world, ui_state.inspector_zone_idx, fonts, world.zone_stats, ui_state, world.active_events
        )

    if ui_state.show_combined_view:
        render_central.draw_central_view(overlay_target, worlds, fonts, tick, worlds[ui_state.current_world_idx])

    if ui_state.show_tuning_panel:
        draw_tuning_panel(overlay_target, ui_state, fonts)

    if ui_state.show_game_snapshot_panel:
        draw_game_snapshot_panel(screen, world, worlds, ui_state, fonts, tick)

    if ui_state.show_controls_panel:
        draw_controls_panel(screen, fonts)

    if ui_state.show_load_menu:
        draw_load_menu(screen, ui_state, fonts)

    draw_edge_tabs(overlay_target, worlds, ui_state, fonts)


def draw_load_menu(screen, ui_state, fonts):
    """Multi-slot load interface inspired by training subfolder configuration."""
    from persistence.api import get_slot_status

    panel_w, panel_h = 500, 320
    panel_x = (WIDTH - panel_w) // 2
    panel_y = (HEIGHT - panel_h) // 2

    overlay = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    overlay.fill((20, 20, 30, 230))
    pygame.draw.rect(overlay, ORANGE, (0, 0, panel_w, panel_h), width=3, border_radius=15)

    title = fonts["header"].render("LOAD SIMULATION STATE", True, ORANGE)
    overlay.blit(title, (panel_w // 2 - title.get_width() // 2, 25))

    for i in range(1, 4):
        status = get_slot_status(i)
        color = WHITE if status != "EMPTY" else (120, 120, 120)
        
        slot_text = fonts["small"].render(f"SLOT {i}: {status}", True, color)
        y_pos = 100 + (i - 1) * 50
        overlay.blit(slot_text, (60, y_pos))
        
        # Indicator for the active save slot (mapped to 'Q' and '5')
        if i == ui_state.active_save_slot:
            pygame.draw.circle(overlay, LIME, (40, y_pos + slot_text.get_height() // 2), 6)

    footer = fonts["tiny"].render("PRESS 1, 2, or 3 TO LOAD | 6 or ESC: CLOSE", True, YELLOW_TEXT)
    overlay.blit(footer, (panel_w // 2 - footer.get_width() // 2, panel_h - 40))

    screen.blit(overlay, (panel_x, panel_y))
