"""Bounded local mask measurements; these values do not approve image quality.

White denotes foreground. Boundaries use 3x3 erosion with a zero-valued exterior;
matching uses a square pixel tolerance, not a distance-based benchmark protocol.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter


MAX_MASK_PIXELS = 16_000_000


@dataclass(frozen=True)
class MaskMetrics:
    iou: float | None
    boundary_precision: float | None
    boundary_recall: float | None
    boundary_f1: float | None
    threshold: int
    tolerance: int


def _binary_mask(image: Image.Image, threshold: int) -> Image.Image:
    if image.mode not in ("1", "L", "RGB", "RGBA"):
        raise ValueError("Masks must use 1, L, grayscale RGB or opaque grayscale RGBA")
    image.load()
    if image.mode in ("RGB", "RGBA"):
        red, green, blue = (image.getchannel(channel) for channel in "RGB")
        if (ImageChops.difference(red, green).getbbox() is not None
                or ImageChops.difference(red, blue).getbbox() is not None):
            raise ValueError("Masks must contain only grayscale pixels")
        if image.mode == "RGBA" and image.getchannel("A").getextrema() != (255, 255):
            raise ValueError("RGBA masks must be fully opaque")
        grayscale = red
    else:
        grayscale = image.convert("L")
    return grayscale.point([255 if value >= threshold else 0 for value in range(256)])


def _boundary(mask: Image.Image) -> Image.Image:
    # Pillow's filter extends edge values. Explicit zero padding makes a full
    # foreground canvas retain its outer boundary, including a one-pixel canvas.
    padded = Image.new("L", (mask.width + 2, mask.height + 2), 0)
    padded.paste(mask, (1, 1))
    eroded = padded.filter(ImageFilter.MinFilter(3)).crop(
        (1, 1, mask.width + 1, mask.height + 1)
    )
    return ImageChops.subtract(mask, eroded)


def _white_pixels(mask: Image.Image) -> int:
    return mask.histogram()[255]


def mask_metrics(
    predicted: Image.Image,
    reference: Image.Image,
    *,
    threshold: int = 128,
    tolerance: int = 2,
) -> MaskMetrics:
    """Measure overlap and tolerant boundary agreement without modifying inputs.

    A pixel is foreground when its grayscale value is >= threshold. Images must
    share a nonempty canvas of at most 16 million pixels. RGB channels must agree;
    RGBA additionally requires every alpha to be 255. Palettes and non-8-bit
    grayscale are rejected rather than silently interpreting or clipping them.

    IoU is undefined (None) for an empty union. An absent predicted boundary has
    undefined precision; an absent reference boundary has undefined recall. Both
    absent gives F1=None; only one absent gives F1=0. A zero score is meaningful
    disagreement, while None indicates that the comparison has no denominator.
    """
    if type(threshold) is not int or not 1 <= threshold <= 255:
        raise ValueError("Mask threshold must be an integer from 1 to 255")
    if type(tolerance) is not int or not 0 <= tolerance <= 8:
        raise ValueError("Boundary tolerance must be an integer from 0 to 8")
    for mask in (predicted, reference):
        if not isinstance(mask, Image.Image):
            raise ValueError("Masks must be Pillow images")
        if mask.width < 1 or mask.height < 1:
            raise ValueError("Masks must have nonzero dimensions")
        if mask.width * mask.height > MAX_MASK_PIXELS:
            raise ValueError("Mask canvas exceeds the pixel limit")
    if predicted.size != reference.size:
        raise ValueError("Masks must share the same canvas")

    predicted_binary = _binary_mask(predicted, threshold)
    reference_binary = _binary_mask(reference, threshold)
    intersection = _white_pixels(ImageChops.darker(predicted_binary, reference_binary))
    union = _white_pixels(ImageChops.lighter(predicted_binary, reference_binary))
    iou = intersection / union if union else None

    predicted_boundary = _boundary(predicted_binary)
    reference_boundary = _boundary(reference_binary)
    predicted_count = _white_pixels(predicted_boundary)
    reference_count = _white_pixels(reference_boundary)
    if not predicted_count or not reference_count:
        return MaskMetrics(
            iou=iou,
            boundary_precision=0.0 if predicted_count else None,
            boundary_recall=0.0 if reference_count else None,
            boundary_f1=0.0 if predicted_count or reference_count else None,
            threshold=threshold,
            tolerance=tolerance,
        )

    if tolerance:
        window = ImageFilter.MaxFilter(tolerance * 2 + 1)
        predicted_neighborhood = predicted_boundary.filter(window)
        reference_neighborhood = reference_boundary.filter(window)
    else:
        predicted_neighborhood = predicted_boundary
        reference_neighborhood = reference_boundary
    precision = _white_pixels(
        ImageChops.darker(predicted_boundary, reference_neighborhood)
    ) / predicted_count
    recall = _white_pixels(
        ImageChops.darker(reference_boundary, predicted_neighborhood)
    ) / reference_count
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return MaskMetrics(iou, precision, recall, f1, threshold, tolerance)
