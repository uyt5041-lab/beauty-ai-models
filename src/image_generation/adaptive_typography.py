"""Bounded local text placement from pixel changes; no object/semantic detector."""
from __future__ import annotations

from array import array
from collections import Counter
from dataclasses import dataclass
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from .errors import GatewayError
from .typography_color import (
    CORE_ALPHA, TARGET_CONTRAST, TypographyColorResolution, _Samples,
    _hex, _pixels, _scene_tone, _tonal,
)

VERSION = "local-adaptive-typography-v1"


@dataclass(frozen=True)
class AdaptiveTypographyOptions:
    movement_axis: str = "vertical"
    clearance_px: int = 12
    edge_threshold: int = 24
    median_delta: int = 64
    step_px: int = 4
    inset_px: int = 6
    max_move_px: int = 140
    max_color_checks: int = 64
    min_component_area_px: int = 96
    min_component_span_px: int = 80
    fallback_lower: bool = False
    fallback_shift_px: int = 48

    def __post_init__(self):
        if self.movement_axis not in ("vertical", "both"):
            raise ValueError("movement_axis must be vertical or both")
        if type(self.fallback_lower) is not bool:
            raise ValueError("fallback_lower must be a boolean")
        limits = {"clearance_px": (0, 48), "edge_threshold": (1, 255),
                  "median_delta": (1, 255), "step_px": (1, 16),
                  "inset_px": (0, 64), "max_move_px": (0, 512),
                  "max_color_checks": (1, 64), "min_component_area_px": (1, 4096),
                  "min_component_span_px": (1, 512), "fallback_shift_px": (1, 512)}
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid adaptive typography option: {name}")


def scene_palette(background: Image.Image) -> list[tuple[int, int, int]]:
    """More chromatic scene-related candidates, followed by neutral fallbacks."""
    tone, hue, luminance, _ = _scene_tone(background.convert("RGBA"))
    saturation = .65 if tone != "neutral" else .15
    colors = [_tonal(hue, lightness, saturation) for lightness in (.24, .21, .18, .15, .12)]
    colors += [_tonal((hue + shift) % 1, .18, .55) for shift in (.06, -.06, .5)]
    colors += [_tonal(hue, .98, .18), (255, 255, 255), (0, 0, 0)]
    if luminance < .2:
        colors = colors[-3:-1] + colors[:-3] + [colors[-1]]
    return list(dict.fromkeys(colors))


def resolve_scene_color(background: Image.Image, ink_mask: Image.Image) -> TypographyColorResolution:
    counts = Counter((*pixel, alpha) for pixel, alpha in zip(_pixels(background.convert("RGBA")), _pixels(ink_mask))
                     if alpha >= CORE_ALPHA)
    colors = scene_palette(background)
    best, minimum = colors[0], None
    if counts:
        samples = _Samples.from_pixels(list(counts))
        for color in colors:
            measured, _ = samples.measure(color)
            if minimum is None or measured > minimum:
                best, minimum = color, measured
            if measured >= TARGET_CONTRAST:
                best, minimum = color, measured
                break
    return TypographyColorResolution(_hex(best), None, {
        "policy_version": VERSION, "selected_hex": _hex(best),
        "candidate_hex": [_hex(color) for color in colors],
        "candidate_count": len(colors), "target_contrast_ratio": TARGET_CONTRAST,
        "min_contrast_ratio": minimum,
        "passed": minimum is not None and minimum >= TARGET_CONTRAST,
        "background_overlay_rgba": None, "background_overlay_allowed": False,
        "checked_sample_count": sum(counts.values()),
        "contrast_scope": "rendered_glyph_core_pixels", "core_alpha_threshold": CORE_ALPHA,
        "adjustment": "expanded_scene_palette_no_scrim",
    })


def _large_components(mask: Image.Image, options: AdaptiveTypographyOptions) -> Image.Image:
    """Discard isolated texture/glints, retaining sizeable connected boundaries."""
    width, height = mask.size
    raw = mask.tobytes()
    seen = bytearray(len(raw))
    kept = bytearray(len(raw))
    for seed, value in enumerate(raw):
        if not value or seen[seed]:
            continue
        seen[seed] = 1
        pending, component = [seed], []
        min_x = max_x = seed % width
        min_y = max_y = seed // width
        while pending:
            index = pending.pop()
            component.append(index)
            x, y = index % width, index // width
            min_x, max_x = min(min_x,x), max(max_x,x)
            min_y, max_y = min(min_y,y), max(max_y,y)
            for ny in range(max(0,y-1), min(height,y+2)):
                for nx in range(max(0,x-1), min(width,x+2)):
                    other = ny*width+nx
                    if raw[other] and not seen[other]:
                        seen[other] = 1
                        pending.append(other)
        if (len(component) >= options.min_component_area_px
                and max(max_x-min_x+1,max_y-min_y+1) >= options.min_component_span_px):
            for index in component:
                kept[index] = 255
    return Image.frombytes('L', mask.size, bytes(kept))


