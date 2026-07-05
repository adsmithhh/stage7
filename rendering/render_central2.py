from __future__ import annotations

import math
import random
import pygame

from config.constants import (
    ALL_ZONES,
    EMX_EMOTIONS,
    HEIGHT,
    WIDTH,
    WHITE,
)
from emx.api import (
    cancel_tick,
    compute_composites,
    compute_neutral_score,
    dominant_region,
    region_attrs,
    zero_composites,
    COMPOSITE_COLORS,
    EMERGE_RATE,
    EMERGE_THRESHOLD,
    TIER_MAP,
    PRIMARY_COMPOSITES,
    SECONDARY_COMPOSITES,
    TERTIARY_COMPOSITES,
)
from .render_types import RectPrimitive, RenderState

# Injected by jj.py after pygame init
ANCHORS: dict = {}


def configure_runtime(*, anchors: dict | None = None) -> None:
    global ANCHORS
    if anchors is not None:
        ANCHORS = anchors


def reset_runtime_state() -> None:
    """Note: State is now owned by AppContext and reset via render_state.clear()"""
    pass

# ── CELL PSI CONSTANTS ────────────────────────────────────────────────────────
_COMPOSITE_BASE_DECAY = 1.00
_SAT_THRESHOLD        = 100.0
_SAT_DECAY_FLOOR      = 0.06
_RESIDUAL_FLOOR       = 300.0
_ABSOLUTE_FLOOR       = 50.0
_CRYSTAL_THRESHOLD    = 120.0

# ── SATURATION ANCHOR CONSTANTS ───────────────────────────────────────────────
ANCHOR_DURATION    = 90       # ticks the anchor persists after firing
ANCHOR_RADIUS      = 4.0      # cell-distance radius of emission
ANCHOR_BASE_EMIT   = 0.06     # uniform per-emotion strength per tick at origin
ANCHOR_EMIT_SCALE  = 3.0      # 3x stronger than normal (applied to ANCHOR_BASE_EMIT)
ANCHOR_RESET_SEED  = 0.0      # composites reset to absolute floor * this factor

# ── GRID / LAYOUT CONSTANTS ───────────────────────────────────────────────────
GRID_COLS   = 16
GRID_ROWS   = 10
CAPTURE_R   = 22
HEADER_H    = 56
FOOTER_H    = 28
DOCTRINE_H  = 110
COMPOSITE_H = 110

DETAIL_MARGIN_X = 36
DETAIL_MARGIN_Y = 76
DETAIL_FOOTER_H = 170


# ── EMOTIONAL MATH ────────────────────────────────────────────────────────────

def _emotional_math_tick(
    state_key: tuple,
    cells_emx: list,
    cells_pop: list,
    cols: int,
    rows: int,
    render_state: RenderState
) -> tuple:
    """
    Persistent composite accumulation per cell using injected RenderState.
    Returns (psi_cells, overflow_indices).
    """
    cell_count = cols * rows

    cache = render_state.composite_cell_cache.get(state_key)
    if cache is None or len(cache) != cell_count:
        cache = [zero_composites() for _ in range(cell_count)]
        render_state.composite_cell_cache[state_key] = cache

    result          = []
    overflow_indices = []

    for idx in range(cell_count):
        cell_base = cells_emx[idx]
        pop       = cells_pop[idx]
        comp      = cache[idx]

        # 1. PAD-driven psi
        if pop > 0:
            snapshot = compute_composites(cell_base)
            for name, value in snapshot.items():
                if value > EMERGE_THRESHOLD * 0.35:
                    comp[name] += value * pop * EMERGE_RATE[TIER_MAP[name]]
            cancel_tick(comp)

        # 3. Saturation-dependent decay
        psi = sum(comp.values())
        if psi > _SAT_THRESHOLD:
            sat_ratio = min(1.0, (psi - _SAT_THRESHOLD) / (1.0 - _SAT_THRESHOLD + 0.001))
            decay = _COMPOSITE_BASE_DECAY * (1.0 - sat_ratio * (1.0 - _SAT_DECAY_FLOOR))
        else:
            decay = _COMPOSITE_BASE_DECAY

        for name in comp:
            comp[name] *= decay

        # 4. Residual floor at high saturation
        if psi > _SAT_THRESHOLD:
            for name in comp:
                if comp[name] > 0.005:
                    comp[name] = max(comp[name], _RESIDUAL_FLOOR)

        # 5. Absolute floor
        for name in comp:
            if comp[name] < _ABSOLUTE_FLOOR:
                comp[name] = 0.0

        # 6. CRYSTALLIZATION TRIGGER
        if psi > _CRYSTAL_THRESHOLD:
            overflow_indices.append(idx)

        result.append(comp.copy())

    return result, overflow_indices


