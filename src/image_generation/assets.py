"""Project-scoped, durable local image assets for a single shared filesystem.

The service authenticates the caller before supplying ``project_id``. This store
enforces project membership but does not authenticate an end user. Stored refs
describe the normalized PNG, not the original upload's bytes or dimensions.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator
from uuid import UUID, uuid4
import warnings

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from .contracts import AssetRef
from .errors import GatewayError


class LocalAssetStore:
    """SQLite metadata plus immutable PNGs; publication commits after file sync.

    A crash before the metadata commit can leave an unreferenced file. Such a
    file is never readable by asset ID and must not be inferred to be published.
    The root is a trusted service-owned directory on a local filesystem, not a
    user-upload directory or a shared, untrusted network filesystem.
    """

    def __init__(self, root: str | Path, *, max_upload_bytes: int = 20 * 1024 * 1024,
                 max_pixels: int = 16_000_000):
        if max_upload_bytes <= 0 or max_pixels <= 0:
            raise ValueError("Image limits must be positive")
        self.root = Path(root).resolve()
        self.max_upload_bytes = max_upload_bytes
        self.max_pixels = max_pixels
        self.files_dir = self.root / "files"
        self.database = self.root / "assets.sqlite3"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.files_dir.mkdir(exist_ok=True)
            if self.files_dir.is_symlink() or self.database.is_symlink():
                raise OSError("Asset storage must not be symlinked")
            with self._connection() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    mime_type TEXT NOT NULL
                )""")
                db.execute("CREATE INDEX IF NOT EXISTS assets_project ON assets(project_id)")
                db.commit()
        except (OSError, sqlite3.Error) as exc:
            raise self._storage_error() from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _storage_error() -> GatewayError:
        return GatewayError("ASSET_STORAGE_ERROR", "Asset storage is unavailable.", retryable=True)

    @staticmethod
    def _integrity_error() -> GatewayError:
        return GatewayError("ASSET_INTEGRITY_ERROR", "Stored asset does not match its reference.")

    @staticmethod
    def _uuid(value: UUID) -> UUID:
        if not isinstance(value, UUID):
            raise ValueError("Asset and project identifiers must be UUID values")
        return value

    def _check_size(self, size: tuple[int, int]) -> None:
        width, height = size
        if (width < 1 or height < 1 or width > 16384 or height > 16384
                or width * height > self.max_pixels):
            raise GatewayError("IMAGE_TOO_LARGE", "Image dimensions exceed the configured limit.")

    def _normalize(self, image: Image.Image) -> Image.Image:
        self._check_size(image.size)
        if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
            raise GatewayError("INVALID_IMAGE", "Animated images are not supported.")
        image.load()
        upright = ImageOps.exif_transpose(image)
        has_alpha = "A" in upright.getbands() or "transparency" in upright.info
        target_mode = "RGBA" if has_alpha else "RGB"
        converted = upright.convert(target_mode)
        profile = upright.info.get("icc_profile")
        if profile:
            try:
                source = ImageCms.ImageCmsProfile(BytesIO(profile))
                # Alpha is coverage, not color; transform only RGB/CMYK channels.
                color_input = upright if upright.mode in ("RGB", "CMYK", "LAB") else converted.convert("RGB")
                rgb = ImageCms.profileToProfile(
                    color_input, source, ImageCms.createProfile("sRGB"), outputMode="RGB")
                if has_alpha:
                    rgb.putalpha(converted.getchannel("A"))
                converted = rgb
            except (ImageCms.PyCMSError, OSError, ValueError, TypeError) as exc:
                raise GatewayError("INVALID_IMAGE", "Image color profile is invalid or unsupported.") from exc
        # New pixel-only image drops EXIF, comments, ICC and text metadata.
        clean = Image.frombytes(converted.mode, converted.size, converted.tobytes())
        self._check_size(clean.size)
        return clean

    def put_upload(self, data: bytes, *, project_id: UUID) -> AssetRef:
        self._uuid(project_id)
        if not isinstance(data, bytes) or not data:
            raise GatewayError("INVALID_IMAGE", "An image byte payload is required.")
        if len(data) > self.max_upload_bytes:
            raise GatewayError("IMAGE_TOO_LARGE", "Image upload exceeds the configured byte limit.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", UserWarning)
                with Image.open(BytesIO(data)) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP"}:
                        raise GatewayError("INVALID_IMAGE", "Only PNG, JPEG and WebP images are supported.")
                    clean = self._normalize(image)
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise GatewayError("IMAGE_TOO_LARGE", "Image dimensions exceed the decoder safety limit.") from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, UserWarning) as exc:
            raise GatewayError("INVALID_IMAGE", "Image could not be decoded.") from exc
        return self._publish(clean, project_id)

    def put_image(self, image: Image.Image, *, project_id: UUID) -> AssetRef:
        self._uuid(project_id)
        if not isinstance(image, Image.Image):
            raise GatewayError("INVALID_IMAGE", "A decoded image is required.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", UserWarning)
                clean = self._normalize(image)
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise GatewayError("IMAGE_TOO_LARGE", "Image dimensions exceed the decoder safety limit.") from exc
        except (OSError, ValueError, SyntaxError, UserWarning) as exc:
            raise GatewayError("INVALID_IMAGE", "Image could not be normalized.") from exc
        return self._publish(clean, project_id)

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _file_path(self, project_id: UUID, asset_id: UUID) -> Path:
        return self.files_dir / project_id.hex / f"{asset_id.hex}.png"

    def _publish(self, image: Image.Image, project_id: UUID) -> AssetRef:
        output = BytesIO()
        image.save(output, format="PNG")
        data = output.getvalue()
        asset = AssetRef(asset_id=uuid4(), sha256=hashlib.sha256(data).hexdigest(),
                         width=image.width, height=image.height, mime_type="image/png")
        destination = self._file_path(project_id, asset.asset_id)
        temporary: Path | None = None
        try:
            destination.parent.mkdir(exist_ok=True)
            if self.files_dir.is_symlink() or destination.parent.is_symlink():
                raise OSError("Asset storage must not be symlinked")
            # Persist the new project directory before publishing a file in it.
            self._sync_directory(self.files_dir)
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".asset-", delete=False) as file:
                temporary = Path(file.name)
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, destination)
            temporary = None
            self._sync_directory(destination.parent)
            with self._connection() as db:
                db.execute("INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?)", (
                    str(asset.asset_id), str(project_id), asset.sha256,
                    asset.width, asset.height, asset.mime_type))
                db.commit()
            return asset
        except (OSError, sqlite3.Error) as exc:
            # An ambiguous metadata commit may have succeeded: preserve the file
            # rather than deleting an asset now referenced by a committed row.
            raise self._storage_error() from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # Preserve the original stable storage error; this orphan is
                    # unpublished and cannot be fetched through the metadata DB.
                    pass

    def get_ref(self, asset_id: UUID, *, project_id: UUID) -> AssetRef:
        self._uuid(asset_id)
        self._uuid(project_id)
        try:
            with self._connection() as db:
                row = db.execute("SELECT * FROM assets WHERE asset_id=? AND project_id=?",
                                 (str(asset_id), str(project_id))).fetchone()
        except sqlite3.Error as exc:
            raise self._storage_error() from exc
        if row is None:
            raise GatewayError("ASSET_NOT_FOUND", "Asset was not found in this project.")
        try:
            return AssetRef(**{key: row[key] for key in (
                "asset_id", "sha256", "width", "height", "mime_type")})
        except ValidationError as exc:
            raise self._integrity_error() from exc

    def read(self, ref: AssetRef, *, project_id: UUID) -> Image.Image:
        data = self.read_bytes(ref, project_id=project_id)
        with Image.open(BytesIO(data)) as image:
            image.load()
            return image.copy()

    def read_bytes(self, ref: AssetRef, *, project_id: UUID) -> bytes:
        """Return the exact verified PNG bytes, preserving the reference hash."""
        self._uuid(project_id)
        if not isinstance(ref, AssetRef):
            raise ValueError("Expected an AssetRef")
        stored = self.get_ref(ref.asset_id, project_id=project_id)
        if stored != ref:
            raise self._integrity_error()
        path = self._file_path(project_id, ref.asset_id)
        try:
            if self.files_dir.is_symlink() or path.parent.is_symlink() or path.is_symlink():
                raise self._integrity_error()
            # Ref-derived paths only; do not follow a replaced asset symlink.
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as file:
                # PNG data produced by this store is bounded by pixel count.
                max_stored_bytes = ref.width * ref.height * 5 + 1024 * 1024
                if os.fstat(file.fileno()).st_size > max_stored_bytes:
                    raise self._integrity_error()
                data = file.read(max_stored_bytes + 1)
            if hashlib.sha256(data).hexdigest() != ref.sha256:
                raise self._integrity_error()
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    if (image.format != "PNG" or image.size != (ref.width, ref.height)
                            or image.mode not in {"RGB", "RGBA"}
                            or getattr(image, "n_frames", 1) != 1):
                        raise self._integrity_error()
                    image.load()
                    return data
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombWarning,
                Image.DecompressionBombError) as exc:
            raise self._integrity_error() from exc
