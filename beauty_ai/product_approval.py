"""Persist explicit approval of a precise product working layer and layout.

The authenticated host is responsible for collecting the user's confirmation.
This store records that confirmation; it does not authenticate a human identity
or certify image quality. Changing any source/layer/mask/visual input requires
approval of that new fingerprint.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import UUID, uuid4

from PIL import Image, ImageChops
from pydantic import Field, ValidationError

from .assets import LocalAssetStore
from .contracts import AssetRef, BackgroundRequest, Contract, VisualBrief, content_hash
from .errors import GatewayError
from .image_preparation import _pixel_bounds, prepare_product_canvas


class ProductCandidate(Contract):
    candidate_id: UUID
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: AssetRef
    product_rgba: AssetRef
    preserve_mask: AssetRef
    visual: VisualBrief
    approved: bool


class _ProductSnapshot(Contract):
    source: AssetRef
    product_rgba: AssetRef
    preserve_mask: AssetRef
    visual: VisualBrief


class ProductApprovalStore:
    def __init__(self, path: str | Path, assets: LocalAssetStore):
        self.path = Path(path).resolve()
        self.assets = assets
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""CREATE TABLE IF NOT EXISTS product_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    candidate_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)),
                    created_at TEXT NOT NULL,
                    approved_at TEXT
                )""")
                db.execute("""CREATE INDEX IF NOT EXISTS candidates_fingerprint
                    ON product_candidates(project_id, candidate_hash, approved)""")
        except (OSError, sqlite3.Error) as exc:
            raise self._storage_error() from exc

    @staticmethod
    def _storage_error(*, retryable: bool = True) -> GatewayError:
        return GatewayError("PRODUCT_STORAGE_ERROR",
                            "Product approval storage is unavailable or inconsistent.",
                            retryable=retryable)

    @staticmethod
    def _invalid_input() -> GatewayError:
        return GatewayError("INVALID_IMAGE_REQUEST", "Product image inputs are invalid or unavailable.")

    @staticmethod
    def _not_found() -> GatewayError:
        return GatewayError("PRODUCT_NOT_FOUND", "Product candidate was not found in this project.")

    @staticmethod
    def _approval_required() -> GatewayError:
        return GatewayError("PRODUCT_APPROVAL_REQUIRED", "This product layer and layout require explicit approval.")

    @staticmethod
    def _uuid(value: UUID) -> UUID:
        if not isinstance(value, UUID):
            raise ValueError("Project and candidate identifiers must be UUID values")
        return value

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

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
    def _decode(cls, row: sqlite3.Row) -> ProductCandidate:
        try:
            snapshot = _ProductSnapshot.model_validate_json(row["snapshot_json"])
            if content_hash(snapshot) != row["candidate_hash"]:
                raise ValueError("Stored product snapshot hash mismatch")
            if row["approved"] not in (0, 1):
                raise ValueError("Stored approval is invalid")
            if bool(row["approved"]) != (row["approved_at"] is not None):
                raise ValueError("Approval timestamp is inconsistent")
            UUID(row["project_id"])
            return ProductCandidate(candidate_id=row["candidate_id"],
                                    candidate_hash=row["candidate_hash"],
                                    approved=bool(row["approved"]), **snapshot.model_dump())
        except (ValueError, TypeError, ValidationError) as exc:
            raise cls._storage_error(retryable=False) from exc

    def _read(self, ref: AssetRef, project_id: UUID) -> Image.Image:
        try:
            return self.assets.read(ref, project_id=project_id)
        except GatewayError as exc:
            if exc.code == "ASSET_STORAGE_ERROR":
                raise self._storage_error() from exc
            raise self._invalid_input() from exc

    def _put(self, image: Image.Image, project_id: UUID) -> AssetRef:
        try:
            return self.assets.put_image(image, project_id=project_id)
        except GatewayError as exc:
            if exc.code == "ASSET_STORAGE_ERROR":
                raise self._storage_error() from exc
            raise self._invalid_input() from exc

    @staticmethod
    def _validate_layers(product: Image.Image, mask: Image.Image, visual: VisualBrief) -> None:
        """Require actual alpha bounds and an exact binary keep mask on this canvas."""
        size = (visual.generation_width, visual.generation_height)
        if product.mode != "RGBA" or product.size != size or mask.size != size:
            raise ValueError("Product, mask and layout must share an RGBA working canvas")
        alpha = product.getchannel("A")
        bounds = alpha.getbbox()
        if bounds is None:
            raise ValueError("Product alpha must not be empty")
        allowed = _pixel_bounds(visual.product_box, size, inside=True)
        if not (allowed[0] <= bounds[0] < bounds[2] <= allowed[2]
                and allowed[1] <= bounds[1] < bounds[3] <= allowed[3]):
            raise ValueError("Product alpha exceeds the product box")
        safe = _pixel_bounds(visual.text_safe_zone, size, inside=False)
        if alpha.crop(safe).getbbox() is not None:
            raise ValueError("Product alpha overlaps reserved text pixels")
        if mask.mode not in ("L", "RGB", "RGBA"):
            raise ValueError("Preserve mask must be a grayscale image")
        if mask.mode == "RGBA" and mask.getchannel("A").getextrema() != (255, 255):
            raise ValueError("Preserve mask must not contain transparency")
        if mask.mode in ("RGB", "RGBA"):
            red, green, blue = mask.convert("RGB").split()
            if (ImageChops.difference(red, green).getbbox() is not None
                    or ImageChops.difference(red, blue).getbbox() is not None):
                raise ValueError("Preserve mask must not contain colored pixels")
        gray = mask.convert("L")
        if sum(gray.histogram()[1:255]):
            raise ValueError("Preserve mask must be binary")
        expected = alpha.point(lambda value: 255 if value > 0 else 0)
        if ImageChops.difference(gray, expected).getbbox() is not None:
            raise ValueError("Preserve mask must exactly cover nonzero product alpha")

    def _verify_assets(self, candidate: ProductCandidate, project_id: UUID) -> None:
        try:
            with self._read(candidate.source, project_id), \
                    self._read(candidate.product_rgba, project_id) as product, \
                    self._read(candidate.preserve_mask, project_id) as mask:
                self._validate_layers(product, mask, candidate.visual)
        except (ValueError, TypeError, OSError) as exc:
            raise self._invalid_input() from exc

    def prepare(self, project_id: UUID, source: AssetRef, cutout: AssetRef,
                visual: VisualBrief) -> ProductCandidate:
        """Create an unapproved, resized candidate from a project's cutout RGBA."""
        self._uuid(project_id)
        try:
            source = AssetRef.model_validate(source.model_dump(mode="json"))
            cutout = AssetRef.model_validate(cutout.model_dump(mode="json"))
            visual = VisualBrief.model_validate(visual.model_dump(mode="json"))
            with self._read(source, project_id), self._read(cutout, project_id) as cutout_image:
                prepared = prepare_product_canvas(cutout_image, visual)
            try:
                self._validate_layers(prepared.product_rgba, prepared.preserve_mask, visual)
                product_ref = self._put(prepared.product_rgba, project_id)
                mask_ref = self._put(prepared.preserve_mask, project_id)
            finally:
                prepared.product_rgba.close()
                prepared.preserve_mask.close()
            snapshot = _ProductSnapshot(source=source, product_rgba=product_ref,
                                        preserve_mask=mask_ref, visual=visual)
        except (ValueError, TypeError, OSError, AttributeError) as exc:
            raise self._invalid_input() from exc
        candidate = ProductCandidate(candidate_id=uuid4(), candidate_hash=content_hash(snapshot),
                                     approved=False, **snapshot.model_dump())
        try:
            with self._connection(write=True) as db:
                db.execute("""INSERT INTO product_candidates
                    (candidate_id, project_id, candidate_hash, snapshot_json, approved, created_at)
                    VALUES (?, ?, ?, ?, 0, ?)""", (
                    str(candidate.candidate_id), str(project_id), candidate.candidate_hash,
                    snapshot.model_dump_json(), self._now()))
            return candidate
        except sqlite3.Error as exc:
            # Assets can remain unreferenced if candidate commit fails. No
            # approval row is fabricated, and approved files are never deleted.
            raise self._storage_error() from exc

    def get(self, candidate_id: UUID, project_id: UUID) -> ProductCandidate:
        self._uuid(candidate_id)
        self._uuid(project_id)
        try:
            with self._connection() as db:
                row = db.execute("SELECT * FROM product_candidates WHERE candidate_id=? AND project_id=?",
                                 (str(candidate_id), str(project_id))).fetchone()
            if row is None:
                raise self._not_found()
            return self._decode(row)
        except sqlite3.Error as exc:
            raise self._storage_error() from exc

    def approve(self, candidate_id: UUID, project_id: UUID, candidate_hash: str,
                *, confirmed: bool) -> ProductCandidate:
        self._uuid(candidate_id)
        self._uuid(project_id)
        try:
            with self._connection(write=True) as db:
                row = db.execute("SELECT * FROM product_candidates WHERE candidate_id=? AND project_id=?",
                                 (str(candidate_id), str(project_id))).fetchone()
                if row is None:
                    raise self._not_found()
                candidate = self._decode(row)
                if candidate_hash != candidate.candidate_hash:
                    raise GatewayError("PRODUCT_APPROVAL_CONFLICT", "Product approval fingerprint changed.")
                if confirmed is not True:
                    raise self._approval_required()
                self._verify_assets(candidate, project_id)
                if not candidate.approved:
                    db.execute("""UPDATE product_candidates SET approved=1, approved_at=?
                        WHERE candidate_id=? AND project_id=? AND approved=0""", (
                        self._now(), str(candidate_id), str(project_id)))
                    row = db.execute("SELECT * FROM product_candidates WHERE candidate_id=?",
                                     (str(candidate_id),)).fetchone()
                    candidate = self._decode(row)
            return candidate
        except sqlite3.Error as exc:
            raise self._storage_error() from exc

    def require_approved(self, request: BackgroundRequest, project_id: UUID) -> ProductCandidate:
        self._uuid(project_id)
        try:
            if not isinstance(request, BackgroundRequest):
                raise ValueError("A background request is required")
            request = BackgroundRequest.model_validate(request.model_dump(mode="json"))
            snapshot = _ProductSnapshot(source=request.source, product_rgba=request.product_rgba,
                                        preserve_mask=request.preserve_mask, visual=request.visual)
            candidate_hash = content_hash(snapshot)
        except (ValueError, TypeError, AttributeError) as exc:
            raise self._invalid_input() from exc
        try:
            with self._connection() as db:
                row = db.execute("""SELECT * FROM product_candidates
                    WHERE project_id=? AND candidate_hash=? AND approved=1 ORDER BY rowid DESC LIMIT 1""",
                    (str(project_id), candidate_hash)).fetchone()
            if row is None:
                raise self._approval_required()
            candidate = self._decode(row)
            self._verify_assets(candidate, project_id)
            return candidate
        except sqlite3.Error as exc:
            raise self._storage_error() from exc
