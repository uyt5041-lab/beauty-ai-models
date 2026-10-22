from io import BytesIO
import fcntl
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import urllib.error

import pytest

from beauty_ai import model_store
from beauty_ai.errors import GatewayError


@pytest.fixture
def tiny_files(monkeypatch):
    payloads = {
        "birefnet.py": b"# reviewed model fixture\n",
        "BiRefNet_config.py": b"# reviewed config fixture\n",
        "config.json": b'{"bb_pretrained": false}',
        "model.safetensors": b"verified fixture weights",
    }
    monkeypatch.setattr(model_store, "FILES", {
        name: model_store.ModelFile(len(data), hashlib.sha256(data).hexdigest())
        for name, data in payloads.items()
    })
    return payloads


def populate(path, payloads):
    path.mkdir(parents=True)
    for name, data in payloads.items():
        (path / name).write_bytes(data)
    return path


def forbid_network(*args, **kwargs):
    pytest.fail("This operation must not access the network")


def assert_unavailable(operation):
    with pytest.raises(GatewayError) as error:
        operation()
    assert error.value.code == "MODEL_UNAVAILABLE"
    assert str(error.value) == "The reviewed local model is unavailable or invalid."


def test_verified_files_are_read_without_import_or_network(tmp_path, tiny_files, monkeypatch):
    directory = populate(model_store.model_directory(tmp_path), tiny_files)
    monkeypatch.setattr(model_store.urllib.request, "urlopen", forbid_network)
    assert model_store.verify_model_directory(directory) == directory.resolve()
    assert not (directory / "__pycache__").exists()


@pytest.mark.parametrize("corruption", ["same_size", "short", "long", "missing", "extra_code", "extra_config", "directory", "fifo"])
def test_tampered_missing_extra_and_special_files_are_rejected(tmp_path, tiny_files, corruption):
    directory = populate(model_store.model_directory(tmp_path), tiny_files)
    target = directory / "model.safetensors"
    if corruption == "same_size":
        target.write_bytes(b"X" * target.stat().st_size)
    elif corruption == "short":
        target.write_bytes(target.read_bytes()[:-1])
    elif corruption == "long":
        target.write_bytes(target.read_bytes() + b"X")
    elif corruption == "extra_code":
        (directory / "handler.py").write_text("raise RuntimeError('unreviewed')")
    elif corruption == "extra_config":
        (directory / "generation_config.json").write_text("{}")
    else:
        target.unlink()
        if corruption == "directory":
            target.mkdir()
        elif corruption == "fifo":
            os.mkfifo(target)
    assert_unavailable(lambda: model_store.verify_model_directory(directory))


@pytest.mark.parametrize("link_kind", ["file", "directory", "parent"])
def test_symlinks_are_rejected_even_when_target_contains_verified_bytes(tmp_path, tiny_files, link_kind):
    directory = populate(model_store.model_directory(tmp_path / "real"), tiny_files)
    if link_kind == "file":
        target = directory / "model.safetensors"
        outside = tmp_path / "outside.safetensors"
        target.rename(outside)
        target.symlink_to(outside)
        candidate = directory
    elif link_kind == "directory":
        candidate = tmp_path / "linked-snapshot"
        candidate.symlink_to(directory, target_is_directory=True)
    else:
        parent = tmp_path / "linked-store"
        parent.symlink_to(directory.parent, target_is_directory=True)
        candidate = parent / model_store.MODEL_REVISION
    assert_unavailable(lambda: model_store.verify_model_directory(candidate))


def test_missing_model_verify_never_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store.urllib.request, "urlopen", forbid_network)
    assert_unavailable(lambda: model_store.verify_model_directory(model_store.model_directory(tmp_path)))


def test_prepare_downloads_fixed_urls_then_reuses_verified_snapshot(tmp_path, tiny_files, monkeypatch):
    calls = []
    def download(url, *, timeout):
        name = url.rsplit("/", 1)[-1]
        assert url == f"https://huggingface.co/{model_store.MODEL_ID}/resolve/{model_store.MODEL_REVISION}/{name}"
        assert timeout == 30
        assert not model_store.model_directory(tmp_path).exists()
        calls.append(name)
        return BytesIO(tiny_files[name])
    monkeypatch.setattr(model_store.urllib.request, "urlopen", download)
    directory = model_store.prepare_model(tmp_path)
    assert directory == model_store.model_directory(tmp_path)
    assert set(calls) == set(tiny_files)
    assert set(path.name for path in directory.iterdir()) == set(tiny_files)
    for name, data in tiny_files.items():
        assert (directory / name).read_bytes() == data
        assert (directory / name).stat().st_mode & 0o222 == 0
    monkeypatch.setattr(model_store.urllib.request, "urlopen", forbid_network)
    assert model_store.prepare_model(tmp_path) == directory
    assert list(directory.parent.glob(".staging-*")) == []