def _apply_saturation_anchors(
    state_key: tuple,
    tick: int,
    composites: list,
    overflow_idxs: list,
    cols: int,
    rows: int,
    render_state: RenderState
):
    """
    Manages active saturation anchors and their propagation using injected RenderState.
    """
    active = render_state.saturation_anchors.get(state_key, [])

    # 1. Spawn new anchors
    for idx in overflow_idxs:
        active.append({'idx': idx, 'birth': tick})
        # Immediate reset of origin cell
        composites[idx] = zero_composites()
        for k in composites[idx]:
            composites[idx][k] = _ABSOLUTE_FLOOR * ANCHOR_RESET_SEED

        # Reset the persistent cache value too
        if state_key in render_state.composite_cell_cache:
            render_state.composite_cell_cache[state_key][idx] = composites[idx].copy()

    # 2. Cleanup expired
    active = [a for a in active if tick - a['birth'] < ANCHOR_DURATION]
    render_state.saturation_anchors[state_key] = active

    # 3. Propagate emission
    for a in active:
        o_idx = a['idx']
        o_col, o_row = o_idx % cols, o_idx // cols
        
        age = tick - a['birth']
        pulse = 1.0 - (age / ANCHOR_DURATION)
        
        r_int = int(ANCHOR_RADIUS)
        for dr in range(-r_int, r_int + 1):
            for dc in range(-r_int, r_int + 1):
                tr, tc = o_row + dr, o_col + dc
                if 0 <= tr < rows and 0 <= tc < cols:
                    dist = math.sqrt(dr*dr + dc*dc)
                    if 0 < dist <= ANCHOR_RADIUS:
                        t_idx = tr * cols + tc
                        falloff = (1.0 - dist / ANCHOR_RADIUS) * pulse * ANCHOR_BASE_EMIT * ANCHOR_EMIT_SCALE
                        for name in composites[t_idx]:
                            composites[t_idx][name] += falloff
                            # Halve neighbors on first hit
                            if age == 0:
                                composites[t_idx][name] *= 0.5


