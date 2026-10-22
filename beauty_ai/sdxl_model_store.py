"""Prepare and verify the fixed SDXL fp16 snapshot without runtime downloads.

The server owns this model root and prevents concurrent external modification.
Hash checks establish integrity; they do not sandbox an untrusted filesystem.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import fcntl
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
import threading
from types import MappingProxyType
import urllib.error
import urllib.request

from .errors import GatewayError


MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
MODEL_REVISION = "462165984030d82259a11f4367a4eed129e94a7b"
_CHUNK_BYTES = 64 * 1024
_NETWORK_TIMEOUT = 30
_DOWNLOAD_WORKERS = 4


@dataclass(frozen=True)
class ModelFile:
    size: int
    sha256: str


# The publisher's pinned LFS metadata supplies weight hashes; downloaded small
# files were hashed independently. The public JSON manifest mirrors this list.
FILES = MappingProxyType({
    "model_index.json": ModelFile(609, "6d7b93508390ab91ac5bfbe4aeb4dc2d83f7bb1b05fb069d714b5b0c75f70d44"),
    "scheduler/scheduler_config.json": ModelFile(479, "af3e45a949aff8b8341ab8b811429ec03fee857a700a1d9477363e4fff9666e2"),
    "text_encoder/config.json": ModelFile(565, "39b8b2e4b1949e36969caa425b6c81c68bace99198dd9078ce05d16ad401fe7f"),
    "text_encoder_2/config.json": ModelFile(575, "a892d1c3a69a7e9247a24de2bc1d5891e3109a54696e53be20093af671072c34"),
    "unet/config.json": ModelFile(1680, "30ebc70750223e59006f7f2b4e1e6c102570aa19a9c4ae3e1fbe7591332dbae6"),
    "vae/config.json": ModelFile(642, "0b331c8ac22ded5f9997a144a575c1d113d6169aff262c353f39015bd24a6264"),
    "tokenizer/merges.txt": ModelFile(524619, "9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a"),
    "tokenizer/special_tokens_map.json": ModelFile(472, "c4864a9376a8401918425bed71fc14fc0e81f9b59ec45c1cf96cccb2df508eac"),
    "tokenizer/tokenizer_config.json": ModelFile(737, "19d7b034cb0cc3ce9766c2231373ab8aa8991fc72e2c8f76558bfaae3de0d563"),
    "tokenizer/vocab.json": ModelFile(1059962, "e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349"),
    "tokenizer_2/merges.txt": ModelFile(524619, "9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a"),
    "tokenizer_2/special_tokens_map.json": ModelFile(460, "f118ab3a983206e4f32583448de6bd6aae4ee21869135cef1f5848a753cdaab6"),
    "tokenizer_2/tokenizer_config.json": ModelFile(725, "c9d23941f76a41cbd50eda9290f57be7828f0a7a677939e9ef181f7e12bd1bdf"),
    "tokenizer_2/vocab.json": ModelFile(1059962, "e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349"),
    "text_encoder/model.fp16.safetensors": ModelFile(246144152, "660c6f5b1abae9dc498ac2d21e1347d2abdb0cf6c0c0c8576cd796491d9a6cdd"),
    "text_encoder_2/model.fp16.safetensors": ModelFile(1389382176, "ec310df2af79c318e24d20511b601a591ca8cd4f1fce1d8dff822a356bcdb1f4"),
    "unet/diffusion_pytorch_model.fp16.safetensors": ModelFile(5135149760, "83e012a805b84c7ca28e5646747c90a243c65c8ba4f070e2d7ddc9d74661e139"),
    "vae/diffusion_pytorch_model.fp16.safetensors": ModelFile(167335342, "bcb60880a46b63dea58e9bc591abe15f8350bde47b405f9c38f4be70c6161e68"),
    "LICENSE.md": ModelFile(14109, "19b6998b569b53ac1fc2158a8a3202c8699a9a4605b47075715d9c96be7fb6d0"),
    "README.md": ModelFile(8668, "40d263065b8a3e9a41996c257e11463fd97ae956ebf4e3f8b9625e0d78183893"),
})


def _unavailable() -> GatewayError:
    return GatewayError("MODEL_UNAVAILABLE", "The reviewed local SDXL model is unavailable or invalid.")


def model_directory(root: str | os.PathLike[str]) -> Path:
    return Path(root) / "sdxl-base" / MODEL_REVISION


def _expected_tree() -> dict:
    tree: dict = {}
    for name, expected in FILES.items():
        parts = PurePosixPath(name).parts
        if not parts or name != "/".join(parts) or any(part in {"", ".", "..", "/"} for part in parts) or "\\" in name:
            raise _unavailable()
        branch = tree
        for part in parts[:-1]:
            branch = branch.setdefault(part, {})
            if not isinstance(branch, dict):
                raise _unavailable()
        if parts[-1] in branch:
            raise _unavailable()
        branch[parts[-1]] = expected
    return tree


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


def _verify_tree(directory_fd: int, expected: dict) -> None:
    if set(os.listdir(directory_fd)) != set(expected):
        raise _unavailable()
    for name, child in expected.items():
        if isinstance(child, ModelFile):
            _verify_file(directory_fd, name, child)
        else:
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            try:
                _verify_tree(descriptor, child)
            finally:
                os.close(descriptor)
    if set(os.listdir(directory_fd)) != set(expected):
        raise _unavailable()


def verify_model_directory(directory: str | os.PathLike[str]) -> Path:
    """Verify every nested file without following links or accessing the network."""
    descriptor = None
    try:
        path = Path(directory)
        # The configured root and managed directories cannot be links. Higher
        # system aliases, such as macOS /tmp, are outside this store's boundary.
        if any(candidate.is_symlink() for candidate in (path, path.parent, path.parent.parent)):
            raise _unavailable()
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        _verify_tree(descriptor, _expected_tree())
        return path.resolve(strict=True)
    except (OSError, ValueError, TypeError, RuntimeError):
        raise _unavailable() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _download_file(directory: Path, name: str, expected: ModelFile, cancelled: threading.Event) -> None:
    try:
        if cancelled.is_set():
            raise _unavailable()
        url = f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{name}"
        digest = hashlib.sha256()
        total = 0
        with urllib.request.urlopen(url, timeout=_NETWORK_TIMEOUT) as response:
            with (directory / name).open("xb") as destination:
                while True:
                    if cancelled.is_set():
                        raise _unavailable()
                    chunk = response.read(min(_CHUNK_BYTES, expected.size - total + 1))
                    if not chunk:
                        break
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
    except BaseException:
        cancelled.set()
        raise


def _download_snapshot(staging: Path) -> None:
    cancelled = threading.Event()
    for name in FILES:
        (staging / name).parent.mkdir(parents=True, exist_ok=True)
    # Exiting the executor joins running writers even on failure. Only after
    # this function returns/raises may prepare_model remove the staging tree.
    with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as executor:
        pending = [executor.submit(_download_file, staging, name, expected, cancelled) for name, expected in FILES.items()]
        try:
            for future in as_completed(pending):
                future.result()
        except BaseException:
            cancelled.set()
            for future in pending:
                future.cancel()
            raise


def prepare_model(root: str | os.PathLike[str]) -> Path:
    """Prepare the fixed official snapshot; refuse to replace invalid contents.

    Four bounded download workers share cancellation. A completed snapshot is
    verified before atomic publication. Existing verified models need no network.
    """
    staging = None
    lock_fd = None
    try:
        _expected_tree()
        destination = model_directory(root)
        if os.path.lexists(destination):
            return verify_model_directory(destination)
        parent = destination.parent
        if parent.is_symlink() or parent.parent.is_symlink():
            raise _unavailable()
        parent.mkdir(parents=True, exist_ok=True)
        parent = parent.resolve(strict=True)
        destination = parent / MODEL_REVISION
        lock_fd = os.open(parent / ".prepare.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise _unavailable()
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.path.lexists(destination):
            return verify_model_directory(destination)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))
        _download_snapshot(staging)
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
    parser = _SafeArgumentParser(description="Prepare or verify the pinned local SDXL fp16 model.")
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
