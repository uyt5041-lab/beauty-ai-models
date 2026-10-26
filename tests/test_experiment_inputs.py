"""Offline input preparation contract; no provider or observability clients."""
import hashlib
from io import BytesIO
from pathlib import Path
import socket
import struct
from uuid import uuid4
import zlib

from PIL import Image, ImageCms, PngImagePlugin
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import AssetRef
from beauty_ai.errors import GatewayError
from beauty_ai import experiment_inputs as inputs


def picture(path, color=(20, 40, 60), *, format="PNG", size=(6, 4), **options):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, color) as image:
        image.save(path, format=format, **options)
    return path


def photos(tmp_path, count=2):
    return [picture(tmp_path / "photos" / f"view-{i}.png", (i * 17, 40, 60)) for i in range(count)]


def output(tmp_path):
    return tmp_path / "outputs" / "input-trial"


@pytest.mark.parametrize("count,valid", [(0, False), (1, False), (2, True), (5, True), (6, False)])
def test_product_count_boundaries(tmp_path, count, valid):
    products = photos(tmp_path, count)
    target = output(tmp_path)
    if valid:
        assert len(inputs.prepare_inputs(products, [], target, uuid4())) == count
    else:
        with pytest.raises(ValueError, match="^EXPERIMENT_PRODUCT_COUNT_INVALID$"):
            inputs.prepare_inputs(products, [], target, uuid4())
        assert not target.exists()


@pytest.mark.parametrize("count", [0, 5, 6])
def test_reference_count_boundaries(tmp_path, count):
    products = photos(tmp_path)
    references = [picture(tmp_path / "references" / f"scene-{i}.png", (200, i * 17, 10))
                  for i in range(count)]
    if count == 6:
        with pytest.raises(ValueError, match="^EXPERIMENT_REFERENCE_COUNT_INVALID$"):
            inputs.prepare_inputs(products, references, output(tmp_path), uuid4())
        assert not output(tmp_path).exists()
    else:
        assert len(inputs.prepare_inputs(products, references, output(tmp_path), uuid4())) == 2 + count


def test_roles_order_refs_hashes_and_source_files_are_preserved(tmp_path):
    first, second = photos(tmp_path)
    reference = picture(tmp_path / "photos/scene.webp", "green", format="WEBP", lossless=True)
    original = {p: p.read_bytes() for p in (first, second, reference)}
    project_id = uuid4()
    target = output(tmp_path)
    records = inputs.prepare_inputs([second, first], [reference], target, project_id)
    assert [(r["role"], r["index"], r["name"]) for r in records] == [
        ("product", 1, second.name), ("product", 2, first.name), ("reference", 1, reference.name)]
    assert [r["path"] for r in records] == [
        "inputs/product-01.png", "inputs/product-02.png", "inputs/reference-01.png"]
    store = LocalAssetStore(target / "runtime/assets")
    for record, source in zip(records, (second, first, reference), strict=True):
        assert set(record) == {"role", "index", "name", "source_sha256", "normalized_sha256", "path", "asset_ref"}
        assert record["source_sha256"] == hashlib.sha256(original[source]).hexdigest()
        normalized = (target / record["path"]).read_bytes()
        assert record["normalized_sha256"] == hashlib.sha256(normalized).hexdigest()
        ref = AssetRef.model_validate(record["asset_ref"])
        assert record["asset_ref"]["mime_type"] == "image/png"
        assert isinstance(record["asset_ref"]["asset_id"], str)
        assert store.read_bytes(ref, project_id=project_id) == normalized
        with pytest.raises(GatewayError):
            store.read_bytes(ref, project_id=uuid4())
    assert all(p.read_bytes() == raw for p, raw in original.items())


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_supported_formats_decode_to_png_regardless_of_extension(tmp_path, format):
    products = [picture(tmp_path / "first.dat", "red", format=format),
                picture(tmp_path / "second.dat", "blue", format=format)]
    records = inputs.prepare_inputs(products, [], output(tmp_path), uuid4())
    for record in records:
        with Image.open(output(tmp_path) / record["path"]) as image:
            assert image.format == "PNG" and image.mode == "RGB"
            assert image.size == (6, 4)


