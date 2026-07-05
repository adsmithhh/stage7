from __future__ import annotations

import colorsys
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

import yaml


def _load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, dict) else {}


def _titleize(name: str) -> str:
    return str(name).strip().replace("_", " ").title().replace(" ", "")


def _safe_hsv_to_rgb(hue: float, saturation: float, value: float) -> Tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb((hue % 360.0) / 360.0, max(0.0, min(1.0, saturation)), max(0.0, min(1.0, value)))
    return int(r * 255), int(g * 255), int(b * 255)


def _prefer(ordered: List[str], candidates: List[str]) -> str:
    for candidate in candidates:
        if candidate in ordered:
            return candidate
    return ordered[0]


@dataclass(frozen=True)
class EmotionSchema:
    emotions: List[str]
    color_map: Dict[str, Tuple[int, int, int]]
    pads: Dict[str, Tuple[float, float, float]]
    aliases: Dict[str, str]
    order: List[str]
    raw_axis_map: Dict[str, dict]
    raw_overrides: Dict[str, dict]


def _eval_formula(formula_str: str, **ns) -> float:
    """Evaluate a 'clamp(expr)' formula string in the provided variable namespace.

    Supports variables: E, P, A, D, and the emotion name (e.g. 'fear').
    Clamps the result to [0.0, 100.0].
    """
    m = re.match(r"clamp\((.+)\)", formula_str.strip())
    if not m:
        return float(ns.get("E", 50.0))
    try:
        result = eval(m.group(1), {"__builtins__": {}}, ns)  # noqa: S307
        return max(0.0, min(100.0, float(result)))
    except Exception:
        return 50.0


def apply_pad_override(
    emotion_name: str,
    E: float,
    P: float,
    A: float,
    D: float,
    raw_overrides: Dict[str, dict],
) -> Tuple[float, float, float]:
    """Reshape (P, A, D) values using the override formulas for *emotion_name*.

    Parameters
    ----------
    emotion_name : str
        Canonical emotion name (case-insensitive match against override keys).
    E : float
        Current intensity of *emotion_name* in [0, 100].
    P, A, D : float
        Current PAD axis values in [0, 100].
    raw_overrides : dict
        Parsed overrides block from ``EmotionSchema.raw_overrides``.

    Returns
    -------
    (new_P, new_A, new_D) tuple with each value clamped to [0, 100].
    """
    key = emotion_name.lower()
    override = raw_overrides.get(key)
    if not override:
        return P, A, D

    formulas = override.get("formulas", {})
    replaces_axis = override.get("replaces", "").upper()  # P, A, or D

    # Normalise formula keys — strip the trailing '*' (implicit-axis marker)
    clean_keys = {k.rstrip("*").upper() for k in formulas}

    # Step 1: compute the emotion's own processed value (usually just clamp(E))
    em_formula = formulas.get(key, "clamp(E)")
    em_val = _eval_formula(em_formula, E=E, P=P, A=A, D=D)

    # Shared namespace for axis formulas — emotion name maps to its processed value
    ns = {key: em_val, "E": E, "P": P, "A": A, "D": D}

    new_P, new_A, new_D = P, A, D

    # Default: replaced axis takes em_val unless an explicit formula overrides it
    if replaces_axis == "P" and "P" not in clean_keys:
        new_P = em_val
    elif replaces_axis == "A" and "A" not in clean_keys:
        new_A = em_val
    elif replaces_axis == "D" and "D" not in clean_keys:
        new_D = em_val

    # Step 2: evaluate axis formulas (P / A / D), skipping the emotion's own entry
    for raw_key, formula in formulas.items():
        axis = raw_key.rstrip("*").upper()
        if axis == key.upper():
            continue  # already handled above
        val = _eval_formula(formula, **ns)
        if axis == "P":
            new_P = val
        elif axis == "A":
            new_A = val
        elif axis == "D":
            new_D = val

    return new_P, new_A, new_D


def load_emotion_schema(base_dir: str) -> EmotionSchema:
    emotions_path  = os.path.join(base_dir, "emotions.yaml")
    colors_path    = os.path.join(base_dir, "em_colors.yaml")
    overrides_path = os.path.join(base_dir, "override.yaml")

    emotions_yaml  = _load_yaml(emotions_path)
    colors_yaml    = _load_yaml(colors_path)
    overrides_yaml = _load_yaml(overrides_path) if os.path.exists(overrides_path) else {}

    axis_map = emotions_yaml.get("emotion_axis_map") or {}
    ordered = [_titleize(name) for name in axis_map.keys()]
    if not ordered:
        ordered = ["Joy", "Sadness", "Calm", "Fear", "Anger"]

    color_specs = colors_yaml.get("emotion_colors") or {}
    color_map: Dict[str, Tuple[int, int, int]] = {}
    for emotion in ordered:
        spec = color_specs.get(emotion.lower(), {})
        color_map[emotion] = _safe_hsv_to_rgb(
            float(spec.get("hue", 0.0)),
            float(spec.get("saturation", 0.7)),
            float(spec.get("value", 0.85)),
        )

    pad_defaults: Dict[str, Tuple[float, float, float]] = {
        "Joy": (0.85, 0.65, 0.55),
        "Love": (0.90, 0.45, 0.90),
        "Calm": (0.70, 0.15, 0.45),
        "Pride": (0.70, 0.55, 0.20),
        "Anger": (0.15, 0.90, -0.45),
        "Fear": (0.15, 0.78, -0.30),
        "Panic": (0.05, 1.00, -0.55),
        "Sadness": (0.10, 0.20, -0.25),
        "Shame": (0.08, 0.30, -0.65),
        "Exhaustion": (0.18, 0.05, -0.35),
    }

    pads = {emotion: pad_defaults.get(emotion, (0.5, 0.5, 0.0)) for emotion in ordered}

    aliases = {
        "Joy": _prefer(ordered, ["Joy", "Love"]),
        "Sadness": _prefer(ordered, ["Sadness", "Exhaustion", "Shame"]),
        "Acceptance": _prefer(ordered, ["Calm", "Love"]),
        "Disgust": _prefer(ordered, ["Shame", "Anger"]),
        "Fear": _prefer(ordered, ["Fear", "Panic"]),
        "Anger": _prefer(ordered, ["Anger", "Panic"]),
        "Surprise": _prefer(ordered, ["Panic", "Fear"]),
        "Anticipation": _prefer(ordered, ["Pride", "Joy"]),
    }

    # Parse overrides block — keys are lowercase emotion names
    raw_overrides: Dict[str, dict] = {}
    for em_key, spec in (overrides_yaml.get("overrides") or {}).items():
        if isinstance(spec, dict):
            raw_overrides[em_key.lower()] = spec

    return EmotionSchema(
        emotions=ordered,
        color_map=color_map,
        pads=pads,
        aliases=aliases,
        order=ordered,
        raw_axis_map=axis_map,
        raw_overrides=raw_overrides,
    )
