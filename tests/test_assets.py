from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import sqlite3
from uuid import uuid4

from PIL import Image, ImageCms, PngImagePlugin
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.errors import GatewayError


def encoded(image, format="PNG", **kwargs):
    stream = BytesIO()
    image.save(stream, format=format, **kwargs)
    return stream.getvalue()


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_upload_roundtrip_is_project_scoped_normalized_png(tmp_path, format):
    project = uuid4()
    store = LocalAssetStore(tmp_path)
    ref = store.put_upload(encoded(Image.new("RGB", (12, 9), "red"), format), project_id=project)
    assert ref.mime_type == "image/png"
    assert (ref.width, ref.height) == (12, 9)
    reloaded = LocalAssetStore(tmp_path)
    assert reloaded.get_ref(ref.asset_id, project_id=project) == ref
    image = reloaded.read(ref, project_id=project)
    assert image.size == (12, 9)
    assert image.mode == "RGB"
    for action in [lambda: reloaded.get_ref(ref.asset_id, project_id=uuid4()),
                   lambda: reloaded.read(ref, project_id=uuid4())]:
        with pytest.raises(GatewayError) as raised:
            action()
        assert raised.value.code == "ASSET_NOT_FOUND"


def test_upload_preserves_alpha_strips_metadata_and_rotates_exif(tmp_path):
    project = uuid4()
    image = Image.new("RGBA", (12, 9), (15, 25, 35, 123))
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private photographer"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-note", "must be stripped")
    store = LocalAssetStore(tmp_path)
    ref = store.put_upload(encoded(image, exif=exif, pnginfo=metadata), project_id=project)
    result = store.read(ref, project_id=project)
    assert result.size == (9, 12)
    assert result.getpixel((0, 0)) == (15, 25, 35, 123)
    assert not result.getexif()
    assert not result.info


def test_palette_transparency_becomes_rgba(tmp_path):
    project = uuid4()
    image = Image.new("P", (3, 3), 0)
    image.info["transparency"] = 0
    store = LocalAssetStore(tmp_path)
    ref = store.put_upload(encoded(image), project_id=project)
    assert store.read(ref, project_id=project).getpixel((1, 1))[3] == 0


def test_embedded_srgb_profile_converts_then_is_removed(tmp_path):
    project = uuid4()
    image = Image.new("RGB", (3, 3), (40, 80, 120))
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    store = LocalAssetStore(tmp_path)
    ref = store.put_upload(encoded(image, icc_profile=profile), project_id=project)
    actual = store.read(ref, project_id=project)
    assert actual.getpixel((1, 1)) == (40, 80, 120)
    assert "icc_profile" not in actual.info


@pytest.mark.parametrize("payload", [b"", b"not an image", b"\x89PNG\r\n\x1a\n"])
def test_invalid_uploads_are_rejected(tmp_path, payload):
    with pytest.raises(GatewayError) as raised:
        LocalAssetStore(tmp_path).put_upload(payload, project_id=uuid4())
    assert raised.value.code == "INVALID_IMAGE"


def test_unsupported_format_and_invalid_profile_are_rejected(tmp_path):
    image = Image.new("RGB", (4, 4), "red")
    store = LocalAssetStore(tmp_path)
    for payload in [encoded(image, "GIF"), encoded(image, icc_profile=b"broken profile")]:
        with pytest.raises(GatewayError) as raised:
            store.put_upload(payload, project_id=uuid4())
        assert raised.value.code == "INVALID_IMAGE"


