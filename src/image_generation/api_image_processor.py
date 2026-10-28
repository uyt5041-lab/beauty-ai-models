"""Project assets -> one durable OpenAI attempt -> verified single PNG asset."""
from __future__ import annotations

from io import BytesIO
from uuid import UUID
import warnings

from PIL import Image, UnidentifiedImageError

from .assets import LocalAssetStore
from .contracts import ExternalImageRequest, ExternalImageResult, content_hash, image_result_matches
from .errors import GatewayError
from .openai_images import OpenAIImagesAdapter, OpenAIImagesError
from .provider_attempts import ProviderAttemptStore, money_units


class ExternalImageProcessor:
    def __init__(self, assets: LocalAssetStore, adapter: OpenAIImagesAdapter,
                 attempts: ProviderAttemptStore, *, budget_limit_usd="0", reservation_usd="1"):
        money_units(budget_limit_usd)
        money_units(reservation_usd, positive=True)
        self.assets, self.adapter, self.attempts = assets, adapter, attempts
        self.budget_limit_usd, self.reservation_usd = budget_limit_usd, reservation_usd

    def validate_request(self, request: ExternalImageRequest) -> None:
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
                    return result
            raise GatewayError("PROVIDER_ATTEMPT_CONFLICT", "This job already has an attempt; review its receipt.")
        product_images = [self.assets.read_bytes(ref, project_id=project_id) for ref in request.product_images]
        reference_images = [self.assets.read_bytes(ref, project_id=project_id) for ref in request.reference_images]
        try:
            self.adapter.validate_input(request, product_images, reference_images)
        except OpenAIImagesError as error:
            raise GatewayError(error.code, "OpenAI image input is unavailable or invalid.") from None
        # Commit before crossing the network boundary. A crash leaves a held,
        # uncertain attempt; restarting never turns it into another POST.
        attempt_id = self.attempts.begin(job_id, project_id, snapshot_hash,
                                        budget_limit_usd=self.budget_limit_usd,
                                        reservation_usd=self.reservation_usd)
        response = None
        try:
            response = self.adapter.execute(request, product_images, reference_images,
                                            client_request_id=attempt_id)
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
                    "PROVIDER_OUTPUT_INVALID", "PROVIDER_RESULT_STORAGE_FAILED"}:
                code = error.code
            else:
                code = "PROVIDER_SUBMISSION_UNKNOWN"
            request_id = response.provider_request_id if response else getattr(error, "provider_request_id", None)
            self.attempts.finish(job_id, project_id, attempt_id,
                                 state="unknown" if code == "PROVIDER_SUBMISSION_UNKNOWN" else "failed",
                                 provider_request_id=request_id, usage=response.usage if response else None,
                                 error_code=code)
            raise GatewayError(code, "Provider attempt requires review; automatic resubmission is disabled.",
                               provider_request_id=request_id) from None
        # Failure here keeps a submitting reservation. The result asset can be
        # orphaned, but no incomplete result is published and no POST is repeated.
        self.attempts.finish(job_id, project_id, attempt_id, state="succeeded",
                             provider_request_id=response.provider_request_id, usage=response.usage, result=result)
        return result
