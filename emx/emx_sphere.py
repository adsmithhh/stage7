"""
emx_sphere.py - PAD-driven composite sphere.

Uses PAD-derived composite centroids from ``emx_composites.py`` and groups them
into a six-region field used by the CENTRAL renderers.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from .emx_composites import COMPOSITE_COORDS


def _unit(v: float, a: float, d: float) -> Tuple[float, float, float]:
    mag = math.sqrt(v * v + a * a + d * d) or 1.0
    return (v / mag, a / mag, d / mag)


ATTR_MAP: Dict[str, List[str]] = {}
for name, (p, a, d) in COMPOSITE_COORDS.items():
    tags: List[str] = []
    tags.append("good" if p > 0.20 else ("bad" if p < -0.20 else "neutral"))
    if a > 0.30:
        tags.append("energetic")
    elif a < -0.20:
        tags.append("calm")
    if d > 0.20:
        tags.append("dominant")
    elif d < -0.20:
        tags.append("yielding")
    ATTR_MAP[name] = tags


_REGIONS = {
    "RADIANT": (0.85, 0.70, 0.55),
    "WARM": (0.65, -0.20, 0.15),
    "DRIVEN": (0.25, 0.65, 0.75),
    "VOLATILE": (-0.45, 0.80, 0.10),
    "HEAVY": (-0.70, -0.35, -0.25),
    "COLD": (-0.40, -0.20, -0.70),
}
_REGION_UNITS = {name: _unit(*coords) for name, coords in _REGIONS.items()}
_COMPOSITE_UNITS = {name: _unit(*coords) for name, coords in COMPOSITE_COORDS.items()}


def composite_region(name: str) -> str:
    uv = _COMPOSITE_UNITS.get(name)
    if uv is None:
        return "COLD"
    best_region, best_dot = "COLD", -2.0
    for region, rv in _REGION_UNITS.items():
        dot = uv[0] * rv[0] + uv[1] * rv[1] + uv[2] * rv[2]
        if dot > best_dot:
            best_region, best_dot = region, dot
    return best_region


REGION_MAP: Dict[str, str] = {
    name: composite_region(name) for name in COMPOSITE_COORDS
}

REGION_MEMBERS: Dict[str, List[str]] = {}
for name, region in REGION_MAP.items():
    REGION_MEMBERS.setdefault(region, []).append(name)


_PAIR_DOTS: Dict[frozenset, float] = {}
_names = list(COMPOSITE_COORDS.keys())
for i in range(len(_names)):
    for j in range(i + 1, len(_names)):
        left, right = _names[i], _names[j]
        ul = _COMPOSITE_UNITS[left]
        ur = _COMPOSITE_UNITS[right]
        _PAIR_DOTS[frozenset({left, right})] = ul[0] * ur[0] + ul[1] * ur[1] + ul[2] * ur[2]


def dominant_region(comp: Dict[str, float]) -> Optional[str]:
    region_mass: Dict[str, float] = {}
    for name, value in comp.items():
        if value > 0.0:
            region = REGION_MAP.get(name, "COLD")
            region_mass[region] = region_mass.get(region, 0.0) + value
    if not region_mass:
        return None
    return max(region_mass, key=region_mass.get)


def region_attrs(region: str) -> List[str]:
    return {
        "RADIANT": ["good", "energetic", "dominant"],
        "WARM": ["good", "calm"],
        "DRIVEN": ["energetic", "dominant"],
        "VOLATILE": ["bad", "energetic"],
        "HEAVY": ["bad", "calm"],
        "COLD": ["bad", "yielding"],
    }.get(region, [])
