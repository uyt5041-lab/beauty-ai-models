"""Prepare an unapproved working product layer and inspect bounded pixel integrity.

These helpers perform no segmentation, model inference or human approval. Resizing
creates a new candidate layer; the upload is not the pixel-comparison reference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

from PIL import Image, ImageChops

from .contracts import Rect, VisualBrief


PixelBounds = tuple[int, int, int, int]


@dataclass(frozen=True)
class PreparedProduct:
    product_rgba: Image.Image
    preserve_mask: Image.Image
    source_bounds: PixelBounds
    product_bounds: PixelBounds
    needs_approval: bool = field(default=True, init=False)


@dataclass(frozen=True)
class IntegrityReport:
    opaque_pixels: int
    mismatched_pixels: int
    partial_alpha_pixels: int

    @property
    def opaque_preserved(self) -> bool:
        """False when no fully opaque pixels exist; avoid a vacuous pass."""
        return self.opaque_pixels > 0 and self.mismatched_pixels == 0

    @property
    def needs_manual_check(self) -> bool:
        """Flag a failed comparison or pixels outside its certifiable scope."""
        return (self.mismatched_pixels > 0 or self.partial_alpha_pixels > 0
                or self.opaque_pixels == 0)


def _require_rgba(image: Image.Image, label: str) -> tuple[Image.Image, PixelBounds]:
    if not isinstance(image, Image.Image) or image.mode != "RGBA":
        raise ValueError(f"{label} must contain an explicit RGBA alpha channel")
    if image.width < 1 or image.height < 1:
        raise ValueError(f"{label} must have nonzero dimensions")
    image.load()
    alpha = image.getchannel("A")
    bounds = alpha.getbbox()
    if bounds is None:
        raise ValueError(f"{label} must contain a nontransparent product")
    return alpha, bounds


def _pixel_bounds(rect: Rect, size: tuple[int, int], *, inside: bool) -> PixelBounds:
    """Convert half-open normalized rectangles with conservative edge rounding."""
    width, height = size
    lower = math.ceil if inside else math.floor
    upper = math.floor if inside else math.ceil
    # Rect tolerates tiny floating-point overflow; always clip to the real canvas.
    return (
        max(0, min(width, lower(rect.x * width))),
        max(0, min(height, lower(rect.y * height))),
        max(0, min(width, upper((rect.x + rect.width) * width))),
        max(0, min(height, upper((rect.y + rect.height) * height))),
    )


def prepare_product_canvas(product_rgba: Image.Image, visual: VisualBrief) -> PreparedProduct:
    """Crop alpha bounds, fit and center within product_box, and make a keep mask.

    Product bounds round inward and text bounds round outward. Shared edges are
    half-open; the resulting alpha is checked against the reserved text pixels.
    The preserve mask is binary (white for any alpha > 0), never an alpha matte.
    """
    _, source_bounds = _require_rgba(product_rgba, "Product")
    # Revalidate even when callers used model_construct or mutated a model instance.
    visual = VisualBrief.model_validate(visual.model_dump())
    size = (visual.generation_width, visual.generation_height)
    left, top, right, bottom = _pixel_bounds(visual.product_box, size, inside=True)
    available_width, available_height = right - left, bottom - top
    if available_width < 1 or available_height < 1:
        raise ValueError("Product box has no whole pixels at working resolution")

    cropped = product_rgba.crop(source_bounds)
    # Integer arithmetic avoids accidentally rounding the fitted image out of bounds.
    if cropped.width * available_height > cropped.height * available_width:
        fitted_width = available_width
        fitted_height = max(1, cropped.height * available_width // cropped.width)
    else:
        fitted_height = available_height
        fitted_width = max(1, cropped.width * available_height // cropped.height)
    fitted = cropped.resize((fitted_width, fitted_height), Image.Resampling.LANCZOS)
    origin = (left + (available_width - fitted_width) // 2,
              top + (available_height - fitted_height) // 2)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    # A masked paste would multiply alpha again and corrupt transparent boundaries.
    canvas.paste(fitted, origin)
    alpha, product_bounds = _require_rgba(canvas, "Prepared product")
    safe_bounds = _pixel_bounds(visual.text_safe_zone, size, inside=False)
    if alpha.crop(safe_bounds).getbbox() is not None:
        raise ValueError("Prepared product alpha overlaps the text safe zone")
    if not (left <= product_bounds[0] < product_bounds[2] <= right
            and top <= product_bounds[1] < product_bounds[3] <= bottom):
        raise ValueError("Prepared product alpha exceeds its product box")
    keep = alpha.point(lambda value: 255 if value > 0 else 0)
    return PreparedProduct(canvas, keep, source_bounds, product_bounds)


def inspect_composite(preview: Image.Image, approved_rgba: Image.Image) -> IntegrityReport:
    """Compare fully opaque product pixels on an identical working canvas.

    RGB previews are accepted. For RGBA previews the corresponding output pixels
    must also remain opaque. Partial-alpha pixels are counted, not certified.
    A successful opaque comparison is not an overall visual-quality approval.
    """
    alpha, _ = _require_rgba(approved_rgba, "Approved product")
    if not isinstance(preview, Image.Image) or preview.mode not in ("RGB", "RGBA"):
        raise ValueError("Preview must be an RGB or RGBA image")
    if preview.size != approved_rgba.size:
        raise ValueError("Preview and approved product must share a working canvas")
    preview.load()
    histogram = alpha.histogram()
    opaque = alpha.point(lambda value: 255 if value == 255 else 0)
    difference = ImageChops.difference(preview.convert("RGB"), approved_rgba.convert("RGB"))
    red, green, blue = difference.split()
    any_difference = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    if preview.mode == "RGBA":
        alpha_difference = ImageChops.invert(preview.getchannel("A"))
        any_difference = ImageChops.lighter(any_difference, alpha_difference)
    binary_difference = any_difference.point(lambda value: 255 if value > 0 else 0)
    mismatches = ImageChops.multiply(binary_difference, opaque).histogram()[255]
    return IntegrityReport(
        opaque_pixels=histogram[255],
        mismatched_pixels=mismatches,
        partial_alpha_pixels=sum(histogram[1:255]),
    )
