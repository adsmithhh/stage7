from __future__ import annotations

import math
from typing import Dict, List, Tuple

from config.constants import EMX_COLORS, EMX_EMOTIONS, EMX_OVERRIDES, EMX_PAD
from config.emotion_runtime import apply_pad_override


def _norm100_to_signed(value: float) -> float:
    return max(-1.0, min(1.0, (value / 50.0) - 1.0))


def emotion_pad_vector(emotion: str, intensity: float = 1.0) -> Tuple[float, float, float]:
    base_p, base_a, base_d = EMX_PAD.get(emotion, (0.5, 0.5, 0.5))
    p0 = base_p * 100.0
    a0 = base_a * 100.0
    d0 = (base_d + 1.0) * 50.0 if base_d < 0.0 else base_d * 100.0
    p1, a1, d1 = apply_pad_override(
        emotion_name=emotion,
        E=max(0.0, min(100.0, intensity * 100.0)),
        P=p0,
        A=a0,
        D=d0,
        raw_overrides=EMX_OVERRIDES,
    )
    return (_norm100_to_signed(p1), _norm100_to_signed(a1), _norm100_to_signed(d1))


def _unit(vec: Tuple[float, float, float]) -> Tuple[float, float, float]:
    mag = math.sqrt(sum(v * v for v in vec)) or 1.0
    return tuple(v / mag for v in vec)


EMOTION_PAD_VECTORS: Dict[str, Tuple[float, float, float]] = {
    emotion: emotion_pad_vector(emotion, 1.0) for emotion in EMX_EMOTIONS
}
EMOTION_UNIT_VECTORS: Dict[str, Tuple[float, float, float]] = {
    emotion: _unit(vec) for emotion, vec in EMOTION_PAD_VECTORS.items()
}


def _dot(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _blend_color(a: str, b: str) -> tuple:
    ca = EMX_COLORS.get(a, (160, 160, 160))
    cb = EMX_COLORS.get(b, (160, 160, 160))
    return tuple(int((ca[i] + cb[i]) / 2) for i in range(3))


def _pair_name(a: str, b: str, prefix: str) -> str:
    known = {
        frozenset({"Joy", "Love"}): "Radiance",
        frozenset({"Love", "Calm"}): "Belonging",
        frozenset({"Calm", "Pride"}): "Confidence",
        frozenset({"Pride", "Anger"}): "Defiance",
        frozenset({"Anger", "Fear"}): "Alarm",
        frozenset({"Fear", "Panic"}): "Terror",
        frozenset({"Panic", "Sadness"}): "Griefstorm",
        frozenset({"Sadness", "Shame"}): "Remorse",
        frozenset({"Shame", "Exhaustion"}): "Collapse",
        frozenset({"Exhaustion", "Joy"}): "Relief",
        frozenset({"Joy", "Calm"}): "Ease",
        frozenset({"Love", "Pride"}): "Devotion",
        frozenset({"Fear", "Sadness"}): "Despair",
        frozenset({"Anger", "Shame"}): "Contempt",
        frozenset({"Pride", "Panic"}): "Wonder",
    }
    pair = frozenset({a, b})
    if pair in known:
        return known[pair]
    return f"{prefix}_{a}_{b}"


def _pair_center(a: str, b: str) -> Tuple[float, float, float]:
    va = EMOTION_PAD_VECTORS[a]
    vb = EMOTION_PAD_VECTORS[b]
    return ((va[0] + vb[0]) / 2.0, (va[1] + vb[1]) / 2.0, (va[2] + vb[2]) / 2.0)


def _pair_rank(a: str, b: str) -> float:
    ua = EMOTION_UNIT_VECTORS[a]
    ub = EMOTION_UNIT_VECTORS[b]
    alignment = _dot(ua, ub)
    center = _pair_center(a, b)
    magnitude = math.sqrt(sum(v * v for v in center))
    return alignment * 0.7 + magnitude * 0.3


def _build_tiered_pairs() -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str]], List[Tuple[str, str, str]]]:
    scored: List[Tuple[float, str, str]] = []
    for i, emotion_a in enumerate(EMX_EMOTIONS):
        for emotion_b in EMX_EMOTIONS[i + 1:]:
            scored.append((_pair_rank(emotion_a, emotion_b), emotion_a, emotion_b))
    scored.sort(reverse=True)

    primary: List[Tuple[str, str, str]] = []
    secondary: List[Tuple[str, str, str]] = []
    tertiary: List[Tuple[str, str, str]] = []

    for idx, (_, emotion_a, emotion_b) in enumerate(scored[:24]):
        if idx < 8:
            bucket = primary
            prefix = "Primary"
        elif idx < 16:
            bucket = secondary
            prefix = "Secondary"
        else:
            bucket = tertiary
            prefix = "Tertiary"
        bucket.append((_pair_name(emotion_a, emotion_b, prefix), emotion_a, emotion_b))

    return primary, secondary, tertiary