def test_animated_png_is_rejected(tmp_path):
    frames = [Image.new("RGB", (4, 4), color) for color in ["red", "blue"]]
    payload = encoded(frames[0], save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(GatewayError) as raised:
        LocalAssetStore(tmp_path).put_upload(payload, project_id=uuid4())
    assert raised.value.code == "INVALID_IMAGE"


def test_byte_and_pixel_limits_are_enforced_before_decode_allocation(tmp_path):
    payload = encoded(Image.new("RGB", (11, 10), "red"))
    for options in [{"max_upload_bytes": len(payload)-1}, {"max_pixels": 100}]:
        with pytest.raises(GatewayError) as raised:
            LocalAssetStore(tmp_path, **options).put_upload(payload, project_id=uuid4())
        assert raised.value.code == "IMAGE_TOO_LARGE"
    with pytest.raises(GatewayError) as raised:
        LocalAssetStore(tmp_path, max_pixels=100).put_image(Image.new("RGB", (11, 10)), project_id=uuid4())
    assert raised.value.code == "IMAGE_TOO_LARGE"


def test_internal_image_roundtrip_does_not_mutate_input(tmp_path):
    image = Image.new("RGBA", (8, 8), (10, 20, 30, 40))
    image.info["private-note"] = "secret"
    project = uuid4()
    store = LocalAssetStore(tmp_path)
    ref = store.put_image(image, project_id=project)
    result = store.read(ref, project_id=project)
    assert result.tobytes() == image.tobytes()
    assert not result.info
    assert image.info["private-note"] == "secret"


def test_forged_ref_and_tampered_or_missing_file_are_rejected(tmp_path):
    project = uuid4()
    store = LocalAssetStore(tmp_path)
    ref = store.put_image(Image.new("RGB", (8, 8), "red"), project_id=project)
    for forged in [ref.model_copy(update={"sha256": "0"*64}),
                   ref.model_copy(update={"width": 7}),
                   ref.model_copy(update={"mime_type": "image/jpeg"})]:
        with pytest.raises(GatewayError) as raised:
            store.read(forged, project_id=project)
        assert raised.value.code == "ASSET_INTEGRITY_ERROR"
    path = tmp_path / "files" / project.hex / f"{ref.asset_id.hex}.png"
    path.write_bytes(encoded(Image.new("RGB", (8, 8), "blue")))
    with pytest.raises(GatewayError) as raised:
        store.read(ref, project_id=project)
    assert raised.value.code == "ASSET_INTEGRITY_ERROR"
    path.unlink()
    with pytest.raises(GatewayError) as raised:
        store.read(ref, project_id=project)
    assert raised.value.code == "ASSET_INTEGRITY_ERROR"


def test_asset_symlink_is_not_followed_even_when_bytes_match(tmp_path):
    project = uuid4()
    store = LocalAssetStore(tmp_path)
    ref = store.put_image(Image.new("RGB", (8, 8), "red"), project_id=project)
    path = tmp_path / "files" / project.hex / f"{ref.asset_id.hex}.png"
    other = tmp_path / "outside.png"
    path.rename(other)
    path.symlink_to(other)
    with pytest.raises(GatewayError) as raised:
        store.read(ref, project_id=project)
    assert raised.value.code == "ASSET_INTEGRITY_ERROR"


def test_metadata_database_is_authoritative_and_unpublished_file_is_invisible(tmp_path):
    store = LocalAssetStore(tmp_path)
    project, asset_id = uuid4(), uuid4()
    directory = tmp_path / "files" / project.hex
    directory.mkdir()
    (directory / f"{asset_id.hex}.png").write_bytes(encoded(Image.new("RGB", (1, 1))))
    with pytest.raises(GatewayError) as raised:
        store.get_ref(asset_id, project_id=project)
    assert raised.value.code == "ASSET_NOT_FOUND"


def test_concurrent_writes_publish_distinct_readable_assets(tmp_path):
    project = uuid4()
    store = LocalAssetStore(tmp_path)
    def save(index):
        return store.put_image(Image.new("RGB", (5, 5), (index, 0, 0)), project_id=project)
    with ThreadPoolExecutor(max_workers=4) as executor:
        refs = list(executor.map(save, range(12)))
    assert len({ref.asset_id for ref in refs}) == 12
    assert [store.read(ref, project_id=project).getpixel((0, 0))[0] for ref in refs] == list(range(12))


def test_failed_file_publication_does_not_publish_metadata(tmp_path, monkeypatch):
    store = LocalAssetStore(tmp_path)
    project = uuid4()
    def fail(*args):
        raise OSError("synthetic fsync failure")
    monkeypatch.setattr("beauty_ai.assets.os.replace", fail)
    with pytest.raises(GatewayError) as raised:
        store.put_image(Image.new("RGB", (5, 5)), project_id=project)
    assert raised.value.code == "ASSET_STORAGE_ERROR"
    with sqlite3.connect(store.database) as db:
        assert db.execute("SELECT count(*) FROM assets").fetchone()[0] == 0
    assert not list((tmp_path / "files").rglob(".asset-*"))


def test_invalid_limits_and_non_uuid_identifiers_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        LocalAssetStore(tmp_path, max_pixels=0)
    store = LocalAssetStore(tmp_path)
    with pytest.raises(ValueError):
        store.put_image(Image.new("RGB", (1, 1)), project_id="../../secret")
