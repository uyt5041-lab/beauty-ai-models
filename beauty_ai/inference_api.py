"""Authenticated internal API. User login and cloud deployment are host concerns."""
from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
from typing import Annotated, Callable, Mapping
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import Field, TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool

from .assets import LocalAssetStore
from .contracts import BackgroundRequest, CutoutRequest, ImageJob, ImageRequest, ImageResult
from .errors import GatewayError
from .image_worker import SingleHostWorker
from .job_ledger import JobLedger


_REQUEST = TypeAdapter(Annotated[CutoutRequest | BackgroundRequest, Field(discriminator="kind")])
_MAX_JOB_BYTES = 64 * 1024


async def _read_limited(request: Request, limit: int) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise HTTPException(413, detail={"code": "REQUEST_TOO_LARGE"})
        data.extend(chunk)
    return bytes(data)


def create_app(ledger: JobLedger, assets: LocalAssetStore,
               credentials: Mapping[str, UUID],
               processor: Callable[[ImageRequest, UUID], ImageResult] | None = None) -> FastAPI:
    """Bind a server-owned bearer credential to one project, never to user input.

    With no processor, requests stay durably queued. A compatible separate
    single-host worker may drain them later. One ledger corresponds to one local
    execution resource; do not use separate ledgers to share the same GPU.
    """
    if not credentials or any(not isinstance(k, str) or len(k) < 32 or not isinstance(v, UUID)
                              for k, v in credentials.items()):
        raise ValueError("Provide server-owned tokens of at least 32 characters mapped to UUIDs")
    tokens = [(key.encode("utf-8"), value) for key, value in credentials.items()]
    worker = SingleHostWorker(ledger, assets, processor) if processor is not None else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if worker is not None:
            # An API replica can serve while another process owns the worker.
            await run_in_threadpool(worker.start)
        try:
            yield
        finally:
            if worker is not None:
                # A disconnected HTTP client or shutdown timeout is not GPU cancellation.
                await run_in_threadpool(worker.stop)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.worker = worker

    async def project(authorization: Annotated[str | None, Header()] = None) -> UUID:
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        found = None
        supplied = token.encode("utf-8")
        for expected, project_id in tokens:
            if hmac.compare_digest(supplied, expected):
                found = project_id
        if found is None:
            raise HTTPException(401, detail={"code": "UNAUTHORIZED"},
                                headers={"WWW-Authenticate": "Bearer"})
        return found

    @app.exception_handler(GatewayError)
    async def gateway_error(request: Request, exc: GatewayError):
        statuses = {
            "ASSET_NOT_FOUND": 404, "IMAGE_JOB_NOT_FOUND": 404,
            "IDEMPOTENCY_CONFLICT": 409, "JOB_STATE_CONFLICT": 409,
            "INVALID_IMAGE": 400, "IMAGE_TOO_LARGE": 413,
            "ASSET_INTEGRITY_ERROR": 400, "ASSET_STORAGE_ERROR": 503,
            "JOB_STORAGE_ERROR": 503,
        }
        code = exc.code if exc.code in statuses else "INTERNAL_ERROR"
        return JSONResponse(status_code=statuses.get(code, 500), content={"detail": {"code": code}})

    @app.exception_handler(RequestValidationError)
    async def invalid_parameters(request: Request, exc: RequestValidationError):
        # Pydantic error input/context may include private prompt or credential data.
        errors = [{"type": e["type"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.post("/internal/v1/assets", status_code=201)
    async def upload(request: Request, project_id: UUID = Depends(project)):
        data = await _read_limited(request, assets.max_upload_bytes)
        return await run_in_threadpool(assets.put_upload, data, project_id=project_id)

    @app.get("/internal/v1/assets/{asset_id}")
    async def download(asset_id: UUID, project_id: UUID = Depends(project)):
        ref = await run_in_threadpool(assets.get_ref, asset_id, project_id=project_id)
        data = await run_in_threadpool(assets.read_bytes, ref, project_id=project_id)
        return Response(data, media_type="image/png", headers={
            "ETag": '"' + ref.sha256 + '"', "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        })

    @app.put("/internal/v1/jobs/{job_id}", status_code=202, response_model=ImageJob)
    async def submit(job_id: UUID, request: Request, project_id: UUID = Depends(project)):
        data = await _read_limited(request, _MAX_JOB_BYTES)
        try:
            payload = _REQUEST.validate_json(data)
        except ValidationError as exc:
            raise HTTPException(422, detail=[
                {"type": e["type"]} for e in exc.errors()
            ]) from None

        # Replays read the committed record before revalidating mutable files. A
        # lost acknowledgement still resolves to the original job/hash even if
        # a file later becomes unavailable. New submissions validate actual files.
        try:
            await run_in_threadpool(ledger.get, job_id, project_id)
        except GatewayError as exc:
            if exc.code != "IMAGE_JOB_NOT_FOUND":
                raise
        else:
            return await run_in_threadpool(ledger.submit, job_id, project_id, payload)

        refs = [payload.source]
        if isinstance(payload, BackgroundRequest):
            refs.extend([payload.product_rgba, payload.preserve_mask])
        for ref in refs:
            await run_in_threadpool(assets.read, ref, project_id=project_id)
        return await run_in_threadpool(ledger.submit, job_id, project_id, payload)

    @app.get("/internal/v1/jobs/{job_id}", response_model=ImageJob)
    async def get(job_id: UUID, project_id: UUID = Depends(project)):
        return await run_in_threadpool(ledger.get, job_id, project_id)

    @app.get("/internal/v1/health")
    async def health(project_id: UUID = Depends(project)):
        fatal = worker.fatal_error_code if worker is not None else None
        return JSONResponse(status_code=503 if fatal else 200, content={
            "local_worker_running": worker.is_running if worker is not None else False,
            "worker_configured": worker is not None,
            "error_code": fatal,
            "model_readiness": "not_assessed",
        })

    return app
