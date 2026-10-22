from dataclasses import asdict
import fcntl
import hashlib
from http.client import IncompleteRead
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import threading
import urllib.error

import pytest

from beauty_ai import sdxl_model_store as store
from beauty_ai.errors import GatewayError


@pytest.fixture
def tiny_files(monkeypatch):
    payloads = {
        "model_index.json": b'{"_class_name":"StableDiffusionXLPipeline"}',
        "LICENSE.md": b"Pinned fixture license\n",
        "README.md": b"Pinned fixture model card\n",
        "unet/config.json": b'{"in_channels":4}',
        "unet/diffusion_pytorch_model.fp16.safetensors": b"small fixture weights",
        "tokenizer/merges.txt": b"fixture tokenizer data\n",
        "text_encoder/model.fp16.safetensors": b"small fixture text encoder",
    }
    monkeypatch.setattr(store, "FILES", {
        name: store.ModelFile(len(data), hashlib.sha256(data).hexdigest())
        for name, data in payloads.items()
    })
    return payloads


def populate(directory, payloads):
    directory.mkdir(parents=True)
    for name, data in payloads.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return directory


def forbid_network(*args, **kwargs):
    pytest.fail("This operation must not access the network")


def name_from_url(url):
    prefix = f"https://huggingface.co/{store.MODEL_ID}/resolve/{store.MODEL_REVISION}/"
    assert url.startswith(prefix)
    return url[len(prefix):]


def mock_download(monkeypatch, payloads):
    def download(url, *, timeout):
        assert timeout == 30
        return BytesIO(payloads[name_from_url(url)])
    monkeypatch.setattr(store.urllib.request, "urlopen", download)


def assert_unavailable(operation):
    with pytest.raises(GatewayError) as error:
        operation()
    assert error.value.code == "MODEL_UNAVAILABLE"
    assert str(error.value) == "The reviewed local SDXL model is unavailable or invalid."


def assert_no_partial(root):
    directory = store.model_directory(root)
    assert not directory.exists()
    assert list(directory.parent.glob(".staging-*")) == []


def test_public_manifest_matches_exact_pinned_runtime_and_license_files():
    path = Path(__file__).parents[1] / "docs/modelManifests/sdxl-base-fp16.json"
    manifest = json.loads(path.read_text())
    assert manifest["model_id"] == store.MODEL_ID
    assert manifest["revision"] == store.MODEL_REVISION
    assert manifest["files"] == {name: asdict(value) for name, value in store.FILES.items()}
    assert len(store.FILES) == 20
    assert manifest["runtime_file_count"] == 18
    assert manifest["runtime_total_bytes"] == 6941187536
    assert manifest["total_bytes"] == sum(value.size for value in store.FILES.values()) == 6941210313
    assert {name for name in store.FILES if name.endswith(".safetensors")} == {
        "text_encoder/model.fp16.safetensors", "text_encoder_2/model.fp16.safetensors",
        "unet/diffusion_pytorch_model.fp16.safetensors", "vae/diffusion_pytorch_model.fp16.safetensors",
    }
    assert "LICENSE.md" in store.FILES and "README.md" in store.FILES


def test_verify_nested_snapshot_without_network_or_model_import(tmp_path, tiny_files, monkeypatch):
    directory = populate(store.model_directory(tmp_path), tiny_files)
    monkeypatch.setattr(store.urllib.request, "urlopen", forbid_network)
    assert store.verify_model_directory(directory) == directory.resolve()
    assert list(directory.rglob("__pycache__")) == []


@pytest.mark.parametrize("corruption", ["same_size", "short", "long", "missing", "directory", "fifo"])
def test_nested_weights_missing_corrupt_or_special_are_rejected(tmp_path, tiny_files, corruption):
    directory = populate(store.model_directory(tmp_path), tiny_files)
    target = directory / "unet/diffusion_pytorch_model.fp16.safetensors"
    if corruption == "same_size":
        target.write_bytes(b"X" * target.stat().st_size)
    elif corruption == "short":
        target.write_bytes(target.read_bytes()[:-1])
    elif corruption == "long":
        target.write_bytes(target.read_bytes() + b"X")
    else:
        target.unlink()
        if corruption == "directory":
            target.mkdir()
        elif corruption == "fifo":
            os.mkfifo(target)
    assert_unavailable(lambda: store.verify_model_directory(directory))


@pytest.mark.parametrize("extra", ["custom.py", "unet/config.local.json", "tokenizer/extra", "unused_directory"])
def test_extra_root_and_nested_files_or_directories_are_rejected(tmp_path, tiny_files, extra):
    directory = populate(store.model_directory(tmp_path), tiny_files)
    target = directory / extra
    if extra == "unused_directory":
        target.mkdir()
    else:
        target.write_bytes(b"unreviewed")
    assert_unavailable(lambda: store.verify_model_directory(directory))


@pytest.mark.parametrize("path", ["LICENSE.md", "README.md", "unet/config.json"])
def test_license_card_and_config_are_integrity_checked(tmp_path, tiny_files, path):
    directory = populate(store.model_directory(tmp_path), tiny_files)
    target = directory / path
    target.write_bytes(b"X" * target.stat().st_size)
    assert_unavailable(lambda: store.verify_model_directory(directory))


