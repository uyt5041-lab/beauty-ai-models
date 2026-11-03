"""Install bounded, project-authenticated advertisement work endpoints."""
from __future__ import annotations

import re

from fastapi import Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .errors import GatewayError
from .work_contracts import (MAX_JSON_BYTES, normalize_attempt_spec, normalize_completion,
                             normalize_create_spec, normalize_receipt, normalize_uuid,
                             parse_work_json)
from .work_metrics import summarize_work_dataset


_WORK_CODE = re.compile(r"WORK_[A-Z_]+\Z")


def work_error_status(code: str) -> int:
    """Map only safe work codes; capture failure takes precedence over PENDING."""
    if code == "WORK_NOT_FOUND":
        return 404
    if code == "WORK_INPUT_TOO_LARGE":
        return 413
    if "STORAGE" in code or code in {"WORK_CAPTURE_PENDING", "WORK_SERVICE_UNAVAILABLE"}:
        return 503
    if code.endswith("CONFLICT") or code in {
            "WORK_CLOSED", "WORK_PENDING", "WORK_ALREADY_RECORDED", "WORK_MISSING_STAGE",
            "WORK_COMPLETION_INVALID"}:
        return 409
    return 400


def _http_error(code) -> HTTPException:
    if type(code) is not str or not _WORK_CODE.fullmatch(code):
        code = "WORK_INVALID"
    return HTTPException(work_error_status(code), detail={"code": code})


def _normalize(function, value):
    try:
        return function(value)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise _http_error(str(exc)) from None


async def _call(function, *args, **kwargs):
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except GatewayError as exc:
        if type(exc.code) is str and _WORK_CODE.fullmatch(exc.code):
            raise _http_error(exc.code) from None
        raise
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise _http_error(str(exc)) from None


def install_work_api(app, work_ledger, project_dependency, read_limited) -> None:
    """Reuse the host's bearer dependency and bounded request stream reader."""
    def store():
        if work_ledger is None:
            raise _http_error("WORK_SERVICE_UNAVAILABLE")
        return work_ledger

    async def body(request):
        try:
            raw = await read_limited(request, MAX_JSON_BYTES)
        except HTTPException as exc:
            if exc.status_code == 413:
                raise _http_error("WORK_INPUT_TOO_LARGE") from None
            raise
        return _normalize(parse_work_json, raw)

    @app.put("/internal/v1/ad-works/{work_id}")
    async def create(work_id: str, request: Request, project_id=Depends(project_dependency)):
        ledger = store()
        work_id = _normalize(normalize_uuid, work_id)
        spec = _normalize(normalize_create_spec, await body(request))
        return await _call(ledger.create_work, project_id, work_id, **spec)

    @app.get("/internal/v1/ad-works")
    async def export(project_id=Depends(project_dependency)):
        return await _call(store().export_dataset, project_id)

    @app.get("/internal/v1/ad-works/{work_id}")
    async def get(work_id: str, project_id=Depends(project_dependency)):
        ledger = store()
        work_id = _normalize(normalize_uuid, work_id)
        dataset = await _call(ledger.export_dataset, project_id)
        work = next((item for item in dataset["works"] if item["work_id"] == work_id), None)
        if work is None:
            raise _http_error("WORK_NOT_FOUND")
        metrics = await _call(summarize_work_dataset, dataset)
        row = next(item for item in metrics["works"] if item["work_id"] == work_id)
        return {"work": work, "metrics": row}

    @app.post("/internal/v1/ad-works/{work_id}/attempts")
    async def start(work_id: str, request: Request, project_id=Depends(project_dependency)):
        ledger = store()
        work_id = _normalize(normalize_uuid, work_id)
        spec = _normalize(normalize_attempt_spec, await body(request))
        await _call(ledger.start_attempt, project_id, work_id, **spec)
        return {"attempt_id": spec["attempt_id"], "status": "REGISTERED"}

    @app.post("/internal/v1/ad-works/{work_id}/receipts")
    async def record(work_id: str, request: Request, project_id=Depends(project_dependency)):
        ledger = store()
        work_id = _normalize(normalize_uuid, work_id)
        receipt = _normalize(normalize_receipt, await body(request))
        await _call(ledger.record_receipt, project_id, work_id, receipt)
        return {"attempt_id": receipt["attempt_id"], "status": "RECORDED"}

    @app.post("/internal/v1/ad-works/{work_id}/complete")
    async def complete(work_id: str, request: Request, project_id=Depends(project_dependency)):
        ledger = store()
        work_id = _normalize(normalize_uuid, work_id)
        completion = _normalize(normalize_completion, await body(request))
        await _call(ledger.complete, project_id, work_id, completion)
        return await _call(ledger.get_work, project_id, work_id)

    @app.post("/internal/v1/ad-works/{work_id}/cancel")
    async def cancel(work_id: str, request: Request, project_id=Depends(project_dependency)):
        ledger = store()
        work_id = _normalize(normalize_uuid, work_id)
        if await body(request) != {}:
            raise _http_error("WORK_SCHEMA_INVALID")
        await _call(ledger.cancel, project_id, work_id)
        return await _call(ledger.get_work, project_id, work_id)