@pytest.mark.parametrize("failure", ["short", "oversized", "wrong_hash", "network"])
def test_failed_download_never_publishes_partial_model(tmp_path, tiny_files, monkeypatch, failure):
    seen = []
    def download(url, *, timeout):
        name = url.rsplit("/", 1)[-1]
        seen.append(name)
        data = tiny_files[name]
        if name == "model.safetensors":
            if failure == "short":
                data = data[:-1]
            elif failure == "oversized":
                data += b"overflow"
            elif failure == "wrong_hash":
                data = b"X" * len(data)
            else:
                raise urllib.error.URLError("private-local-path-and-token")
        return BytesIO(data)
    monkeypatch.setattr(model_store.urllib.request, "urlopen", download)
    assert_unavailable(lambda: model_store.prepare_model(tmp_path))
    assert len(seen) == 4
    assert not model_store.model_directory(tmp_path).exists()
    assert list(model_store.model_directory(tmp_path).parent.glob(".staging-*")) == []


def test_oversized_stream_stops_after_expected_size_plus_one(tmp_path, tiny_files, monkeypatch):
    reads = []
    class OversizedResponse(BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)
    first_name = next(iter(tiny_files))
    monkeypatch.setattr(model_store.urllib.request, "urlopen", lambda *args, **kwargs: OversizedResponse(b"X" * 10000))
    assert_unavailable(lambda: model_store.prepare_model(tmp_path))
    assert reads == [len(tiny_files[first_name]) + 1]


def test_truncated_http_response_is_sanitized_and_staging_is_cleaned(tmp_path, tiny_files, monkeypatch, capsys):
    class InterruptedResponse(BytesIO):
        def read(self, size=-1):
            raise IncompleteRead(b"private-response-data", 100)
    monkeypatch.setattr(model_store.urllib.request, "urlopen", lambda *args, **kwargs: InterruptedResponse())
    assert model_store.main(["prepare", "--root", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error_code": "MODEL_UNAVAILABLE"}
    directory = model_store.model_directory(tmp_path)
    assert not directory.exists()
    assert list(directory.parent.glob(".staging-*")) == []


def test_invalid_existing_snapshot_is_never_overwritten_or_downloaded(tmp_path, tiny_files, monkeypatch):
    directory = populate(model_store.model_directory(tmp_path), tiny_files)
    damaged = directory / "birefnet.py"
    damaged.write_bytes(b"do not overwrite")
    monkeypatch.setattr(model_store.urllib.request, "urlopen", forbid_network)
    assert_unavailable(lambda: model_store.prepare_model(tmp_path))
    assert damaged.read_bytes() == b"do not overwrite"


def test_prepare_refuses_managed_parent_symlink(tmp_path, monkeypatch):
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / "birefnet-lite").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(model_store.urllib.request, "urlopen", forbid_network)
    assert_unavailable(lambda: model_store.prepare_model(tmp_path))
    assert list(external.iterdir()) == []


def test_busy_preparer_fails_without_network_or_waiting(tmp_path, tiny_files, monkeypatch):
    parent = model_store.model_directory(tmp_path).parent
    parent.mkdir(parents=True)
    with (parent / ".prepare.lock").open("wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(model_store.urllib.request, "urlopen", forbid_network)
        assert_unavailable(lambda: model_store.prepare_model(tmp_path))


def test_destination_created_during_download_is_not_replaced(tmp_path, tiny_files, monkeypatch):
    directory = model_store.model_directory(tmp_path)
    def download(url, *, timeout):
        name = url.rsplit("/", 1)[-1]
        if name == "model.safetensors":
            directory.mkdir()
            (directory / "user-file").write_bytes(b"preserve")
        return BytesIO(tiny_files[name])
    monkeypatch.setattr(model_store.urllib.request, "urlopen", download)
    assert_unavailable(lambda: model_store.prepare_model(tmp_path))
    assert (directory / "user-file").read_bytes() == b"preserve"
    assert list(directory.parent.glob(".staging-*")) == []


def test_publish_failure_cleans_staging_without_leaving_snapshot(tmp_path, tiny_files, monkeypatch):
    monkeypatch.setattr(model_store.urllib.request, "urlopen", lambda url, **kwargs: BytesIO(tiny_files[url.rsplit("/", 1)[-1]]))
    def failed_rename(*args):
        raise OSError("private staging location")
    monkeypatch.setattr(model_store.os, "rename", failed_rename)
    assert_unavailable(lambda: model_store.prepare_model(tmp_path))
    directory = model_store.model_directory(tmp_path)
    assert not directory.exists()
    assert list(directory.parent.glob(".staging-*")) == []


def test_cli_success_only_discloses_source_revision_and_status(tmp_path, tiny_files, capsys):
    populate(model_store.model_directory(tmp_path), tiny_files)
    assert model_store.main(["verify", "--root", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "source": model_store.MODEL_ID, "revision": model_store.MODEL_REVISION, "status": "VERIFIED",
    }
    assert str(tmp_path) not in output.out


@pytest.mark.parametrize("args", [["verify", "--root", "/private/token-do-not-echo"], ["--secret-token"], ["prepare", "--unexpected-private"]])
def test_cli_errors_never_echo_paths_or_unknown_arguments(args, capsys):
    assert model_store.main(args) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error_code": "MODEL_UNAVAILABLE"}
