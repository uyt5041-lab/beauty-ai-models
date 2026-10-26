"""Prepare local SSOT experiment inputs without models, credentials or network.

The first product is the primary view. Other products are supporting views of
the same item; this module validates files, not that human identity assertion.
Records retain both original-byte and normalized-PNG hashes. A failed prepare
may leave its newly created output directory for inspection; it is never reused
or removed automatically.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID

from .assets import LocalAssetStore
from .errors import GatewayError


MAX_RAW_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_PROVIDER_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PROVIDER_TOTAL_BYTES = 100 * 1024 * 1024


def _fail(code: str) -> None:
    raise ValueError(code) from None


def _absolute_without_links(path: Path) -> Path:
    if not isinstance(path, Path) or ".." in path.parts:
        _fail("EXPERIMENT_INPUT_UNSAFE_PATH")
    candidate = path.absolute()
    cursor = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            _fail("EXPERIMENT_INPUT_UNSAFE_PATH")
    return candidate


def _destination(output: Path) -> Path:
    candidate = _absolute_without_links(output)
    # The explicit output argument is the filesystem boundary: it must be a
    # descendant (not the directory itself) of a folder named exactly outputs.
    if not any(parent.name == "outputs" for parent in candidate.parents):
        _fail("EXPERIMENT_OUTPUT_OUTSIDE_OUTPUTS")
    if candidate.exists():
        _fail("EXPERIMENT_OUTPUT_EXISTS")
    return candidate


def _read_source(path: Path) -> tuple[str, bytes]:
    source = _absolute_without_links(path)
    try:
        if not source.is_file():
            _fail("EXPERIMENT_INPUT_FILE_UNAVAILABLE")
        if source.stat().st_size > MAX_RAW_BYTES:
            _fail("EXPERIMENT_INPUT_RAW_TOO_LARGE")
        # Bound the read even if a source grows after stat; never follow a
        # last-component symlink substituted between validation and open.
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(MAX_RAW_BYTES + 1)
        if len(raw) > MAX_RAW_BYTES:
            _fail("EXPERIMENT_INPUT_RAW_TOO_LARGE")
        if not raw:
            _fail("EXPERIMENT_INPUT_INVALID_IMAGE")
        if any(ord(character) < 32 for character in source.name):
            _fail("EXPERIMENT_INPUT_INVALID_NAME")
        return source.name, raw
    except OSError:
        _fail("EXPERIMENT_INPUT_FILE_UNAVAILABLE")


def prepare_inputs(products: list[Path], references: list[Path], output: Path,
                   project_id: UUID) -> list[dict]:
    """Create one new outputs/... folder and normalized, project-scoped inputs.

    Product/reference indices are independently one-based. Input order is
    preserved, with products followed by references. ``path`` is relative to
    ``output``; ``asset_ref`` identifies the exact normalized bytes in its
    runtime/assets store. Raw files and their embedded metadata are not copied.

    ``ValueError`` messages contain fixed error codes only. No provider or
    observability client is imported, configured or invoked here.
    """
    if type(products) is not list or not 2 <= len(products) <= 5:
        _fail("EXPERIMENT_PRODUCT_COUNT_INVALID")
    if type(references) is not list or not 0 <= len(references) <= 5:
        _fail("EXPERIMENT_REFERENCE_COUNT_INVALID")
    if not isinstance(project_id, UUID):
        _fail("EXPERIMENT_PROJECT_ID_INVALID")
    try:
        destination = _destination(output)
        sources = []
        raw_hashes = set()
        for role, paths in (("product", products), ("reference", references)):
            for index, path in enumerate(paths, start=1):
                name, raw = _read_source(path)
                digest = hashlib.sha256(raw).hexdigest()
                if digest in raw_hashes:
                    _fail("EXPERIMENT_DUPLICATE_RAW_INPUT")
                raw_hashes.add(digest)
                sources.append((role, index, name, raw, digest))

        # Exclusive creation also rejects an output created by another process
        # after the initial check. Never merge into an existing experiment.
        try:
            destination.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            _fail("EXPERIMENT_OUTPUT_EXISTS")
        _absolute_without_links(destination)
        inputs = destination / "inputs"
        inputs.mkdir()
        store = LocalAssetStore(destination / "runtime/assets",
                                max_upload_bytes=MAX_RAW_BYTES, max_pixels=MAX_PIXELS)
        records = []
        normalized_hashes = set()
        total_bytes = 0
        for role, index, name, raw, source_hash in sources:
            ref = store.put_upload(raw, project_id=project_id)
            normalized = store.read_bytes(ref, project_id=project_id)
            normalized_hash = hashlib.sha256(normalized).hexdigest()
            if normalized_hash != ref.sha256:
                _fail("EXPERIMENT_INPUT_HASH_MISMATCH")
            if len(normalized) > MAX_PROVIDER_IMAGE_BYTES:
                _fail("EXPERIMENT_INPUT_PROVIDER_IMAGE_TOO_LARGE")
            total_bytes += len(normalized)
            if total_bytes > MAX_PROVIDER_TOTAL_BYTES:
                _fail("EXPERIMENT_INPUT_PROVIDER_TOTAL_TOO_LARGE")
            if normalized_hash in normalized_hashes:
                _fail("EXPERIMENT_DUPLICATE_NORMALIZED_INPUT")
            normalized_hashes.add(normalized_hash)
            relative = Path("inputs") / f"{role}-{index:02d}.png"
            with (destination / relative).open("xb") as stream:
                stream.write(normalized)
                stream.flush()
                os.fsync(stream.fileno())
            records.append({"role": role, "index": index, "name": name,
                            "source_sha256": source_hash,
                            "normalized_sha256": normalized_hash,
                            "path": relative.as_posix(), "asset_ref": ref.model_dump(mode="json")})
        return records
    except GatewayError as error:
        code = {"INVALID_IMAGE": "EXPERIMENT_INPUT_INVALID_IMAGE",
                "IMAGE_TOO_LARGE": "EXPERIMENT_INPUT_DIMENSIONS_TOO_LARGE",
                "ASSET_INTEGRITY_ERROR": "EXPERIMENT_INPUT_HASH_MISMATCH"}.get(
                    error.code, "EXPERIMENT_INPUT_STORAGE_ERROR")
        _fail(code)
    except OSError:
        _fail("EXPERIMENT_INPUT_STORAGE_ERROR")
    except ValueError as error:
        if str(error).startswith("EXPERIMENT_"):
            raise
        _fail("EXPERIMENT_INPUT_INVALID")
