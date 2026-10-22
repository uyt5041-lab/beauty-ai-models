"""Project-scoped image selection with optimistic revisions and verified reuse.

This store neither submits model jobs nor changes ad copy or its review state.
The caller authenticates the project and validates profiles/product approval.
Stored selections are metadata; result files must still be checked when read.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import UUID

from pydantic import Field, ValidationError

from .assets import LocalAssetStore
from .contracts import BackgroundRequest, BackgroundResult, Contract, content_hash
from .errors import GatewayError
from .image_preparation import inspect_composite
from .job_ledger import JobLedger
from .product_approval import ProductApprovalStore


class ImageSelection(Contract):
    campaign_id: UUID
    revision: int = Field(ge=1, le=2**63 - 1)
    copy_revision: int = Field(ge=1, le=2**63 - 1)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request: BackgroundRequest
    active_job_id: UUID | None


class ImageEditStore:
    """SQLite is the authority for current selection and its image cache key."""

    def __init__(self, path: str | Path, assets: LocalAssetStore, ledger: JobLedger):
        self.path = Path(path).resolve()
        self.assets = assets
        self.ledger = ledger
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""CREATE TABLE IF NOT EXISTS image_selections (
                    project_id TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    copy_revision INTEGER NOT NULL CHECK(copy_revision >= 1),
                    request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    active_job_id TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, campaign_id)
                )""")
                db.execute("""CREATE TABLE IF NOT EXISTS image_cache (
                    project_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    PRIMARY KEY(project_id, request_hash)
                )""")
        except (OSError, sqlite3.Error) as exc:
            raise self._storage_error() from exc

    @staticmethod
    def _storage_error(*, retryable: bool = True) -> GatewayError:
        return GatewayError("EDIT_STORAGE_ERROR", "Image selection storage is unavailable or inconsistent.",
                            retryable=retryable)

    @staticmethod
    def _conflict() -> GatewayError:
        return GatewayError("EDIT_CONFLICT", "Image selection revision or inputs changed.")

    @staticmethod
    def _invalid() -> GatewayError:
        return GatewayError("INVALID_IMAGE_REQUEST", "Image selection assets or inputs are invalid.")

    @staticmethod
    def _not_found() -> GatewayError:
        return GatewayError("EDIT_NOT_FOUND", "Image selection was not found in this project.")

    @staticmethod
    def _not_ready() -> GatewayError:
        return GatewayError("IMAGE_JOB_NOT_READY", "A verified successful background job is required.")

    @staticmethod
    def _validate_ids(campaign_id: UUID, project_id: UUID) -> None:
        if not isinstance(campaign_id, UUID) or not isinstance(project_id, UUID):
            raise ImageEditStore._invalid()

    @staticmethod
    def _revision(value: int, *, minimum: int, maximum: int = 2**63 - 1) -> None:
        if type(value) is not int or value < minimum or value > maximum:
            raise ImageEditStore._invalid()

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @classmethod
    def _decode(cls, row: sqlite3.Row) -> ImageSelection:
        try:
            UUID(row["project_id"])
            request = BackgroundRequest.model_validate_json(row["request_json"])
            if content_hash(request) != row["request_hash"]:
                raise ValueError("Stored image request changed")
            return ImageSelection(campaign_id=row["campaign_id"], revision=row["revision"],
                copy_revision=row["copy_revision"], request_hash=row["request_hash"],
                request=request, active_job_id=row["active_job_id"])
        except (ValueError, TypeError, ValidationError) as exc:
            raise cls._storage_error(retryable=False) from exc

    def _read(self, reference, project_id):
        try:
            return self.assets.read(reference, project_id=project_id)
        except GatewayError as exc:
            if exc.code == "ASSET_STORAGE_ERROR":
                raise self._storage_error() from exc
            raise self._invalid() from exc

    def _verify_inputs(self, request: BackgroundRequest, project_id: UUID) -> None:
        try:
            with self._read(request.source, project_id), \
                    self._read(request.product_rgba, project_id) as product, \
                    self._read(request.preserve_mask, project_id) as mask:
                # Share the runtime's internal layer invariant, without granting approval.
                ProductApprovalStore._validate_layers(product, mask, request.visual)
        except (ValueError, TypeError, OSError) as exc:
            raise self._invalid() from exc

    def _verify_result(self, job_id: UUID, project_id: UUID, request: BackgroundRequest) -> None:
        try:
            job = self.ledger.get(job_id, project_id)
        except GatewayError as exc:
            if exc.code == "IMAGE_JOB_NOT_FOUND":
                raise self._not_ready() from exc
            raise self._storage_error(retryable=exc.retryable) from exc
        if job.request_hash != content_hash(request):
            raise self._conflict()
        if job.status != "succeeded" or not isinstance(job.result, BackgroundResult):
            raise self._not_ready()
        result = job.result
        if result.product_rgba != request.product_rgba:
            raise self._invalid()
        canvas = (request.visual.generation_width, request.visual.generation_height)
        try:
            with self._read(result.scene_underlay, project_id) as scene, \
                    self._read(result.product_rgba, project_id) as product, \
                    self._read(result.composed_preview, project_id) as preview:
                if scene.size != canvas or preview.size != canvas or product.size != canvas:
                    raise self._invalid()
                if inspect_composite(preview, product).mismatched_pixels:
                    raise self._invalid()
        except (ValueError, TypeError, OSError) as exc:
            raise self._invalid() from exc

    def _cached_job(self, db: sqlite3.Connection, project_id: UUID,
                    request: BackgroundRequest) -> UUID | None:
        request_hash = content_hash(request)
        row = db.execute("SELECT job_id FROM image_cache WHERE project_id=? AND request_hash=?",
                         (str(project_id), request_hash)).fetchone()
        if row is None:
            return None
        try:
            job_id = UUID(row["job_id"])
        except (ValueError, TypeError) as exc:
            raise self._storage_error(retryable=False) from exc
        try:
            self._verify_result(job_id, project_id, request)
        except GatewayError as exc:
            if exc.code not in {"INVALID_IMAGE_REQUEST", "EDIT_CONFLICT", "IMAGE_JOB_NOT_READY"}:
                raise
            # A result that vanished or became invalid is a miss, not a success.
            # Storage failures are deliberately not swallowed by this branch.
            db.execute("DELETE FROM image_cache WHERE project_id=? AND request_hash=?",
                       (str(project_id), request_hash))
            return None
        return job_id

    def select(self, campaign_id: UUID, project_id: UUID, expected_revision: int,
               request: BackgroundRequest, copy_revision: int) -> ImageSelection:
        """Replace the current image inputs only if the caller saw its latest revision."""
        self._validate_ids(campaign_id, project_id)
        self._revision(expected_revision, minimum=0, maximum=2**63 - 2)
        self._revision(copy_revision, minimum=1)
        try:
            if not isinstance(request, BackgroundRequest):
                raise ValueError("A background request is required")
            snapshot = BackgroundRequest.model_validate(request.model_dump(mode="json"))
            request_hash = content_hash(snapshot)
        except (ValueError, TypeError, AttributeError) as exc:
            raise self._invalid() from exc
        try:
            with self._connection(write=True) as db:
                row = db.execute("SELECT * FROM image_selections WHERE project_id=? AND campaign_id=?",
                                 (str(project_id), str(campaign_id))).fetchone()
                current = self._decode(row) if row is not None else None
                if expected_revision != (current.revision if current else 0):
                    raise self._conflict()
                if current is not None and copy_revision < current.copy_revision:
                    raise self._conflict()
                self._verify_inputs(snapshot, project_id)
                cached = self._cached_job(db, project_id, snapshot)
                db.execute("""INSERT INTO image_selections
                    (project_id,campaign_id,revision,copy_revision,request_hash,request_json,active_job_id,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(project_id,campaign_id) DO UPDATE SET
                    revision=excluded.revision,copy_revision=excluded.copy_revision,
                    request_hash=excluded.request_hash,request_json=excluded.request_json,
                    active_job_id=excluded.active_job_id,updated_at=excluded.updated_at""", (
                    str(project_id), str(campaign_id), expected_revision + 1, copy_revision,
                    request_hash, snapshot.model_dump_json(), str(cached) if cached else None,
                    datetime.now(timezone.utc).isoformat(timespec="microseconds")))
                row = db.execute("SELECT * FROM image_selections WHERE project_id=? AND campaign_id=?",
                                 (str(project_id), str(campaign_id))).fetchone()
                selection = self._decode(row)
            return selection
        except sqlite3.Error as exc:
            raise self._storage_error() from exc

    def get(self, campaign_id: UUID, project_id: UUID) -> ImageSelection:
        """Read the saved selection; this is not an export or file-quality approval."""
        self._validate_ids(campaign_id, project_id)
        try:
            with self._connection() as db:
                row = db.execute("SELECT * FROM image_selections WHERE project_id=? AND campaign_id=?",
                                 (str(project_id), str(campaign_id))).fetchone()
            if row is None:
                raise self._not_found()
            return self._decode(row)
        except sqlite3.Error as exc:
            raise self._storage_error() from exc

    def attach(self, campaign_id: UUID, project_id: UUID, expected_revision: int,
               job_id: UUID) -> ImageSelection:
        """Attach a verified terminal result without letting an old job replace new edits."""
        self._validate_ids(campaign_id, project_id)
        self._revision(expected_revision, minimum=1)
        if not isinstance(job_id, UUID):
            raise self._invalid()
        try:
            with self._connection(write=True) as db:
                row = db.execute("SELECT * FROM image_selections WHERE project_id=? AND campaign_id=?",
                                 (str(project_id), str(campaign_id))).fetchone()
                if row is None:
                    raise self._not_found()
                current = self._decode(row)
                if current.revision != expected_revision:
                    raise self._conflict()
                self._verify_inputs(current.request, project_id)
                self._verify_result(job_id, project_id, current.request)
                db.execute("""UPDATE image_selections SET active_job_id=?,updated_at=?
                    WHERE project_id=? AND campaign_id=? AND revision=?""", (
                    str(job_id), datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    str(project_id), str(campaign_id), expected_revision))
                db.execute("""INSERT INTO image_cache(project_id,request_hash,job_id) VALUES(?,?,?)
                    ON CONFLICT(project_id,request_hash) DO UPDATE SET job_id=excluded.job_id""",
                    (str(project_id), current.request_hash, str(job_id)))
                row = db.execute("SELECT * FROM image_selections WHERE project_id=? AND campaign_id=?",
                                 (str(project_id), str(campaign_id))).fetchone()
                selection = self._decode(row)
            return selection
        except sqlite3.Error as exc:
            raise self._storage_error() from exc