@pytest.mark.parametrize("link_kind", ["file", "nested_directory", "snapshot", "managed_parent", "root"])
def test_symlinks_cannot_escape_even_with_correct_target_bytes(tmp_path, tiny_files, link_kind):
    directory = populate(store.model_directory(tmp_path / "real"), tiny_files)
    if link_kind in {"file", "nested_directory"}:
        target = directory / ("unet/config.json" if link_kind == "file" else "unet")
        external = tmp_path / "external"
        target.rename(external)
        target.symlink_to(external, target_is_directory=link_kind == "nested_directory")
        candidate = directory
    elif link_kind == "snapshot":
        candidate = tmp_path / "linked-snapshot"
        candidate.symlink_to(directory, target_is_directory=True)
    elif link_kind == "managed_parent":
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(directory.parent, target_is_directory=True)
        candidate = linked_parent / store.MODEL_REVISION
    else:
        linked_root = tmp_path / "linked-root"
        linked_root.symlink_to(directory.parent.parent, target_is_directory=True)
        candidate = store.model_directory(linked_root)
    assert_unavailable(lambda: store.verify_model_directory(candidate))


@pytest.mark.parametrize("replacement", ["file", "fifo"])
def test_expected_directory_cannot_be_file_or_fifo(tmp_path, tiny_files, replacement):
    directory = populate(store.model_directory(tmp_path), tiny_files)
    nested = directory / "unet"
    shutil.rmtree(nested)
    if replacement == "file":
        nested.write_bytes(b"not a directory")
    else:
        os.mkfifo(nested)
    assert_unavailable(lambda: store.verify_model_directory(directory))


def test_missing_verify_does_not_download(tmp_path, monkeypatch):
    monkeypatch.setattr(store.urllib.request, "urlopen", forbid_network)
    assert_unavailable(lambda: store.verify_model_directory(store.model_directory(tmp_path)))


def test_prepare_publishes_complete_nested_snapshot_then_reuses_without_network(tmp_path, tiny_files, monkeypatch):
    seen = []
    def download(url, *, timeout):
        name = name_from_url(url)
        assert timeout == 30
        assert not store.model_directory(tmp_path).exists()
        seen.append(name)
        return BytesIO(tiny_files[name])
    monkeypatch.setattr(store.urllib.request, "urlopen", download)
    directory = store.prepare_model(tmp_path)
    assert directory == store.model_directory(tmp_path)
    assert sorted(seen) == sorted(tiny_files)
    for name, data in tiny_files.items():
        assert (directory / name).read_bytes() == data
        assert (directory / name).stat().st_mode & 0o222 == 0
    monkeypatch.setattr(store.urllib.request, "urlopen", forbid_network)
    assert store.prepare_model(tmp_path) == directory
    assert list(directory.parent.glob(".staging-*")) == []


@pytest.mark.parametrize("failure", ["short", "oversized", "hash", "network", "truncated_http", "timeout"])
def test_failed_download_cleans_staging_and_never_publishes(tmp_path, tiny_files, monkeypatch, failure):
    class BrokenResponse(BytesIO):
        def read(self, size=-1):
            raise IncompleteRead(b"private response payload", 10)
    def download(url, *, timeout):
        name = name_from_url(url)
        data = tiny_files[name]
        if name == "unet/diffusion_pytorch_model.fp16.safetensors":
            if failure == "short":
                data = data[:-1]
            elif failure == "oversized":
                data += b"overflow"
            elif failure == "hash":
                data = b"X" * len(data)
            elif failure == "network":
                raise urllib.error.URLError("private signed URL")
            elif failure == "timeout":
                raise TimeoutError("private server address")
            else:
                return BrokenResponse()
        return BytesIO(data)
    monkeypatch.setattr(store.urllib.request, "urlopen", download)
    assert_unavailable(lambda: store.prepare_model(tmp_path))
    assert_no_partial(tmp_path)


def test_oversize_stream_is_bounded_to_expected_size_plus_one(tmp_path, tiny_files, monkeypatch):
    observed = []
    class OversizedResponse(BytesIO):
        def __init__(self, name):
            super().__init__(b"X" * 100000)
            self.name = name
        def read(self, size=-1):
            observed.append((self.name, size))
            return super().read(size)
    monkeypatch.setattr(store.urllib.request, "urlopen", lambda url, **kwargs: OversizedResponse(name_from_url(url)))
    assert_unavailable(lambda: store.prepare_model(tmp_path))
    assert observed
    assert all(size == len(tiny_files[name]) + 1 for name, size in observed)
    assert_no_partial(tmp_path)