PRIMARY_COMPOSITES, SECONDARY_COMPOSITES, TERTIARY_COMPOSITES = _build_tiered_pairs()
ALL_COMPOSITES: List[Tuple[str, str, str]] = PRIMARY_COMPOSITES + SECONDARY_COMPOSITES + TERTIARY_COMPOSITES

COMPOSITE_COORDS: Dict[str, Tuple[float, float, float]] = {
    name: _pair_center(a, b) for name, a, b in ALL_COMPOSITES
}


def _counter_pairs(entries: List[Tuple[str, str, str]]) -> List[Tuple[str, str]]:
    remaining = [name for name, _, _ in entries]
    coords = COMPOSITE_COORDS
    pairs: List[Tuple[str, str]] = []
    while len(remaining) > 1:
        left = remaining.pop(0)
        left_u = _unit(coords[left])
        opposite_idx = min(
            range(len(remaining)),
            key=lambda idx: _dot(left_u, _unit(coords[remaining[idx]])),
        )
        right = remaining.pop(opposite_idx)
        pairs.append((left, right))
    return pairs


PRIMARY_COUNTER_PAIRS: List[Tuple[str, str]] = _counter_pairs(PRIMARY_COMPOSITES)
SECONDARY_COUNTER_PAIRS: List[Tuple[str, str]] = _counter_pairs(SECONDARY_COMPOSITES)
TERTIARY_COUNTER_PAIRS: List[Tuple[str, str]] = _counter_pairs(TERTIARY_COMPOSITES)
ALL_COUNTER_PAIRS: List[Tuple[str, str]] = PRIMARY_COUNTER_PAIRS + SECONDARY_COUNTER_PAIRS + TERTIARY_COUNTER_PAIRS

EMERGE_RATE: Dict[str, float] = {"primary": 0.100, "secondary": 0.055, "tertiary": 0.028}
CANCEL_RATE: Dict[str, float] = {"primary": 0.45, "secondary": 0.38, "tertiary": 0.30}
TIER_BLEND_WEIGHT: Dict[str, float] = {"primary": 1.00, "secondary": 0.70, "tertiary": 0.45}
EMERGE_THRESHOLD: float = 0.18

TIER_MAP: Dict[str, str] = {}
for _name, _a, _b in PRIMARY_COMPOSITES:
    TIER_MAP[_name] = "primary"
for _name, _a, _b in SECONDARY_COMPOSITES:
    TIER_MAP[_name] = "secondary"
for _name, _a, _b in TERTIARY_COMPOSITES:
    TIER_MAP[_name] = "tertiary"

COMPOSITE_COLORS: Dict[str, tuple] = {name: _blend_color(a, b) for name, a, b in ALL_COMPOSITES}


def zero_composites() -> Dict[str, float]:
    return {name: 0.0 for name, _, _ in ALL_COMPOSITES}


def composite_alignment(name: str) -> float:
    center = COMPOSITE_COORDS.get(name, (0.0, 0.0, 0.0))
    return math.sqrt(sum(v * v for v in center))


def compute_composites(base_emx: Dict[str, float]) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for tier_list in (PRIMARY_COMPOSITES, SECONDARY_COMPOSITES, TERTIARY_COMPOSITES):
        for name, ea, eb in tier_list:
            va = base_emx.get(ea, 0.0)
            vb = base_emx.get(eb, 0.0)
            alignment = max(0.10, (_dot(EMOTION_UNIT_VECTORS[ea], EMOTION_UNIT_VECTORS[eb]) + 1.0) / 2.0)
            raw = ((va + vb) / 2.0) * alignment * (0.65 + 0.35 * composite_alignment(name))
            result[name] = raw * TIER_BLEND_WEIGHT[TIER_MAP[name]]
    return result


def cancel_tick(comp: Dict[str, float], rates: Dict[str, float] | None = None) -> None:
    if rates is None:
        rates = CANCEL_RATE
    for tier_name, pairs in (
        ("primary", PRIMARY_COUNTER_PAIRS),
        ("secondary", SECONDARY_COUNTER_PAIRS),
        ("tertiary", TERTIARY_COUNTER_PAIRS),
    ):
        rate = rates[tier_name]
        for left, right in pairs:
            cancel = min(comp.get(left, 0.0), comp.get(right, 0.0)) * rate
            comp[left] = max(0.0, comp.get(left, 0.0) - cancel)
            comp[right] = max(0.0, comp.get(right, 0.0) - cancel)


def apply_cancellation(comp: Dict[str, float]) -> Dict[str, float]:
    result = comp.copy()
    cancel_tick(result)
    return result


def compute_neutral_score(comp: Dict[str, float]) -> float:
    if not ALL_COUNTER_PAIRS:
        return 1.0
    net_charge = sum(abs(comp.get(a, 0.0) - comp.get(b, 0.0)) for a, b in ALL_COUNTER_PAIRS) / len(ALL_COUNTER_PAIRS)
    return max(0.0, 1.0 - net_charge)
