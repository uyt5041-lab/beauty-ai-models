"""Project assets -> one durable OpenAI attempt -> verified single PNG asset."""
from __future__ import annotations

from io import BytesIO
import time
from uuid import UUID
import warnings

from PIL import Image, UnidentifiedImageError

from .account_budget import AccountBudgetStore
from .assets import LocalAssetStore
from .contracts import ExternalImageRequest, ExternalImageResult, content_hash, image_result_matches
from .errors import GatewayError
from .openai_images import OpenAIImagesAdapter, OpenAIImagesError
from .provider_attempts import ProviderAttemptStore, money_units
from .work_ledger import AdWorkLedger


class ExternalImageProcessor:
    def __init__(self, assets: LocalAssetStore, adapter: OpenAIImagesAdapter,
                 attempts: ProviderAttemptStore, *, budget_limit_usd="0", reservation_usd="1",
                 account_budget: AccountBudgetStore | None = None,
                 work_ledger: AdWorkLedger | None = None):
        money_units(budget_limit_usd)
        money_units(reservation_usd, positive=True)
        self.assets, self.adapter, self.attempts = assets, adapter, attempts
        self.budget_limit_usd, self.reservation_usd = budget_limit_usd, reservation_usd
        if account_budget is not None and not isinstance(account_budget, AccountBudgetStore):
            raise GatewayError("ACCOUNT_BUDGET_CONFIG_INVALID", "Account budget configuration is invalid.")
        self.account_budget = account_budget
        self._validate_work_ledger(work_ledger)
        self.work_ledger = work_ledger

    def _validate_work_ledger(self, work_ledger: AdWorkLedger | None) -> None:
        if work_ledger is not None and not isinstance(work_ledger, AdWorkLedger):
            raise GatewayError("WORK_CONFIG_INVALID", "Advertisement work configuration is invalid.")
        if work_ledger is not None and (work_ledger.path.resolve() == self.attempts.path.resolve()
                or self.account_budget is not None and work_ledger.path.resolve() == self.account_budget.path.resolve()):
            raise GatewayError("WORK_LEDGER_CONFLICT", "Advertisement work requires a separate ledger.")

    def validate_request(self, request: ExternalImageRequest) -> None:
        if not self.adapter.offline and self.adapter.config.live_calls and self.account_budget is None:
            raise GatewayError("ACCOUNT_BUDGET_REQUIRED", "A shared account budget is required for live image calls.")
        try:
            self.adapter.validate_request(request)
        except OpenAIImagesError as error:
            raise GatewayError(error.code, "OpenAI image request is unavailable or invalid.") from None

    @staticmethod
    def _validate_output(data: bytes, request: ExternalImageRequest) -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", UserWarning)
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    if (image.format != "PNG" or image.size != (request.width, request.height)
                            or getattr(image, "n_frames", 1) != 1):
                        raise ValueError
                    image.load()
                    if request.operation == "cutout":
                        if "A" not in image.getbands():
                            raise ValueError
                        with image.getchannel("A") as alpha:
                            low, high = alpha.getextrema()
                            if low == 255 or high == 0:
                                raise ValueError
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, UserWarning,
                Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise GatewayError("PROVIDER_OUTPUT_INVALID", "Provider image failed PNG, size, or alpha validation.") from None

    def process_job(self, request: ExternalImageRequest, project_id: UUID,
                    job_id: UUID) -> ExternalImageResult:
        from .work_tracking import resolve_image_work, sync_image_receipt

        scope = resolve_image_work(self.work_ledger, project_id, job_id)
        if scope is None:
            return self._process_job(request, project_id, job_id)
        self._validate_work_ledger(scope.ledger)
        try:
            work = scope.ledger.get_work(project_id, scope.work_id)
            if work["status"] != "OPEN":
                existing = self.attempts.get(job_id, project_id)
                reusable = False
                if (existing is not None and existing["state"] == "succeeded"
                        and existing["request_hash"] == content_hash(request)):
                    result = ExternalImageResult.model_validate(existing["result"])
                    reusable = image_result_matches(request, result)
                if not reusable:
                    raise GatewayError("WORK_CLOSED", "Advertisement work is closed.")
            return self._process_job(request, project_id, job_id)
        finally:
            try:
                record = self.attempts.get(job_id, project_id)
                if record is not None:
                    sync_image_receipt(scope.ledger, project_id, job_id, record, request)
            except Exception:
                raise GatewayError("WORK_CAPTURE_PENDING", "Provider receipt needs work reconciliation.",
                                   retryable=True) from None

    def _process_job(self, request: ExternalImageRequest, project_id: UUID,
                     job_id: UUID) -> ExternalImageResult:
        self.validate_request(request)
        # Copy validated fields before hashing or crossing the external boundary.
        request = ExternalImageRequest.model_validate(request.model_dump())
        snapshot_hash = content_hash(request)
        existing = self.attempts.get(job_id, project_id)
        if existing is not None:
            if existing["request_hash"] == snapshot_hash and existing["state"] == "succeeded":
                result = ExternalImageResult.model_validate(existing["result"])
                if image_result_matches(request, result):
                    self.assets.read_bytes(result.image, project_id=project_id)
                    self.attempts.record_reuse(job_id, project_id, snapshot_hash)
                    return result
            raise GatewayError("PROVIDER_ATTEMPT_CONFLICT", "This job already has an attempt; review its receipt.")
        product_images = [self.assets.read_bytes(ref, project_id=project_id) for ref in request.product_images]
        reference_images = [self.assets.read_bytes(ref, project_id=project_id) for ref in request.reference_images]
        try:
            self.adapter.validate_input(request, product_images, reference_images)
        except OpenAIImagesError as error:
            raise GatewayError(error.code, "OpenAI image input is unavailable or invalid.") from None
        # The shared reservation precedes the local receipt. Neither database
        # automatically releases its reservation if the next step fails.
        reservation_id = (self.account_budget.reserve(project_id, job_id, snapshot_hash, self.reservation_usd)
                          if self.account_budget is not None else None)
        metadata = {"operation": request.operation, "model": request.model, "quality": request.quality,
                    "width": request.width, "height": request.height,
                    "prompt_version": (str(request.managed_prompt.version) if request.managed_prompt
                                       else request.prompt.prompt_version if request.prompt else None),
                    "request_hash": snapshot_hash}
        attempt_id = self.attempts.begin(job_id, project_id, snapshot_hash,
                                        budget_limit_usd=self.budget_limit_usd,
                                        reservation_usd=self.reservation_usd, metadata=metadata,
                                        account_reservation_id=reservation_id)
        response = None
        execute_started = elapsed = None
        try:
            permit = (self.account_budget.bind_attempt(reservation_id, attempt_id)
                      if self.account_budget is not None else None)
            execute_started = time.monotonic()
            response = self.adapter.execute(request, product_images, reference_images,
                                            client_request_id=attempt_id, budget_permit=permit)
            elapsed = time.monotonic() - execute_started
            self._validate_output(response.image_bytes, request)
            try:
                asset = self.assets.put_upload(response.image_bytes, project_id=project_id)
            except Exception:
                raise GatewayError("PROVIDER_RESULT_STORAGE_FAILED", "Provider responded but result storage failed.") from None
            result = ExternalImageResult(
                operation=request.operation, image=asset, model=request.model, attempt_id=attempt_id,
                provider_request_id=response.provider_request_id, usage=response.usage,
                warnings=(["GENERATIVE_CUTOUT_REQUIRES_REVIEW"] if request.operation == "cutout"
                          else ["GENERATED_IMAGE_REQUIRES_REVIEW"]))
        except Exception as error:
            if isinstance(error, OpenAIImagesError):
                code = error.code
            elif isinstance(error, GatewayError) and error.code in {
                    "PROVIDER_OUTPUT_INVALID", "PROVIDER_RESULT_STORAGE_FAILED",
                    "ACCOUNT_BUDGET_REQUIRED", "ACCOUNT_BUDGET_CONFIG_INVALID",
                    "ACCOUNT_BUDGET_POLICY_CONFLICT", "ACCOUNT_BUDGET_EXCEEDED",
                    "ACCOUNT_BUDGET_RESERVATION_CONFLICT", "ACCOUNT_BUDGET_STORAGE_ERROR",
                    "ACCOUNT_BUDGET_PERIOD_INACTIVE"}:
                code = error.code
            else:
                code = "PROVIDER_SUBMISSION_UNKNOWN"
            safe_error = error if isinstance(error, OpenAIImagesError) else None
            request_id = response.provider_request_id if response else safe_error.provider_request_id if safe_error else None
            if elapsed is None and execute_started is not None:
                elapsed = time.monotonic() - execute_started
            self.attempts.finish(job_id, project_id, attempt_id,
                                 state="unknown" if code == "PROVIDER_SUBMISSION_UNKNOWN" else "failed",
                                 provider_request_id=request_id,
                                 usage=response.usage if response else safe_error.usage if safe_error else None,
                                 raw_usage=response.raw_usage if response else safe_error.raw_usage if safe_error else None,
                                 error_code=code, elapsed_seconds=elapsed)
            raise GatewayError(code, "Provider attempt requires review; automatic resubmission is disabled.",
                               provider_request_id=request_id) from None
        # Failure here keeps a submitting reservation. The result asset can be
        # orphaned, but no incomplete result is published and no POST is repeated.
        self.attempts.finish(job_id, project_id, attempt_id, state="succeeded",
                             provider_request_id=response.provider_request_id, usage=response.usage,
                             raw_usage=response.raw_usage, elapsed_seconds=elapsed, result=result)
        return result
