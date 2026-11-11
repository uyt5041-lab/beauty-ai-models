"""Deterministic local typography tone selection and glyph-core contrast checks.

The check covers actual rendered pixels whose glyph coverage is at least 192/255.
It does not certify antialiased edges, placement, product preservation or design
quality. Apply the returned RGBA scrim with ``Image.alpha_composite`` before
rendering the same glyph mask; changing the background/mask invalidates the check.
"""
from __future__ import annotations

from collections import Counter
import colorsys
from dataclasses import dataclass
import re

from PIL import Image, ImageStat


POLICY_VERSION = "local-typography-tone-v1"
CORE_ALPHA = 192
TARGET_CONTRAST = 4.5
_LINEAR = tuple(
    value / 255 / 12.92 if value / 255 <= .04045
    else ((value / 255 + .055) / 1.055) ** 2.4
    for value in range(256)
)


@dataclass(frozen=True)
class TypographyColorResolution:
    color: str
    background_overlay: tuple[int, int, int, int] | None
    receipt: dict


def _luminance(rgb: tuple[int, ...]) -> float:
    return .2126 * _LINEAR[rgb[0]] + .7152 * _LINEAR[rgb[1]] + .0722 * _LINEAR[rgb[2]]


def _pixels(image: Image.Image):
    getter = getattr(image, "get_flattened_data", None)
    return getter() if getter is not None else image.getdata()


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _tonal(hue: float, lightness: float, saturation: float) -> tuple[int, int, int]:
    return tuple(round(value * 255) for value in colorsys.hls_to_rgb(hue, lightness, saturation))


def _scene_tone(background: Image.Image) -> tuple[str, float, float, tuple[int, int, int]]:
    # The white matte affects hue selection only, never the contrast verdict.
    thumbnail = background.copy()
    thumbnail.thumbnail((32, 32), Image.Resampling.BOX)
    thumbnail = Image.alpha_composite(Image.new("RGBA", thumbnail.size, "white"), thumbnail)
    median = tuple(ImageStat.Stat(thumbnail.convert("RGB")).median)
    hue, _, saturation = colorsys.rgb_to_hls(*(value / 255 for value in median))
    if max(median) - min(median) < 12 or saturation < .08:
        tone, hue = "neutral", 0.0
    else:
        tone = "warm" if hue < 75 / 360 or hue >= 315 / 360 else "cool"
    return tone, hue, _luminance(median), median


@dataclass
class _Samples:
    background: Image.Image
    coverage: Image.Image

    @classmethod
    def from_pixels(cls, pixels: list[tuple[int, ...]]) -> _Samples:
        size = (len(pixels), 1)
        background = Image.new("RGBA", size)
        background.putdata([pixel[:4] for pixel in pixels])
        coverage = Image.new("L", size)
        coverage.putdata([pixel[4] for pixel in pixels])
        return cls(background, coverage)

    def measure(self, color: tuple[int, int, int], overlay: tuple[int, ...] | None = None) -> tuple[float, int]:
        background = self.background
        if overlay is not None:
            background = Image.alpha_composite(background, Image.new("RGBA", background.size, overlay))
        ink = Image.new("RGBA", background.size, (*color, 255))
        ink.putalpha(self.coverage)
        rendered = Image.alpha_composite(background, ink)
        if background.getextrema()[3][0] == 255:
            ranges = ((pixel, pixel, drawn, drawn)
                      for pixel, drawn in zip(_pixels(background), _pixels(rendered)))
        else:
            # Unknown final matte: any RGB matte lies between black and white
            # channel-wise. Separate luminance intervals give a conservative
            # lower bound, including intermediate mattes (endpoints alone fail).
            black = Image.new("RGBA", background.size, "black")
            white = Image.new("RGBA", background.size, "white")
            ranges = zip(
                _pixels(Image.alpha_composite(black, background)),
                _pixels(Image.alpha_composite(white, background)),
                _pixels(Image.alpha_composite(black, rendered)),
                _pixels(Image.alpha_composite(white, rendered)),
            )
        minimum, worst = 22.0, 0
        for index, (bg_low, bg_high, ink_low, ink_high) in enumerate(ranges):
            low, high = _luminance(bg_low), _luminance(bg_high)
            text_low, text_high = _luminance(ink_low), _luminance(ink_high)
            if text_high < low:
                ratio = (low + .05) / (text_high + .05)
            elif high < text_low:
                ratio = (text_low + .05) / (high + .05)
            else:
                ratio = 1.0
            if ratio < minimum:
                minimum, worst = ratio, index
        return minimum, worst


