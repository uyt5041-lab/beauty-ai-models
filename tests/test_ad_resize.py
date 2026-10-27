import hashlib
from io import BytesIO

from PIL import Image, ImageCms, ImageDraw
import pytest

from beauty_ai.ad_resize import FINAL_SIZE, resize_ad_png
from beauty_ai.errors import GatewayError


def encode(image, format="PNG", **kwargs):
    out = BytesIO()
    image.save(out, format, **kwargs)
    return out.getvalue()


def decode(data):
    image = Image.open(BytesIO(data))
    image.load()
    return image


def test_exact_size_png_is_byte_identical():
    original = encode(Image.new("RGB", FINAL_SIZE, (243, 90, 153)))
    result = resize_ad_png(original)
    assert result.image_bytes is original
    assert result.sha256 == result.source_sha256 == hashlib.sha256(original).hexdigest()
    assert not result.resized and result.method == "unchanged"


def test_downsample_preserves_source_aspect_color_and_png_pixels():
    image = Image.new("RGB", (1152, 1440), (243, 90, 153))
    ImageDraw.Draw(image).rectangle((300, 100, 800, 1300), fill=(80, 100, 120))
    original = encode(image)
    before = hashlib.sha256(original).hexdigest()
    result = resize_ad_png(original)
    output = decode(result.image_bytes)
    assert original == encode(image) and result.source_sha256 == before
    assert output.size == FINAL_SIZE and output.format == "PNG"
    assert result.width == output.width and result.height == output.height
    assert result.resized and result.method == "lanczos"
    assert output.tobytes() == image.resize(FINAL_SIZE, Image.Resampling.LANCZOS).tobytes()
    assert output.getpixel((0, 0)) == (243, 90, 153)
    assert output.getpixel((500, 500)) == (80, 100, 120)


def test_rgba_edge_has_no_black_halo():
    image = Image.new("RGBA", (1152, 1440), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((300, 100, 800, 1300), fill=(255, 255, 255, 255))
    output = decode(resize_ad_png(encode(image)).image_bytes)
    assert output.mode == "RGBA"
    edge = [output.getpixel((x, 500)) for x in range(275, 290)]
    partial = [pixel for pixel in edge if 0 < pixel[3] < 255]
    assert partial and all(pixel[:3] == (255, 255, 255) for pixel in partial)
    assert output.getpixel((0, 0))[3] == 0


def test_icc_is_validated_and_color_managed():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    original = encode(Image.new("RGB", (1152, 1440), (100, 120, 150)), icc_profile=profile)
    output = decode(resize_ad_png(original).image_bytes)
    assert output.getpixel((500, 500)) == (100, 120, 150)
    assert ImageCms.getProfileName(ImageCms.ImageCmsProfile(BytesIO(output.info["icc_profile"])))
    unchanged = encode(Image.new("RGB", FINAL_SIZE), icc_profile=profile)
    assert resize_ad_png(unchanged).image_bytes == unchanged


def test_exif_orientation_is_applied_once():
    image = Image.new("RGB", (1440, 1152), "red")
    exif = Image.Exif()
    exif[274] = 6
    result = resize_ad_png(encode(image, exif=exif))
    assert (result.source_width, result.source_height) == (1152, 1440)
    output = decode(result.image_bytes)
    assert output.getexif().get(274, 1) == 1 and output.size == FINAL_SIZE


@pytest.mark.parametrize("mode", ["L", "LA", "P"])
def test_supported_color_modes(mode):
    image = Image.new(mode, (1152, 1440))
    if mode == "P":
        image.info["transparency"] = 0
    output = decode(resize_ad_png(encode(image)).image_bytes)
    assert output.mode == ("RGB" if mode == "L" else "RGBA")


@pytest.mark.parametrize("size,code", [((1024, 1280), "RESIZE_UPSCALE_DISABLED"),
                                     ((1152, 1400), "RESIZE_ASPECT_MISMATCH")])
def test_no_upscale_or_crop(size, code):
    with pytest.raises(GatewayError) as error:
        resize_ad_png(encode(Image.new("RGB", size)))
    assert error.value.code == code


def test_bad_profile_and_corrupt_images_fail():
    bad_profile = encode(Image.new("RGB", FINAL_SIZE), icc_profile=b"invalid-icc")
    for data in (b"corrupt", bad_profile, encode(Image.new("RGB", (1152, 1440)))[:100]):
        with pytest.raises(GatewayError):
            resize_ad_png(data)


def test_animation_untagged_cmyk_and_limits_fail():
    frame = Image.new("RGB", (1152, 1440))
    animation = encode(frame, save_all=True, append_images=[Image.new("RGB", frame.size, "red")])
    for data in (animation, encode(Image.new("CMYK", frame.size), "JPEG")):
        with pytest.raises(GatewayError):
            resize_ad_png(data)
    for limit in (dict(max_input_bytes=100), dict(max_pixels=100)):
        with pytest.raises(GatewayError) as error:
            resize_ad_png(encode(frame), **limit)
        assert error.value.code == "RESIZE_IMAGE_TOO_LARGE"


def test_jpeg_is_decoded_once_then_saved_as_png():
    original = encode(Image.new("RGB", FINAL_SIZE, (80, 100, 120)), "JPEG")
    result = resize_ad_png(original)
    assert result.method == "png-normalize" and not result.resized
    assert decode(result.image_bytes).tobytes() == decode(original).tobytes()


def test_resize_never_calls_image_api(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **kw: pytest.fail("Network not allowed"))
    resize_ad_png(encode(Image.new("RGB", (1152, 1440))))
