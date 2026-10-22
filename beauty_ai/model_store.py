"""Prepare and verify one reviewed model snapshot without runtime downloads.

Only this explicit preparation command accesses the network. The inference
adapter verifies the same four files before loading local safetensors and code.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import MappingProxyType
import urllib.error
import urllib.request

from .errors import GatewayError


MODEL_ID = "ZhengPeng7/BiRefNet_lite"
MODEL_REVISION = "aa62cd87eafb9cc43056d08ef3615a14628b831d"
_CHUNK_BYTES = 64 * 1024
_NETWORK_TIMEOUT = 30


@dataclass(frozen=True)
class ModelFile:
    size: int
    sha256: str


# Reviewed upstream code/config and publisher LFS metadata at MODEL_REVISION.
# handler.py is deliberately excluded: it fetches URLs and unpinned models.
FILES = MappingProxyType({
    "birefnet.py": ModelFile(92134, "af8568b5be406bf4d2a68a7ed6d72e40f73b37a1fb6fc9ebd71b5b3cbcd069c9"),
    "BiRefNet_config.py": ModelFile(298, "e7b8c2a74f6cea6a59553d517f71d47f2c1d90e670a13416af17c25fe2f3dc52"),
    "config.json": ModelFile(410, "9dc8614fccddd40c601aeadc69b9db6dd820598179b2a2198492e6ffa016a824"),
    "model.safetensors": ModelFile(177634392, "4417d89795250e698c3cb0ae8df15743810065f646f48a694fdfa7ca052d0815"),
})


def _unavailable() -> GatewayError:
    return GatewayError("MODEL_UNAVAILABLE", "The reviewed local model is unavailable or invalid.")


def model_directory(root: str | os.PathLike[str]) -> Path:
    return Path(root) / "birefnet-lite" / MODEL_REVISION


def _verify_file(directory_fd: int, name: str, expected: ModelFile) -> None:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected.size:
            raise _unavailable()
        digest = hashlib.sha256()
        total = 0
        while chunk := source.read(min(_CHUNK_BYTES, expected.size - total + 1)):
            total += len(chunk)
            if total > expected.size:
                raise _unavailable()
            digest.update(chunk)
        if total != expected.size or digest.hexdigest() != expected.sha256:
            raise _unavailable()


def verify_model_directory(directory: str | os.PathLike[str]) -> Path:
    """Verify exact contents, refusing symlinks, special files and extra code.

    Hashing is streamed and does not import model code or contact the network.
    The caller owns the model root and must prevent concurrent modification
    between verification and inference; this is an integrity check, not a sandbox.
    """
    descriptor = None
    try:
        path = Path(directory)
        # Reject links in the managed store directory as well as the snapshot.
        # The server-configured root's ancestors may use normal OS aliases.
        if path.is_symlink() or path.parent.is_symlink():
            raise _unavailable()
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if set(os.listdir(descriptor)) != set(FILES):
            raise _unavailable()
        for name, expected in FILES.items():
            _verify_file(descriptor, name, expected)
        if set(os.listdir(descriptor)) != set(FILES):
            raise _unavailable()
        return path.resolve(strict=True)
    except (OSError, ValueError, TypeError, RuntimeError):
        raise _unavailable() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _download_file(directory: Path, name: str, expected: ModelFile) -> None:
    url = f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{name}"
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(url, timeout=_NETWORK_TIMEOUT) as response:
        with (directory / name).open("xb") as destination:
            while chunk := response.read(min(_CHUNK_BYTES, expected.size - total + 1)):
                total += len(chunk)
                if total > expected.size:
                    raise _unavailable()
                destination.write(chunk)
                digest.update(chunk)
            if total != expected.size or digest.hexdigest() != expected.sha256:
                raise _unavailable()
            destination.flush()
            os.fsync(destination.fileno())
    (directory / name).chmod(0o444)


def prepare_model(root: str | os.PathLike[str]) -> Path:
    """Download the fixed snapshot once and publish only a verified directory.

    An existing invalid snapshot is never overwritten. Concurrent preparers use
    a nonblocking local file lock; a busy preparation returns MODEL_UNAVAILABLE.
    """
    staging = None
    lock_fd = None
    try:
        destination = model_directory(root)
        if os.path.lexists(destination):
            return verify_model_directory(destination)
        parent = destination.parent
        if parent.is_symlink():
            raise _unavailable()
        parent.mkdir(parents=True, exist_ok=True)
        parent = parent.resolve(strict=True)
        destination = parent / MODEL_REVISION
        lock_fd = os.open(parent / ".prepare.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise _unavailable()
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Another completed preparation may have published before we took the lock.
        if os.path.lexists(destination):
            return verify_model_directory(destination)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))
        for name, expected in FILES.items():
            _download_file(staging, name, expected)
        verify_model_directory(staging)
        if os.path.lexists(destination):
            raise _unavailable()
        os.rename(staging, destination)
        staging = None
        return verify_model_directory(destination)
    except (OSError, ValueError, TypeError, RuntimeError, urllib.error.URLError, HTTPException):
        raise _unavailable() from None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if lock_fd is not None:
            os.close(lock_fd)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _unavailable()


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(description="Prepare or verify the pinned local BiRefNet Lite model.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "verify"):
        child = commands.add_parser(command)
        child.add_argument("--root", default="models")
    try:
        args = parser.parse_args(argv)
        if args.command == "prepare":
            prepare_model(args.root)
        else:
            verify_model_directory(model_directory(args.root))
    except GatewayError as exc:
        print(json.dumps({"error_code": exc.code}), file=sys.stderr)
        return 2
    print(json.dumps({"source": MODEL_ID, "revision": MODEL_REVISION, "status": "VERIFIED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
