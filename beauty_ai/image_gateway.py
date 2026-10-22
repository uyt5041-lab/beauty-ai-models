from __future__ import annotations

import asyncio
import time
from uuid import UUID

import httpx
from pydantic import ValidationError

from .contracts import ImageJob, ImageRequest, content_hash
from .errors import GatewayError, JobStillPending, SubmissionUncertain


class HTTPImageGateway:
    """Client of OUR private inference protocol, not an existing third-party endpoint.

    Required server semantics: durable, idempotent PUT; same ID + changed payload => 409;
    acknowledge only AFTER commit; a single GPU executor; GET is safe to retry.
    The local runtime supplies the durable ledger; real GPU adapters are separate.
    """
    def __init__(self, client: httpx.AsyncClient):
        # Configure base_url and the internal authentication header at application startup.
        self.client = client

    async def submit(self, request: ImageRequest, *, job_id: UUID) -> ImageJob:
        try:
            response = await self.client.put(
                f"/internal/v1/jobs/{job_id}", json=request.model_dump(mode="json"),
                headers={"X-Request-ID": str(job_id)},
            )
        except httpx.TransportError as exc:
            raise SubmissionUncertain(job_id) from exc
        if response.status_code >= 500 or response.status_code in (408, 425, 429):
            # Submission may have committed even if a proxy failed to acknowledge it.
            raise SubmissionUncertain(job_id)
        if response.status_code == 409:
            try:
                body = response.json()
                detail = body.get("detail") if isinstance(body, dict) else None
                code = detail.get("code") if isinstance(detail, dict) else None
            except ValueError:
                code = None
            if code in ("PRODUCT_APPROVAL_REQUIRED", "PRODUCT_APPROVAL_CONFLICT"):
                raise GatewayError(code, "The exact product layer and layout need explicit approval.")
            raise GatewayError("IDEMPOTENCY_CONFLICT", "Job ID already belongs to different inputs.")
        if not response.is_success:
            raise GatewayError("IMAGE_SUBMISSION_REJECTED", f"Inference HTTP {response.status_code}")
        job = self._decode(response, expected_id=job_id)
        if job.request_hash != content_hash(request):
            raise GatewayError("IMAGE_PROTOCOL_ERROR", "Job response does not match submitted inputs.")
        if job.result is not None and job.result.kind != request.kind:
            raise GatewayError("IMAGE_PROTOCOL_ERROR", "Result kind does not match the request.")
        return job

    async def get(self, job_id: UUID) -> ImageJob:
        try:
            response = await self.client.get(f"/internal/v1/jobs/{job_id}")
        except httpx.TransportError as exc:
            raise GatewayError("IMAGE_STATUS_UNAVAILABLE", "Could not read image job status.",
                               retryable=True) from exc
        if response.status_code == 404:
            raise GatewayError("IMAGE_JOB_NOT_FOUND", "No committed record found for this job ID.")
        if not response.is_success:
            raise GatewayError("IMAGE_STATUS_UNAVAILABLE", f"Inference HTTP {response.status_code}",
                               retryable=response.status_code >= 500 or response.status_code == 429)
        return self._decode(response, expected_id=job_id)

    async def wait(self, job_id: UUID, *, expected_request: ImageRequest,
                   deadline_seconds: float = 120, poll_seconds: float = 1) -> ImageJob:
        if deadline_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("Deadline and polling interval must be positive")
        deadline = time.monotonic() + deadline_seconds
        expected_hash = content_hash(expected_request)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise JobStillPending(job_id)
            try:
                async with asyncio.timeout(remaining):
                    job = await self.get(job_id)
            except TimeoutError as exc:
                raise JobStillPending(job_id) from exc
            except GatewayError as exc:
                if not exc.retryable:
                    raise
                await asyncio.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
                continue
            if job.request_hash != expected_hash:
                raise GatewayError("IMAGE_PROTOCOL_ERROR", "Job input hash changed.")
            if job.status == "succeeded":
                if job.result is None or job.result.kind != expected_request.kind:
                    raise GatewayError("IMAGE_PROTOCOL_ERROR", "Unexpected image result kind.")
                return job
            if job.status in ("failed", "interrupted"):
                raise GatewayError(job.error_code or "IMAGE_JOB_FAILED", "Inference job did not succeed.")
            await asyncio.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))

    @staticmethod
    def _decode(response: httpx.Response, *, expected_id: UUID) -> ImageJob:
        try:
            job = ImageJob.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise GatewayError("IMAGE_PROTOCOL_ERROR", "Invalid inference response.") from exc
        if job.job_id != expected_id:
            raise GatewayError("IMAGE_PROTOCOL_ERROR", "Unexpected job ID.")
        return job
