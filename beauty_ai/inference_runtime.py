"""Local alpha/template reference server; does not load a segmentation or GPU model."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import UUID

from .assets import LocalAssetStore
from .inference_api import create_app
from .job_ledger import JobLedger
from .local_processor import LocalTemplateProcessor
from .product_approval import ProductApprovalStore


def build_app(root: str | Path, project_id: UUID, token: str):
    root = Path(root)
    assets = LocalAssetStore(root / "assets")
    approvals = ProductApprovalStore(root / "products.sqlite3", assets)
    processor = LocalTemplateProcessor(assets, approvals)
    return create_app(JobLedger(root / "jobs.sqlite3"), assets, {token: project_id},
                      processor, approvals=approvals, request_validator=processor.validate_request)


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
    app = build_app(os.environ.get("BEAUTY_STORAGE_ROOT", "outputs/inference"), project_id, token)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
