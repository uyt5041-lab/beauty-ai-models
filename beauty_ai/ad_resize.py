"""Deterministic final-canvas conversion. Never calls an image provider.

PNG compression is lossless; downsampling is not. Keep the original bytes and
use this derived artifact for export. No crop, stretch, sharpening or upscale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import warnings

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from .errors import GatewayError


FINAL_SIZE = (1080, 1350)


@dataclass(frozen=True)
class ResizedAd:
    image_bytes: bytes = field(repr=False)
    mime_type: str
    width: int
    height: int
    sha256: str
    source_sha256: str
    source_width: int
    source_height: int
    resized: bool
    method: str


def resize_ad_png(data: bytes, *, max_input_bytes: int = 32 * 1024 * 1024,
                  max_pixels: int = 16_000_000) -> ResizedAd:
    """Normalize orientation/color and downsample a 4:5 image to final PNG.

    Same-sized upright RGB/RGBA PNG is returned byte-for-byte after validation.
    ICC-tagged resized images are color-managed into tagged sRGB. Untagged RGB
    is assumed sRGB. Untagged CMYK/LAB and unknown modes fail explicitly.
    Source dimensions in the receipt are measured after EXIF orientation.
    """
    if (type(max_input_bytes) is not int or max_input_bytes <= 0
            or type(max_pixels) is not int or max_pixels <= 0):
        raise ValueError("Image limits must be positive integers")
    if not isinstance(data, bytes) or not data:
        raise GatewayError("INVALID_RESIZE_INPUT", "An immutable image byte string is required.")
    if len(data) > max_input_bytes:
        raise GatewayError("RESIZE_IMAGE_TOO_LARGE", "Image byte limit exceeded.")
    source_sha = hashlib.sha256(data).hexdigest()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.width * image.height > max_pixels:
                    raise GatewayError("RESIZE_IMAGE_TOO_LARGE", "Image pixel limit exceeded.")
                if (image.format not in {"PNG", "JPEG", "WEBP"}
                        or getattr(image, "n_frames", 1) != 1):
                    raise GatewayError("UNSUPPORTED_RESIZE_IMAGE", "Use one PNG, JPEG or WEBP frame.")
                image.load()
                orientation = image.getexif().get(274, 1)
                upright = ImageOps.exif_transpose(image)
                source_size = upright.size
                width, height = source_size
                if width * 5 != height * 4:
                    raise GatewayError("RESIZE_ASPECT_MISMATCH", "Input must be 4:5; cropping is disabled.")
                if width < FINAL_SIZE[0] or height < FINAL_SIZE[1]:
                    raise GatewayError("RESIZE_UPSCALE_DISABLED", "Input is smaller than the final canvas.")
                profile_bytes = upright.info.get("icc_profile")
                profile = ImageCms.ImageCmsProfile(BytesIO(profile_bytes)) if profile_bytes else None
                if upright.mode not in {"RGB", "RGBA", "L", "LA", "P", "CMYK", "LAB"}:
                    raise GatewayError("UNSUPPORTED_RESIZE_COLOR", "Unsupported image color representation.")
                if upright.mode in {"CMYK", "LAB"} and profile is None:
                    raise GatewayError("UNSUPPORTED_RESIZE_COLOR", "This color space needs an ICC profile.")
                alpha = "A" in upright.getbands() or "transparency" in upright.info
                target_mode = "RGBA" if alpha else "RGB"
                pixels = upright.convert(target_mode)
                output_profile = None
                if profile:
                    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
                    colors = upright if upright.mode in {"RGB", "CMYK", "LAB"} else pixels.convert("RGB")
                    rgb = ImageCms.profileToProfile(colors, profile, srgb, outputMode="RGB")
                    if alpha:
                        rgb.putalpha(pixels.getchannel("A"))
                    pixels = rgb
                    output_profile = srgb.tobytes()
                # Validate the profile even on this no-op path. Preserve exact
                # bytes/color tagging when neither orientation nor size changes.
                if (source_size == FINAL_SIZE and image.format == "PNG"
                        and image.mode in {"RGB", "RGBA"} and orientation == 1):
                    result_bytes, method = data, "unchanged"
                else:
                    if source_size != FINAL_SIZE:
                        if alpha:
                            # Premultiplied alpha avoids dark fringes on cutouts.
                            pixels = pixels.convert("RGBa").resize(FINAL_SIZE, Image.Resampling.LANCZOS).convert("RGBA")
                        else:
                            pixels = pixels.resize(FINAL_SIZE, Image.Resampling.LANCZOS)
                    output = BytesIO()
                    # New image strips source EXIF orientation and free-text tags.
                    clean = Image.frombytes(pixels.mode, pixels.size, pixels.tobytes())
                    save_options = {"icc_profile": output_profile} if output_profile else {}
                    clean.save(output, "PNG", compress_level=6, **save_options)
                    result_bytes = output.getvalue()
                    method = "lanczos" if source_size != FINAL_SIZE else "png-normalize"
    except GatewayError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise GatewayError("RESIZE_IMAGE_TOO_LARGE", "Image decoder pixel limit exceeded.") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, TypeError, UserWarning, ImageCms.PyCMSError):
        raise GatewayError("INVALID_RESIZE_IMAGE", "Image or color profile could not be decoded.") from None
    # Assert the delivered bytes rather than declaring hard-coded dimensions.
    with Image.open(BytesIO(result_bytes)) as final:
        final.load()
        if final.format != "PNG" or final.size != FINAL_SIZE:
            raise GatewayError("RESIZE_OUTPUT_INVALID", "Final PNG did not match its required canvas.")
        actual_width, actual_height = final.size
    return ResizedAd(result_bytes, "image/png", actual_width, actual_height,
                     hashlib.sha256(result_bytes).hexdigest(), source_sha, width, height,
                     source_size != FINAL_SIZE, method)
