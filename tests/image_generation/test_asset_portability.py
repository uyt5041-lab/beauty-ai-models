from contextlib import contextmanager
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import stat
from uuid import uuid4

from PIL import Image, ImageCms, ImageOps
import pytest

from src.image_generation import assets
from src.image_generation.assets import LocalAssetStore
from src.image_generation.errors import GatewayError


def encode(image, format="PNG", **kwargs):
    stream = BytesIO()
    image.save(stream, format=format, **kwargs)
    return stream.getvalue()


def mpo_payload(*, exif=None, icc_profile=None):
    first = Image.new("RGB", (13, 9), (190, 30, 60))
    second = Image.new("RGB", (7, 5), (15, 130, 210))
    kwargs = {"save_all": True, "append_images": [second], "quality": 95}
    if exif is not None:
        kwargs["exif"] = exif
    if icc_profile is not None:
        kwargs["icc_profile"] = icc_profile
    return encode(first, "MPO", **kwargs)


def row_count(store):
    with sqlite3.connect(store.database) as db:
        return db.execute("SELECT count(*) FROM assets").fetchone()[0]


def test_mpo_upload_selects_primary_frame_and_reopens_as_plain_png(tmp_path):
    payload = mpo_payload()
    with Image.open(BytesIO(payload)) as source:
        assert source.format == "MPO" and source.n_frames == 2
        source.seek(0)
        expected = source.copy()
        source.seek(1)
        assert source.size == (7, 5)
    project = uuid4()
    store = LocalAssetStore(tmp_path)
    ref = store.put_upload(payload, project_id=project)
    restarted = LocalAssetStore(tmp_path)
    result = restarted.read(ref, project_id=project)
    assert result.size == (13, 9)
    assert result.tobytes() == expected.tobytes()
    with Image.open(BytesIO(restarted.read_bytes(ref, project_id=project))) as png:
        assert png.format == "PNG"
        assert getattr(png, "n_frames", 1) == 1
        assert not png.info
    assert row_count(store) == 1


def test_mpo_keeps_exif_and_icc_until_normalization_then_strips_metadata(tmp_path):
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private metadata fixture"
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    payload = mpo_payload(exif=exif, icc_profile=profile)
    with Image.open(BytesIO(payload)) as original:
        original.seek(0)
        upright = ImageOps.exif_transpose(original)
        expected = ImageCms.profileToProfile(
            upright, ImageCms.ImageCmsProfile(BytesIO(profile)),
            ImageCms.createProfile("sRGB"), outputMode="RGB")
    store = LocalAssetStore(tmp_path)
    project = uuid4()
    ref = store.put_upload(payload, project_id=project)
    result = store.read(ref, project_id=project)
    assert result.size == (9, 13)
    assert result.tobytes() == expected.tobytes()
    assert not result.info and not result.getexif()


@pytest.mark.parametrize("profile", [
    b"invalid profile",
    ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes(),
])
def test_mpo_bad_or_incompatible_profile_is_not_silently_dropped(tmp_path, profile):
    store = LocalAssetStore(tmp_path)
    with pytest.raises(GatewayError) as error:
        store.put_upload(mpo_payload(icc_profile=profile), project_id=uuid4())
    assert error.value.code == "INVALID_IMAGE"
    assert row_count(store) == 0


def test_mpo_upload_byte_and_primary_pixel_limits_before_load(tmp_path, monkeypatch):
    payload = mpo_payload()
    with monkeypatch.context() as patch:
        def unexpected_load(*args, **kwargs):
            raise AssertionError("Oversized primary frame must be rejected before loading")
        patch.setattr(Image.Image, "load", unexpected_load)
        for name, options in [
            ("bytes", {"max_upload_bytes": len(payload) - 1}),
            ("pixels", {"max_pixels": 13 * 9 - 1}),
        ]:
            store = LocalAssetStore(tmp_path / name, **options)
            with pytest.raises(GatewayError) as error:
                store.put_upload(payload, project_id=uuid4())
            assert error.value.code == "IMAGE_TOO_LARGE"
            assert row_count(store) == 0
    store = LocalAssetStore(tmp_path / "boundary", max_upload_bytes=len(payload), max_pixels=13 * 9)
    ref = store.put_upload(payload, project_id=uuid4())
    assert (ref.width, ref.height) == (13, 9)