def boundary_mask(source: Image.Image, box: tuple[int, int, int, int], options: AdaptiveTypographyOptions) -> Image.Image:
    """Detect smoothed local transitions and distance from region median RGB.

    A halo detects edges just outside the allowed text region. Dilation uses a
    square/Chebyshev margin, conservative relative to Euclidean clearance.
    """
    l, t, r, b = box
    halo = options.clearance_px + 8
    area = source.convert("RGB").crop((l-halo, t-halo, r+halo, b+halo))
    smooth = area.filter(ImageFilter.GaussianBlur(1.2))
    horizontal = ImageChops.difference(ImageChops.offset(smooth, 3, 0), ImageChops.offset(smooth, -3, 0))
    vertical = ImageChops.difference(ImageChops.offset(smooth, 0, 3), ImageChops.offset(smooth, 0, -3))
    difference = ImageChops.lighter(horizontal, vertical)
    channels = difference.split()
    magnitude = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    edges = magnitude.point(lambda value: 255 if value >= options.edge_threshold else 0)
    median = tuple(round(value) for value in ImageStat.Stat(source.convert("RGB").crop(box)).median)
    deviation = ImageChops.difference(smooth, Image.new("RGB", area.size, median)).split()
    deviation = ImageChops.lighter(ImageChops.lighter(deviation[0], deviation[1]), deviation[2])
    outliers = deviation.point(lambda value: 255 if value >= options.median_delta else 0)
    hazards = ImageChops.lighter(edges, outliers)
    hazards = _large_components(hazards, options)
    if options.clearance_px:
        hazards = hazards.filter(ImageFilter.MaxFilter(options.clearance_px * 2 + 1))
    return hazards.crop((halo, halo, halo+r-l, halo+b-t))


def _integral(mask: Image.Image):
    width, height = mask.size
    stride = width + 1
    values = array('I', [0]) * ((width+1) * (height+1))
    pixels = mask.load()
    for y in range(height):
        running = 0
        for x in range(width):
            running += bool(pixels[x, y])
            values[(y+1)*stride+x+1] = values[y*stride+x+1] + running

    def count(box):
        l,t,r,b = box
        return values[b*stride+r]-values[t*stride+r]-values[b*stride+l]+values[t*stride+l]
    return count


def _offset_mask(mask: Image.Image, dx: int, dy: int) -> Image.Image:
    output = Image.new("L", mask.size)
    output.paste(mask, (dx, dy))  # Clip; never wrap around as ImageChops.offset does.
    return output


def place_text(source: Image.Image, box, mask: Image.Image, *, options: AdaptiveTypographyOptions):
    """Find the closest edge-cleared placement with verified ink contrast."""
    group = mask.getbbox()
    if group is None:
        raise GatewayError("COMPOSITION_NO_SAFE_POSITION", "문구가 비어 있습니다.")
    hazards = boundary_mask(source, box, options)
    count = _integral(hazards)
    l,t,r,b = group
    width, height = mask.size
    inset, step, limit = options.inset_px, options.step_px, options.max_move_px
    min_dx, max_dx = max(inset-l, -limit), min(width-inset-r, limit)
    if options.movement_axis == "vertical":
        min_dx, max_dx = max(min_dx, 0), min(max_dx, 0)
    min_dy, max_dy = max(inset-t, -limit), min(height-inset-b, limit)
    def offsets(low, high):
        if low > high:
            return []
        return sorted(set(range(low, high+1, step)) | {low, high} | ({0} if low <= 0 <= high else set()))
    candidates = []
    for dy in offsets(min_dy, max_dy):
        for dx in offsets(min_dx, max_dx):
            if not count((l+dx, t+dy, r+dx, b+dy)):
                candidates.append((dx*dx+dy*dy, abs(dx), abs(dy), dx, dy))
    candidates.sort()
    background = source.crop(box).convert("RGBA")
    checks = 0
    for _, _, _, dx, dy in candidates[:options.max_color_checks]:
        moved = _offset_mask(mask, dx, dy)
        colors = resolve_scene_color(background, moved)
        checks += 1
        if colors.receipt["passed"]:
            return moved, colors, {
                "policy_version": VERSION, "offset_px": [dx, dy],
                "movement_axis": options.movement_axis,
                "requested_clearance_px": options.clearance_px,
                "boundary_metric": "smoothed RGB change and region median deviation",
                "clearance_metric": "square dilation (Chebyshev)",
                "hazard_pixels_in_text_box": 0,
                "feasible_position_candidates": len(candidates), "color_position_checks": checks,
                "original_ink_bbox_local": list(group),
                "selected_ink_bbox_local": [l+dx,t+dy,r+dx,b+dy],
                "semantic_product_detection": False,
                "forced_lower_fallback": False,
            }
    if (options.fallback_lower and min_dx <= 0 <= max_dx
            and max(1, min_dy) <= max_dy):
        # Explicit caller-authorized fallback: remain centered and inside the
        # allowed region, even when the measured quality targets cannot pass.
        dy = max(max(1, min_dy), min(options.fallback_shift_px, max_dy))
        moved = _offset_mask(mask, 0, dy)
        colors = resolve_scene_color(background, moved)
        overlap = count((l,t+dy,r,b+dy))
        return moved, colors, {
            "policy_version": VERSION, "movement_axis": options.movement_axis,
            "offset_px": [0, dy], "forced_lower_fallback": True,
            "fallback_reason": "no_position_met_clearance_and_contrast_targets",
            "requested_clearance_px": options.clearance_px,
            "hazard_pixels_in_text_box": overlap, "clearance_passed": overlap == 0,
            "contrast_passed": colors.receipt['passed'],
            "feasible_position_candidates": len(candidates), "color_position_checks": checks,
            "original_ink_bbox_local": list(group),
            "selected_ink_bbox_local": [l,t+dy,r,b+dy],
            "semantic_product_detection": False,
        }
    raise GatewayError("COMPOSITION_NO_SAFE_POSITION", "문구 영역 안에서 경계 거리와 대비를 만족하는 자리를 찾지 못했습니다.")