def _minimum_scrim(
    samples: _Samples, pixels: list[tuple[int, ...]], color: tuple[int, int, int],
    scrim: tuple[int, int, int],
) -> tuple[tuple[int, int, int, int], float]:
    # Exhaustive 8-bit opacity search guarantees the minimum for this color and
    # scrim pair. A small probe rejects failures cheaply; every passing result
    # is checked against all unique core pixels, adding missed worst cases.
    indices = set(range(0, len(pixels), max(1, len(pixels) // 64)))
    indices.add(len(pixels) - 1)
    probe = _Samples.from_pixels([pixels[index] for index in sorted(indices)])
    for alpha in range(1, 256):
        overlay = (*scrim, alpha)
        if probe.measure(color, overlay)[0] < TARGET_CONTRAST:
            continue
        minimum, worst = samples.measure(color, overlay)
        if minimum >= TARGET_CONTRAST:
            return overlay, minimum
        if worst not in indices:
            indices.add(worst)
            probe = _Samples.from_pixels([pixels[index] for index in sorted(indices)])
    raise RuntimeError("The fully opaque tonal scrim must satisfy core contrast")


def resolve_typography_color(
    background: Image.Image,
    ink_mask: Image.Image,
    preferred_color: str = "#151515",
    fixed: bool = False,
) -> TypographyColorResolution:
    """Choose scene-related ink and, if necessary, a minimal flat tonal scrim.

    Nondefault preferred colors are honored when they pass. ``fixed=True``
    preserves any requested color without adding a scrim and reports failures.
    Empty cores are unverified, never a contrast pass. Inputs are not mutated.
    """
    if background.mode not in {"RGB", "RGBA"}:
        raise ValueError("background must be RGB or RGBA")
    if ink_mask.mode != "L" or background.size != ink_mask.size:
        raise ValueError("ink_mask must be L with the background dimensions")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", preferred_color):
        raise ValueError("preferred_color must be a six-digit hex RGB color")
    preferred = tuple(int(preferred_color[index:index + 2], 16) for index in (1, 3, 5))
    rgba = background.convert("RGBA")
    counts = Counter((*pixel, coverage) for pixel, coverage in zip(_pixels(rgba), _pixels(ink_mask))
                     if coverage >= CORE_ALPHA)
    pixels = list(counts)
    tone, hue, luminance, median = _scene_tone(rgba) if rgba.width and rgba.height else ("neutral", 0.0, 0.0, (0, 0, 0))
    selected, overlay, minimum, adjustment = preferred, None, None, "no_glyph_core_pixels"
    saturation = 0.0 if tone == "neutral" else .18
    dark = _tonal(hue, .13, saturation)
    deep = _tonal(hue, .035, saturation)
    light = _tonal(hue, .97, saturation)
    bright = _tonal(hue, .995, saturation)
    palette = [dark, deep, light, bright] if luminance >= .25 else [light, bright, dark, deep]
    if pixels:
        samples = _Samples.from_pixels(pixels)
        if fixed:
            minimum = samples.measure(preferred)[0]
            adjustment = "fixed_color"
        else:
            candidates = ([preferred] if preferred != (21, 21, 21) else []) + palette
            for candidate in candidates:
                minimum = samples.measure(candidate)[0]
                if minimum >= TARGET_CONTRAST:
                    selected = candidate
                    adjustment = "preferred_color_preserved" if candidate == preferred else "scene_tonal_color"
                    break
            else:
                options = []
                for color, scrim in ((deep, _tonal(hue, .97, saturation)),
                                     (bright, _tonal(hue, .035, saturation))):
                    candidate_overlay, candidate_minimum = _minimum_scrim(samples, pixels, color, scrim)
                    options.append((color, candidate_overlay, candidate_minimum))
                selected, overlay, minimum = min(options, key=lambda option: option[1][3])
                adjustment = "scene_tonal_color_with_scrim"
    receipt = {
        "policy_version": POLICY_VERSION,
        "background_tone": tone,
        "background_hue_degrees": round(hue * 360, 3) if tone != "neutral" else None,
        "background_median_rgb": list(median),
        "background_luminance": luminance,
        "background_alpha_policy": "worst_case_unknown_matte",
        "selected_hex": _hex(selected),
        "requested_hex": _hex(preferred),
        "fixed": fixed,
        "min_contrast_ratio": minimum,
        "target_contrast_ratio": TARGET_CONTRAST,
        "checked_sample_count": sum(counts.values()),
        "unique_sample_count": len(pixels),
        "core_alpha_threshold": CORE_ALPHA,
        "contrast_scope": "rendered_glyph_core_pixels",
        "passed": minimum is not None and minimum >= TARGET_CONTRAST,
        "adjustment": adjustment,
        "background_overlay_rgba": list(overlay) if overlay else None,
        "limitations": ["antialias_edges_below_core_threshold_not_checked", "product_and_layout_not_checked"],
    }
    return TypographyColorResolution(_hex(selected), overlay, receipt)