def test_truncated_mpo_is_rejected_without_published_asset(tmp_path):
    store = LocalAssetStore(tmp_path)
    payload = mpo_payload()
    with pytest.raises(GatewayError) as error:
        store.put_upload(payload[: len(payload) // 4], project_id=uuid4())
    assert error.value.code == "INVALID_IMAGE"
    assert row_count(store) == 0


def test_decoded_multiframe_input_still_requires_explicit_upload_path(tmp_path):
    with Image.open(BytesIO(mpo_payload())) as source:
        source.seek(1)
        with pytest.raises(GatewayError) as error:
            LocalAssetStore(tmp_path).put_image(source, project_id=uuid4())
        assert error.value.code == "INVALID_IMAGE"
        assert source.tell() == 1


@pytest.mark.parametrize("format", ["PNG", "WEBP"])
def test_mpo_exception_does_not_allow_animated_uploads(tmp_path, format):
    frames = [Image.new("RGB", (5, 5), color) for color in ["red", "blue"]]
    payload = encode(frames[0], format, save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(GatewayError) as error:
        LocalAssetStore(tmp_path).put_upload(payload, project_id=uuid4())
    assert error.value.code == "INVALID_IMAGE"


def test_windows_directory_sync_is_skipped_without_opening_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(assets.sys, "platform", "win32")
    def unexpected(*args, **kwargs):
        raise AssertionError("Windows must not attempt POSIX directory sync")
    monkeypatch.setattr(assets.os, "open", unexpected)
    monkeypatch.setattr(assets.os, "fsync", unexpected)
    LocalAssetStore._sync_directory(tmp_path)


def test_windows_still_syncs_and_closes_file_before_replace(tmp_path, monkeypatch):
    store = LocalAssetStore(tmp_path)
    monkeypatch.setattr(assets.sys, "platform", "win32")
    events = []
    opened = []
    real_named = assets.tempfile.NamedTemporaryFile
    real_sync = os.fsync
    real_replace = os.replace
    def named(*args, **kwargs):
        file = real_named(*args, **kwargs)
        opened.append(file)
        return file
    def sync(fd):
        assert stat.S_ISREG(os.fstat(fd).st_mode)
        events.append("file_fsync")
        return real_sync(fd)
    def replace(source, destination):
        assert opened[0].closed
        assert events == ["file_fsync"]
        assert row_count(store) == 0
        events.append("replace")
        return real_replace(source, destination)
    monkeypatch.setattr(assets.tempfile, "NamedTemporaryFile", named)
    monkeypatch.setattr(assets.os, "fsync", sync)
    monkeypatch.setattr(assets.os, "replace", replace)
    project = uuid4()
    ref = store.put_image(Image.new("RGB", (5, 5), "red"), project_id=project)
    assert events == ["file_fsync", "replace"]
    assert row_count(store) == 1
    assert store.read(ref, project_id=project).size == (5, 5)


@pytest.mark.parametrize("fault", ["file_fsync", "replace", "mkdir", "db_commit"])
def test_windows_real_storage_failures_are_not_ignored(tmp_path, monkeypatch, fault):
    store = LocalAssetStore(tmp_path)
    project = uuid4()
    monkeypatch.setattr(assets.sys, "platform", "win32")
    def denied(*args, **kwargs):
        raise PermissionError("synthetic storage denial")
    if fault == "file_fsync":
        monkeypatch.setattr(assets.os, "fsync", denied)
    elif fault == "replace":
        monkeypatch.setattr(assets.os, "replace", denied)
    elif fault == "mkdir":
        real_mkdir = Path.mkdir
        def mkdir(path, *args, **kwargs):
            if path == store.files_dir / project.hex:
                denied()
            return real_mkdir(path, *args, **kwargs)
        monkeypatch.setattr(Path, "mkdir", mkdir)
    else:
        real_connection = store._connection
        class CommitFailure:
            def __init__(self, db):
                self.db = db
            def execute(self, *args):
                return self.db.execute(*args)
            def commit(self):
                raise sqlite3.OperationalError("synthetic commit failure")
        @contextmanager
        def connection():
            with real_connection() as db:
                yield CommitFailure(db)
        monkeypatch.setattr(store, "_connection", connection)
    with pytest.raises(GatewayError) as error:
        store.put_image(Image.new("RGB", (5, 5)), project_id=project)
    assert error.value.code == "ASSET_STORAGE_ERROR"
    assert row_count(store) == 0
    assert not list(store.files_dir.rglob(".asset-*"))
    assert len(list(store.files_dir.rglob("*.png"))) == (1 if fault == "db_commit" else 0)


@pytest.mark.parametrize("failed_directory_sync", [1, 2])
def test_posix_directory_sync_failure_is_not_ignored(tmp_path, monkeypatch, failed_directory_sync):
    store = LocalAssetStore(tmp_path)
    monkeypatch.setattr(assets.sys, "platform", "linux")
    real_sync = os.fsync
    directory_calls = 0
    def sync(fd):
        nonlocal directory_calls
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_calls += 1
            if directory_calls == failed_directory_sync:
                raise PermissionError("synthetic directory sync denial")
        return real_sync(fd)
    monkeypatch.setattr(assets.os, "fsync", sync)
    with pytest.raises(GatewayError) as error:
        store.put_image(Image.new("RGB", (5, 5)), project_id=uuid4())
    assert error.value.code == "ASSET_STORAGE_ERROR"
    assert row_count(store) == 0
    assert not list(store.files_dir.rglob(".asset-*"))
    assert len(list(store.files_dir.rglob("*.png"))) == (1 if failed_directory_sync == 2 else 0)
