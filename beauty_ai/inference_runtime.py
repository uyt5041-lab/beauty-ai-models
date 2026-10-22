"""Image server with disabled-by-default OpenAI and explicit legacy profiles."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import UUID

from .assets import LocalAssetStore
from .inference_api import create_app
from .job_ledger import JobLedger
from .image_edits import ImageEditStore
from .local_processor import LocalTemplateProcessor
from .product_approval import ProductApprovalStore
from .errors import GatewayError
from .contracts import ExternalImageRequest
from .openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from .api_image_processor import ExternalImageProcessor
from .provider_attempts import ProviderAttemptStore


class _ProfileRouter:
    def __init__(self, local, overrides, external):
        self.local, self.overrides, self.external = local, overrides, external

    def _processor(self, request):
        if isinstance(request, ExternalImageRequest):
            return self.external
        return self.overrides.get(request.pipeline_profile_id, self.local)

    def validate_request(self, request):
        self._processor(request).validate_request(request)

    def __call__(self, request, project_id):
        return self._processor(request)(request, project_id)

    def process_job(self, request, project_id, job_id):
        if isinstance(request, ExternalImageRequest):
            return self.external.process_job(request, project_id, job_id)
        return self(request, project_id)


def build_app(root: str | Path, project_id: UUID, token: str, *,
              cutout_backend: str = "uploaded-alpha", model_root: str | Path = "models",
              model_device: str = "mps", background_backend: str = "bright-template",
              openai_config: OpenAIImagesConfig | None = None, openai_transport=None,
              image_budget_usd="0", image_reservation_usd="1"):
    root = Path(root)
    assets = LocalAssetStore(root / "assets")
    approvals = ProductApprovalStore(root / "products.sqlite3", assets)
    processor = LocalTemplateProcessor(assets, approvals)
    overrides = {}
    if cutout_backend == "birefnet-lite":
        from .birefnet_processor import BiRefNetCutoutProcessor
        from .model_store import model_directory, verify_model_directory
        directory = verify_model_directory(model_directory(model_root))
        overrides["birefnet-lite"] = BiRefNetCutoutProcessor(assets, directory, device=model_device)
    elif cutout_backend != "uploaded-alpha":
        raise ValueError("Unsupported cutout backend")
    if background_backend == "sdxl-inpaint":
        from .sdxl_processor import SDXLBackgroundProcessor
        from .sdxl_model_store import model_directory, verify_model_directory
        directory = verify_model_directory(model_directory(model_root))
        overrides["sdxl-inpaint"] = SDXLBackgroundProcessor(assets, approvals, directory, device=model_device)
    elif background_backend != "bright-template":
        raise ValueError("Unsupported background backend")
    attempts = ProviderAttemptStore(root / "provider_attempts.sqlite3")
    external = ExternalImageProcessor(
        assets, OpenAIImagesAdapter(openai_config or OpenAIImagesConfig(), transport=openai_transport),
        attempts, budget_limit_usd=image_budget_usd, reservation_usd=image_reservation_usd)
    processor = _ProfileRouter(processor, overrides, external)
    ledger = JobLedger(root / "jobs.sqlite3")
    edits = ImageEditStore(root / "edits.sqlite3", assets, ledger)
    return create_app(ledger, assets, {token: project_id}, processor, approvals=approvals,
                      request_validator=processor.validate_request, edits=edits, provider_attempts=attempts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    try:
        project_id = UUID(os.environ.get("BEAUTY_PROJECT_ID", ""))
        token = os.environ.get("INFERENCE_INTERNAL_TOKEN", "")
        if len(token) < 32:
            raise ValueError
    except ValueError:
        parser.error("Set BEAUTY_PROJECT_ID to a UUID and INFERENCE_INTERNAL_TOKEN to a random 32+ character token.")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    import uvicorn
    try:
        live_value = os.environ.get("BEAUTY_OPENAI_IMAGE_LIVE_CALLS", "false").lower()
        if live_value not in {"true", "false"}:
            raise ValueError
        config = OpenAIImagesConfig(live_calls=live_value == "true",
                                    api_key=os.environ.get("OPENAI_API_KEY", ""),
                                    timeout_seconds=float(os.environ.get("BEAUTY_OPENAI_IMAGE_TIMEOUT_SECONDS", "180")))
        app = build_app(os.environ.get("BEAUTY_STORAGE_ROOT", "outputs/inference"), project_id, token,
                        cutout_backend=os.environ.get("BEAUTY_CUTOUT_BACKEND", "uploaded-alpha"),
                        background_backend=os.environ.get("BEAUTY_BACKGROUND_BACKEND", "bright-template"),
                        model_root=os.environ.get("BEAUTY_MODEL_ROOT", "models"),
                        model_device=os.environ.get("BEAUTY_MODEL_DEVICE", "mps"),
                        openai_config=config,
                        image_budget_usd=os.environ.get("BEAUTY_IMAGE_BUDGET_USD", "0"),
                        image_reservation_usd=os.environ.get("BEAUTY_IMAGE_RESERVATION_USD", "1"))
    except (GatewayError, ValueError):
        parser.error("Image runtime configuration is invalid or the selected legacy backend is unavailable.")
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