def test_jpeg_exif_six_is_applied_and_private_metadata_removed(tmp_path):
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private photographer"
    photo = picture(tmp_path / "rotated.jpg", "red", format="JPEG", size=(6, 4), exif=exif)
    other = picture(tmp_path / "other.png", "blue")
    records = inputs.prepare_inputs([photo, other], [], output(tmp_path), uuid4())
    assert (records[0]["asset_ref"]["width"], records[0]["asset_ref"]["height"]) == (4, 6)
    with Image.open(output(tmp_path) / records[0]["path"]) as normalized:
        assert normalized.size == (4, 6)
        assert not normalized.getexif()
        assert not normalized.info
    assert records[0]["source_sha256"] != records[0]["normalized_sha256"]


def test_icc_is_converted_to_srgb_and_metadata_dropped(tmp_path):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-note", "not part of the input snapshot")
    first = picture(tmp_path / "profile.png", (40, 80, 120), icc_profile=profile, pnginfo=metadata)
    second = picture(tmp_path / "other.png", "blue")
    records = inputs.prepare_inputs([first, second], [], output(tmp_path), uuid4())
    with Image.open(output(tmp_path) / records[0]["path"]) as image:
        assert image.getpixel((0, 0)) == (40, 80, 120)
        assert not image.info


@pytest.mark.parametrize("cross_role", [False, True])
def test_identical_raw_bytes_are_rejected_before_output_creation(tmp_path, cross_role):
    products = photos(tmp_path)
    if cross_role:
        references = [products[0]]
    else:
        products = [products[0], products[0]]
        references = []
    with pytest.raises(ValueError, match="^EXPERIMENT_DUPLICATE_RAW_INPUT$"):
        inputs.prepare_inputs(products, references, output(tmp_path), uuid4())
    assert not output(tmp_path).exists()


@pytest.mark.parametrize("cross_role", [False, True])
def test_metadata_only_duplicates_are_rejected_after_normalization(tmp_path, cross_role):
    first, other = photos(tmp_path)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("note", "different bytes, same pixels")
    duplicate = picture(tmp_path / "duplicate.png", (0, 40, 60), pnginfo=metadata)
    assert first.read_bytes() != duplicate.read_bytes()
    products = [first, other] if cross_role else [first, duplicate]
    references = [duplicate] if cross_role else []
    with pytest.raises(ValueError, match="^EXPERIMENT_DUPLICATE_NORMALIZED_INPUT$"):
        inputs.prepare_inputs(products, references, output(tmp_path), uuid4())
    assert output(tmp_path).is_dir()  # Incomplete evidence is retained, not silently overwritten.


@pytest.mark.parametrize("kind", ["garbage", "empty", "gif", "bad_icc", "animated"])
def test_invalid_or_unsupported_images_have_safe_codes(tmp_path, kind):
    other = picture(tmp_path / "other.png", "blue")
    bad = tmp_path / "private-secret-image.png"
    if kind in {"empty", "garbage"}:
        bad.write_bytes(b"" if kind == "empty" else b"private-secret not an image")
    elif kind == "gif":
        picture(bad, "red", format="GIF")
    elif kind == "bad_icc":
        picture(bad, "red", icc_profile=b"private-secret invalid profile")
    else:
        with Image.new("RGB", (6, 4), "red") as frame, Image.new("RGB", (6, 4), "green") as next_frame:
            frame.save(bad, format="PNG", save_all=True, append_images=[next_frame], duration=100)
    with pytest.raises(ValueError, match="^EXPERIMENT_INPUT_INVALID_IMAGE$") as caught:
        inputs.prepare_inputs([bad, other], [], output(tmp_path), uuid4())
    assert "private-secret" not in str(caught.value)


def test_raw_file_above_twenty_mib_is_rejected_before_reading_payload(tmp_path):
    first = tmp_path / "huge.jpg"
    with first.open("wb") as stream:
        stream.truncate(20 * 1024 * 1024 + 1)
    second = picture(tmp_path / "other.png", "blue")
    with pytest.raises(ValueError, match="^EXPERIMENT_INPUT_RAW_TOO_LARGE$"):
        inputs.prepare_inputs([first, second], [], output(tmp_path), uuid4())
    assert not output(tmp_path).exists()


def png_header(width, height):
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))


