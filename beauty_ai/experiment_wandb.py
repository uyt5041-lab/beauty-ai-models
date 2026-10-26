"""Save a finished trial to W&B without ever importing the image executor.

The execution report is immutable. Sync status lives beside it, separately.
Only recorded LIVE runs may be published; mock and preview runs stay local.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from .experiment_runner import _absolute_without_links, _read_json, write_json
from .experiment_trial_records import collect_trial
from .wandb_experiments import PROJECT_NAME, prepare_package, publish as publish_packages, finops_summary


def validate_destination(entity: str, project: str) -> None:
    if any(type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
           for value in (entity, project)):
        raise ValueError("WANDB_DESTINATION_INVALID")


def sync_trial(plan_path: Path, *, expected_sha256: str, entity: str,
               project: str = PROJECT_NAME, publish: bool = False,
               sdk: Any = None, api: Any = None) -> dict:
    """Stage or publish this exact saved trial. Retry sync, never generation.

    All errors use fixed codes. A failed upload never overwrites report.json.
    W&B SDK authentication is used only after local evidence passes validation.
    """
    validate_destination(entity, project)
    if type(publish) is not bool:
        raise ValueError("WANDB_PUBLISH_FLAG_INVALID")
    if type(expected_sha256) is not str or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None:
        raise ValueError("EXPERIMENT_EXPECTED_PLAN_HASH_REQUIRED")
    path = _absolute_without_links(plan_path)
    if path.name != "plan.json" or not any(p.name == "outputs" for p in path.parent.parents):
        raise ValueError("EXPERIMENT_UNSAFE_PATH")
    root = path.parent
    output = _absolute_without_links(root / "wandb")
    if output.exists() and (not output.is_dir() or any(p.is_symlink() for p in output.rglob("*"))):
        raise ValueError("EXPERIMENT_UNSAFE_PATH")
    receipt_path = _absolute_without_links(root / "wandb-sync.json")
    receipt = {"schema_version": "beautyai-experiment-wandb-sync-v1", "plan_sha256": expected_sha256,
        "entity": entity, "project": project, "status": "PREPARING", "record_count": 0,
        "readback_verified": False, "new_image_model_calls": 0,
        "started_at": datetime.now(timezone.utc).isoformat()}
    # Do not erase verified evidence on a later failed retry.
    previous = _read_json(receipt_path) if receipt_path.exists() else None
    if previous and previous.get("status") == "READBACK_VERIFIED":
        receipt["previous_verified_upload"] = {key: previous.get(key) for key in
            ("overview_url", "plan_sha256", "entity", "project", "finished_at")}
    try:
        records = collect_trial(path, expected_sha256=expected_sha256)
        if not records:
            receipt["status"] = "SKIPPED_NO_LIVE_RECORDS"
        else:
            project_root = next(parent.parent for parent in root.parents if parent.name == "outputs")
            packages = [prepare_package(record, project_root, output) for record in records]
            receipt.update(status="PREPARED", record_count=len(packages), finops=finops_summary(packages))
            write_json(receipt_path, receipt)
            if publish:
                source = Path(__file__).resolve().parents[1]
                plans = [source / rel for rel in ("docs/imageEvaluationPlan.md",
                    "examples/evaluation/image-eval-v0.1.json",
                    "examples/evaluation/real-product-manifest.template.json")]
                result = publish_packages(packages, plans, output, entity=entity, project=project, sdk=sdk, api=api)
                if result.get("status") != "READBACK_VERIFIED":
                    raise ValueError("WANDB_READBACK_UNCONFIRMED")
                receipt.update(status="READBACK_VERIFIED", readback_verified=True,
                    overview_url=result["overview_url"], records=result["records"])
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) else "WANDB_SYNC_FAILED"
        if re.fullmatch(r"(?:WANDB|EXPERIMENT)_[A-Z_]{1,100}", code) is None:
            code = "WANDB_SYNC_FAILED"
        receipt.update(status="INCOMPLETE", error_code=code)
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(receipt_path, receipt)
    return receipt


def finish_recording(plan_path: Path, *, expected_sha256: str, entity: str,
                     project: str, live: bool) -> dict:
    """Called once after durable execution completion; mock never contacts W&B."""
    if not live:
        receipt = {"status": "SKIPPED_MOCK", "readback_verified": False, "new_image_model_calls": 0}
        write_json(plan_path.parent / "wandb-sync.json", receipt)
        return receipt
    try:
        return sync_trial(plan_path, expected_sha256=expected_sha256, entity=entity, project=project, publish=True)
    except Exception:
        receipt = {"status": "INCOMPLETE", "error_code": "WANDB_SYNC_FAILED",
                   "readback_verified": False, "new_image_model_calls": 0}
        write_json(plan_path.parent / "wandb-sync.json", receipt)
        return receipt