def test_parallel_failure_joins_all_open_responses_before_cleanup(tmp_path, tiny_files, monkeypatch):
    # First four workers enter a read together; one fails while the others still
    # own response/file handles. Cleanup must wait for all of those handles.
    barrier = threading.Barrier(4, timeout=5)
    guard = threading.Lock()
    active = 0
    peak = 0
    entered = 0
    class Response(BytesIO):
        def __enter__(self):
            nonlocal active, peak, entered
            with guard:
                active += 1
                entered += 1
                peak = max(peak, active)
                self.number = entered
            return self
        def read(self, size=-1):
            if self.number <= 4 and not self.tell():
                barrier.wait()
                if self.number == 1:
                    raise IncompleteRead(b"private payload", 10)
            return super().read(size)
        def __exit__(self, *args):
            nonlocal active
            with guard:
                active -= 1
            return super().__exit__(*args)
    def download(url, **kwargs):
        return Response(tiny_files[name_from_url(url)])
    cleanup = store.shutil.rmtree
    def checked_cleanup(*args, **kwargs):
        assert active == 0
        return cleanup(*args, **kwargs)
    monkeypatch.setattr(store.urllib.request, "urlopen", download)
    monkeypatch.setattr(store.shutil, "rmtree", checked_cleanup)
    assert_unavailable(lambda: store.prepare_model(tmp_path))
    assert peak == 4
    assert active == 0
    assert_no_partial(tmp_path)


def test_invalid_existing_snapshot_is_never_replaced_or_downloaded(tmp_path, tiny_files, monkeypatch):
    directory = populate(store.model_directory(tmp_path), tiny_files)
    target = directory / "unet/config.json"
    target.write_bytes(b"preserve this invalid existing file")
    monkeypatch.setattr(store.urllib.request, "urlopen", forbid_network)
    assert_unavailable(lambda: store.prepare_model(tmp_path))
    assert target.read_bytes() == b"preserve this invalid existing file"


@pytest.mark.parametrize("linked", ["root", "managed_parent"])
def test_prepare_refuses_symlinked_store_before_writes(tmp_path, monkeypatch, linked):
    external = tmp_path / "external"
    external.mkdir()
    if linked == "root":
        root = tmp_path / "linked-root"
        root.symlink_to(external, target_is_directory=True)
    else:
        root = tmp_path
        (root / "sdxl-base").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(store.urllib.request, "urlopen", forbid_network)
    assert_unavailable(lambda: store.prepare_model(root))
    assert list(external.iterdir()) == []


def test_busy_preparer_fails_without_wait_or_network(tmp_path, monkeypatch):
    parent = store.model_directory(tmp_path).parent
    parent.mkdir(parents=True)
    with (parent / ".prepare.lock").open("wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(store.urllib.request, "urlopen", forbid_network)
        assert_unavailable(lambda: store.prepare_model(tmp_path))


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_prepare_lock_must_be_regular_non_symlink_file(tmp_path, monkeypatch, kind):
    parent = store.model_directory(tmp_path).parent
    parent.mkdir(parents=True)
    lock = parent / ".prepare.lock"
    if kind == "symlink":
        target = tmp_path / "external-lock"
        target.write_text("do not change")
        lock.symlink_to(target)
    else:
        os.mkfifo(lock)
    monkeypatch.setattr(store.urllib.request, "urlopen", forbid_network)
    assert_unavailable(lambda: store.prepare_model(tmp_path))


def test_destination_created_during_download_is_preserved(tmp_path, tiny_files, monkeypatch):
    directory = store.model_directory(tmp_path)
    def download(url, **kwargs):
        name = name_from_url(url)
        if name == "model_index.json":
            directory.mkdir()
            (directory / "user-file").write_bytes(b"preserve")
        return BytesIO(tiny_files[name])
    monkeypatch.setattr(store.urllib.request, "urlopen", download)
    assert_unavailable(lambda: store.prepare_model(tmp_path))
    assert (directory / "user-file").read_bytes() == b"preserve"
    assert list(directory.parent.glob(".staging-*")) == []


def test_publish_failure_removes_staging(tmp_path, tiny_files, monkeypatch):
    mock_download(monkeypatch, tiny_files)
    def failed_rename(*args):
        raise OSError("private staging path")
    monkeypatch.setattr(store.os, "rename", failed_rename)
    assert_unavailable(lambda: store.prepare_model(tmp_path))
    assert_no_partial(tmp_path)


def test_cli_success_outputs_only_source_revision_status(tmp_path, tiny_files, capsys):
    populate(store.model_directory(tmp_path), tiny_files)
    assert store.main(["verify", "--root", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {"source": store.MODEL_ID, "revision": store.MODEL_REVISION, "status": "VERIFIED"}


def test_cli_http_exception_never_discloses_response_or_path(tmp_path, tiny_files, monkeypatch, capsys):
    class BrokenResponse(BytesIO):
        def read(self, size=-1):
            raise IncompleteRead(b"private response and token", 500)
    monkeypatch.setattr(store.urllib.request, "urlopen", lambda *args, **kwargs: BrokenResponse())
    assert store.main(["prepare", "--root", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error_code": "MODEL_UNAVAILABLE"}
    assert_no_partial(tmp_path)


@pytest.mark.parametrize("args", [["verify", "--root", "/private/token-do-not-echo"], ["--private-token"], ["prepare", "--unexpected-private"]])
def test_cli_invalid_arguments_and_paths_are_sanitized(args, capsys):
    assert store.main(args) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error_code": "MODEL_UNAVAILABLE"}