@pytest.mark.parametrize("size", [(4001, 4000), (16385, 1)])
def test_existing_pixel_and_axis_guard_runs_before_large_decode(tmp_path, size):
    first = tmp_path / "oversized.png"
    first.write_bytes(png_header(*size))
    second = picture(tmp_path / "other.png", "blue")
    with pytest.raises(ValueError, match="^EXPERIMENT_INPUT_DIMENSIONS_TOO_LARGE$"):
        inputs.prepare_inputs([first, second], [], output(tmp_path), uuid4())


def test_provider_per_image_limit_is_applied_after_normalization(tmp_path, monkeypatch):
    products = photos(tmp_path)
    monkeypatch.setattr(inputs, "MAX_PROVIDER_IMAGE_BYTES", 1)
    with pytest.raises(ValueError, match="^EXPERIMENT_INPUT_PROVIDER_IMAGE_TOO_LARGE$"):
        inputs.prepare_inputs(products, [], output(tmp_path), uuid4())


def test_provider_total_limit_counts_products_and_references(tmp_path, monkeypatch):
    products = photos(tmp_path)
    reference = picture(tmp_path / "reference.png", "green")
    total_bytes = sum(len(p.read_bytes()) for p in products + [reference])
    monkeypatch.setattr(inputs, "MAX_PROVIDER_TOTAL_BYTES", total_bytes - 1)
    with pytest.raises(ValueError, match="^EXPERIMENT_INPUT_PROVIDER_TOTAL_TOO_LARGE$"):
        inputs.prepare_inputs(products, [reference], output(tmp_path), uuid4())


def test_existing_output_is_untouched(tmp_path):
    target = output(tmp_path)
    target.mkdir(parents=True)
    marker = target / "keep.txt"
    marker.write_text("user work")
    before = list(target.iterdir())
    with pytest.raises(ValueError, match="^EXPERIMENT_OUTPUT_EXISTS$"):
        inputs.prepare_inputs(photos(tmp_path), [], target, uuid4())
    assert list(target.iterdir()) == before and marker.read_text() == "user work"


@pytest.mark.parametrize("kind", ["outside", "outputs_itself", "parent", "symlink"])
def test_output_must_be_new_and_beneath_real_outputs_directory(tmp_path, kind):
    products = photos(tmp_path)
    if kind == "outside":
        target = tmp_path / "somewhere/trial"
        code = "EXPERIMENT_OUTPUT_OUTSIDE_OUTPUTS"
    elif kind == "outputs_itself":
        target = tmp_path / "outputs"
        code = "EXPERIMENT_OUTPUT_OUTSIDE_OUTPUTS"
    elif kind == "parent":
        target = tmp_path / "outputs/../escaped"
        code = "EXPERIMENT_INPUT_UNSAFE_PATH"
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / "outputs").symlink_to(outside, target_is_directory=True)
        target = tmp_path / "outputs/trial"
        code = "EXPERIMENT_INPUT_UNSAFE_PATH"
    with pytest.raises(ValueError, match="^" + code + "$"):
        inputs.prepare_inputs(products, [], target, uuid4())
    assert not target.exists()


def test_source_symlinks_and_missing_sources_are_rejected(tmp_path):
    first, second = photos(tmp_path)
    link = tmp_path / "linked.png"
    link.symlink_to(first)
    with pytest.raises(ValueError, match="^EXPERIMENT_INPUT_UNSAFE_PATH$"):
        inputs.prepare_inputs([link, second], [], output(tmp_path), uuid4())
    with pytest.raises(ValueError, match="^EXPERIMENT_INPUT_FILE_UNAVAILABLE$"):
        inputs.prepare_inputs([tmp_path / "missing.png", second], [], output(tmp_path), uuid4())


def test_input_preparation_does_not_use_network(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("No network allowed during input preparation")
    monkeypatch.setattr(socket, "socket", deny)
    assert len(inputs.prepare_inputs(photos(tmp_path), [], output(tmp_path), uuid4())) == 2


def test_invalid_project_id_is_rejected_before_creating_output(tmp_path):
    with pytest.raises(ValueError, match="^EXPERIMENT_PROJECT_ID_INVALID$"):
        inputs.prepare_inputs(photos(tmp_path), [], output(tmp_path), "not-a-uuid")
    assert not output(tmp_path).exists()