def update_field_logic(
    current_world,
    global_tick,
    zone_name: str,
):
    """
    Perform mathematical updates for a zone's emotional field using world-scoped runtime state.
    """
    render_state = current_world.runtime_state.render_state
    source_world = current_world
    zone_npcs = []
    for npc in source_world.npcs:
        if npc.state == "AT_WORK" and npc.zone == zone_name:
            zone_npcs.append(npc)

    world_id    = id(source_world) if source_world else 0
    state_key   = (world_id, zone_name)
    cx_map, cy_map = ANCHORS.get(zone_name, (WIDTH // 2, HEIGHT // 2))

    cells_emx = [{e: 0.0 for e in EMX_EMOTIONS} for _ in range(GRID_COLS * GRID_ROWS)]
    cells_pop = [0] * (GRID_COLS * GRID_ROWS)
    _prev_cache = render_state.composite_cell_cache.get(state_key, [])

    npc_cancel = {}
    for i, npc in enumerate(zone_npcs):
        rx = (npc.x - cx_map + CAPTURE_R) / (2.0 * CAPTURE_R)
        ry = (npc.y - cy_map + CAPTURE_R) / (2.0 * CAPTURE_R)
        rx = max(0.0, min(0.9999, rx))
        ry = max(0.0, min(0.9999, ry))
        col = int(rx * GRID_COLS)
        row = int(ry * GRID_ROWS)
        idx = row * GRID_COLS + col

        cancel_factor = 1.0
        if _prev_cache and idx < len(_prev_cache):
            cell_psi = sum(_prev_cache[idx].values())
            if cell_psi > _SAT_THRESHOLD:
                sat_ratio    = min(1.0, (cell_psi - _SAT_THRESHOLD) / max(1.0, _SAT_THRESHOLD))
                cancel_factor = max(0.05, 1.0 - sat_ratio * 0.90)

        npc_cancel[i] = cancel_factor
        cells_pop[idx] += 1
        for e in EMX_EMOTIONS:
            cells_emx[idx][e] += float(npc.emx.get(e, 0.0)) * cancel_factor

    cells_emx_norm = [
        ({e: cells_emx[i][e] / cells_pop[i] for e in EMX_EMOTIONS} if cells_pop[i] > 0
         else {e: 0.0 for e in EMX_EMOTIONS})
        for i in range(GRID_COLS * GRID_ROWS)
    ]

    composites, overflows = _emotional_math_tick(
        state_key, cells_emx_norm, cells_pop, GRID_COLS, GRID_ROWS, render_state
    )
    
    _apply_saturation_anchors(
        state_key, global_tick, composites, overflows, GRID_COLS, GRID_ROWS, render_state
    )

    # Cache for rendering phase
    render_state.field_render_cache[state_key] = {
        'composites': composites,
        'npc_cancel': npc_cancel,
        'tick': global_tick
    }


def draw_zone_detail(surface, world, zone_name, fonts, tick, *, draw_background: bool = True):
    """Visualizes the emotional field using world-scoped RenderState."""
    render_state = world.runtime_state.render_state
    world_id = id(world)
    state_key = (world_id, zone_name)
    data = render_state.field_render_cache.get(state_key)
    if not data:
        _draw_missing_detail(surface, zone_name, fonts)
        return

    composites = data['composites']

    _draw_zone_detail(surface, zone_name, tick, composites, fonts, draw_background=draw_background)


def build_zone_detail_rect_primitives(world, zone_name: str) -> list[RectPrimitive]:
    render_state = world.runtime_state.render_state
    state_key = (id(world), zone_name)
    data = render_state.field_render_cache.get(state_key)
    if not data:
        return []

    return build_zone_detail_rect_primitives_from_composites(data["composites"])


def build_zone_detail_rect_primitives_from_composites(composites: list[dict[str, float]]) -> list[RectPrimitive]:
    grid_x, grid_y, grid_w, grid_h = _zone_detail_grid_bounds()
    cell_w = grid_w / GRID_COLS
    cell_h = grid_h / GRID_ROWS
    primitives: list[RectPrimitive] = []

    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            idx = row * GRID_COLS + col
            comp = composites[idx]
            dominant_name, total, color = _composite_visual(comp)
            rect_color = color if dominant_name is not None else (20, 24, 34)
            alpha = 38 if dominant_name is None else min(220, max(70, int(60 + total * 0.30)))
            primitives.append(
                RectPrimitive(
                    x=grid_x + (col * cell_w),
                    y=grid_y + (row * cell_h),
                    width=max(1.0, cell_w - 1.0),
                    height=max(1.0, cell_h - 1.0),
                    color=(*rect_color, alpha),
                )
            )
    return primitives


def draw_zone_detail_overlay(surface, world, zone_name, fonts, tick):
    render_state = world.runtime_state.render_state
    state_key = (id(world), zone_name)
    data = render_state.field_render_cache.get(state_key)
    if not data:
        _draw_missing_detail(surface, zone_name, fonts)
        return

    _draw_zone_detail(surface, zone_name, tick, data["composites"], fonts, draw_background=False)


def _zone_detail_grid_bounds() -> tuple[int, int, int, int]:
    grid_x = DETAIL_MARGIN_X
    grid_y = DETAIL_MARGIN_Y
    grid_w = WIDTH - (DETAIL_MARGIN_X * 2)
    grid_h = HEIGHT - DETAIL_MARGIN_Y - DETAIL_FOOTER_H
    return grid_x, grid_y, grid_w, grid_h


def _composite_visual(comp: dict[str, float]) -> tuple[str | None, float, tuple[int, int, int]]:
    total = float(sum(comp.values()))
    if total <= 0.01:
        return None, total, WHITE
    dominant_name = max(comp, key=comp.get)
    return dominant_name, total, COMPOSITE_COLORS.get(dominant_name, WHITE)


def _aggregate_composites(composites: list[dict[str, float]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for comp in composites:
        for name, value in comp.items():
            totals[name] = totals.get(name, 0.0) + float(value)
    return totals


def _tier_names(entries: list[tuple[str, str, str]]) -> str:
    return ", ".join(name for name, _, _ in entries)


def _draw_missing_detail(surface, zone_name: str, fonts) -> None:
    title = fonts["header"].render(f"CENTRAL 2 FIELD DETAIL - {zone_name}", True, WHITE)
    note = fonts["small"].render("No cached field data yet for this zone.", True, (180, 190, 220))
    surface.blit(title, (DETAIL_MARGIN_X, 20))
    surface.blit(note, (DETAIL_MARGIN_X, 50))


def _draw_zone_detail(surface, zone_name: str, tick: int, composites: list[dict[str, float]], fonts, *, draw_background: bool) -> None:
    grid_x, grid_y, grid_w, grid_h = _zone_detail_grid_bounds()
    cell_w = grid_w / GRID_COLS
    cell_h = grid_h / GRID_ROWS

    if draw_background:
        panel = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        panel.fill((8, 10, 18, 245))
        surface.blit(panel, (0, 0))
        for primitive in build_zone_detail_rect_primitives_from_composites(composites):
            pygame.draw.rect(
                surface,
                primitive.color,
                pygame.Rect(int(primitive.x), int(primitive.y), int(primitive.width), int(primitive.height)),
            )

    totals = _aggregate_composites(composites)
    dominant_name = max(totals, key=totals.get) if totals else None
    dominant_region_name = dominant_region(totals) if totals else None
    neutral_score = compute_neutral_score(totals) if totals else 0.0
    top_composites = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:5]

    title = fonts["header"].render(f"CENTRAL 2 FIELD DETAIL - {zone_name}", True, (160, 230, 255))
    subtitle = fonts["small"].render(
        f"tick={tick} | dominant={dominant_name or '-'} | region={dominant_region_name or 'neutral'} | neutral={neutral_score:.2f}",
        True,
        WHITE,
    )
    surface.blit(title, (DETAIL_MARGIN_X, 18))
    surface.blit(subtitle, (DETAIL_MARGIN_X, 48))

    for row in range(GRID_ROWS + 1):
        y = int(grid_y + (row * cell_h))
        pygame.draw.line(surface, (70, 95, 130, 120), (grid_x, y), (grid_x + grid_w, y), 1)
    for col in range(GRID_COLS + 1):
        x = int(grid_x + (col * cell_w))
        pygame.draw.line(surface, (70, 95, 130, 120), (x, grid_y), (x, grid_y + grid_h), 1)

    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            idx = row * GRID_COLS + col
            comp = composites[idx]
            dominant_cell, total, color = _composite_visual(comp)
            if dominant_cell is None:
                continue
            short = dominant_cell[:4].upper()
            label = fonts["tiny"].render(short, True, color)
            label_x = int(grid_x + (col * cell_w) + 4)
            label_y = int(grid_y + (row * cell_h) + 2)
            surface.blit(label, (label_x, label_y))

    footer_y = grid_y + grid_h + 18
    footer_panel = pygame.Surface((grid_w, DETAIL_FOOTER_H - 24), pygame.SRCALPHA)
    footer_panel.fill((12, 16, 26, 210))
    pygame.draw.rect(footer_panel, (80, 110, 150, 220), footer_panel.get_rect(), 2)

    top_title = fonts["small"].render("Top composites", True, (255, 220, 140))
    footer_panel.blit(top_title, (18, 14))
    for index, (name, value) in enumerate(top_composites):
        color = COMPOSITE_COLORS.get(name, WHITE)
        line = fonts["tiny"].render(f"{index + 1}. {name} = {value:.1f}", True, color)
        footer_panel.blit(line, (18, 42 + (index * 22)))

    region_title = fonts["small"].render("Region + tiers", True, (160, 220, 255))
    footer_panel.blit(region_title, (340, 14))
    region_line = fonts["tiny"].render(
        f"Dominant region: {dominant_region_name or 'neutral'} | attrs: {', '.join(region_attrs(dominant_region_name)) if dominant_region_name else '-'}",
        True,
        WHITE,
    )
    footer_panel.blit(region_line, (340, 42))
    tier_line = fonts["tiny"].render(
        f"Primary: {_tier_names(PRIMARY_COMPOSITES)} | Secondary: {_tier_names(SECONDARY_COMPOSITES)} | Tertiary: {_tier_names(TERTIARY_COMPOSITES)}",
        True,
        (200, 210, 235),
    )
    footer_panel.blit(tier_line, (340, 68))

    neutral_line = fonts["tiny"].render(
        f"Cell count: {len(composites)} | neutral score: {neutral_score:.2f}",
        True,
        (180, 200, 220),
    )
    footer_panel.blit(neutral_line, (340, 94))

    surface.blit(footer_panel, (grid_x, footer_y))
