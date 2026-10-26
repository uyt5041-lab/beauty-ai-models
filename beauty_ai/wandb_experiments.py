"""Explicit, retrospective W&B exports. This module never calls an image model.

Langfuse remains the prompt source. Import timestamps never replace execution
timestamps, and estimated cost never fills an unknown billed amount.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = "beautyai-experiment-export-v1"
PROJECT_NAME = "beautyai-image-experiments"
MAX_FILE_BYTES = 25 * 1024 * 1024
_SECRET = re.compile(r"(?:\bsk-[A-Za-z0-9_-]{20,}|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|-----BEGIN (?:RSA |EC )?PRIVATE KEY-----)")
_SECRET_KEY = re.compile(r"(?:api[_-]?key|authorization|password|secret|credential|access_token)", re.I)
FIELDS = (
    "record_id", "family", "experiment_group", "operation", "model", "status", "error_code",
    "prompt_text", "prompt_name", "prompt_version", "prompt_sha256", "request_hash",
    "source_report_sha256", "started_at", "elapsed_seconds", "timing_scope",
    "estimated_cost_usd", "actual_cost_usd", "billing_status", "usage", "settings",
    "langfuse_url", "quality_status", "review_notes", "data_kind", "cost_category",
    "data_provenance",
)
TABLE_COLUMNS = [
    "record_id", "family", "group", "operation", "status", "model", "original_input", "result",
    "prompt_name", "prompt_version", "prompt_text", "original_started_at", "elapsed_seconds",
    "timing_scope", "estimated_cost_usd", "estimate_origin", "actual_cost_usd", "billing_status", "cost_category",
    "quality_status", "data_kind", "review_notes", "langfuse_url", "run_url",
]

# This is a historical calculation basis, never a claim about today's prices.
# Saved B12 runner at 8ba5bde used these rates for this exact model.
HISTORICAL_RATE_BASIS = {
    "snapshot_date": "2026-10-23", "currency": "USD", "unit": "per_1000000_tokens",
    "model": "gpt-image-2.5-sunburst-2026-09-08",
    "input_text_tokens": 5, "input_image_tokens": 8, "output_tokens": 30,
    "evidence": "https://github.com/uyt5041-lab/beauty-ai-models/blob/8ba5bded8faace5b69a79394523290dfba3ed9b2/scripts/compare_cosmetics_presets.py",
    "provider_source_recorded_at_B12": "https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst",
}


def historical_estimate(record: dict) -> tuple[float | None, str, dict | None]:
    if record.get("estimated_cost_usd") is not None:
        return record["estimated_cost_usd"], "RECORDED_IN_SOURCE", None
    if (record.get("data_provenance") or {}).get("execution_schema") == "beautyai-experiment-execution-v1":
        return None, "NOT_CALCULATED_NO_RATE_BASIS", None
    usage = record.get("usage") or {}
    fields = ("input_text_tokens", "input_image_tokens", "output_tokens")
    if (record.get("family") == "api" and record.get("model") == HISTORICAL_RATE_BASIS["model"]
            and all(type(usage.get(k)) is int and usage[k] >= 0 for k in fields)):
        value = round(sum(usage[k] * HISTORICAL_RATE_BASIS[k] for k in fields) / 1_000_000, 8)
        return value, "RECONSTRUCTED_WITH_RECORDED_B12_RATES", dict(HISTORICAL_RATE_BASIS)
    return None, "UNKNOWN", None


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def checked_value(value: Any, root: Path) -> Any:
    """Reject secrets; strip machine-specific paths from selected metadata."""
    if isinstance(value, dict):
        if any(_SECRET_KEY.search(str(key)) for key in value):
            raise ValueError("EXPERIMENT_SECRET_FIELD")
        return {str(k): checked_value(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [checked_value(v, root) for v in value]
    if isinstance(value, str):
        if _SECRET.search(value):
            raise ValueError("EXPERIMENT_SECRET_VALUE")
        value = value.replace(str(root), "<project>")
        # A path in a model/runtime snapshot does not need a local user identity.
        return re.sub(r"/Users/[^/\s]+/", "<home>/", value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("EXPERIMENT_NUMBER_INVALID")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("EXPERIMENT_VALUE_INVALID")


def contained_file(root: Path, path: str | Path) -> tuple[Path, bytes]:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("EXPERIMENT_FILE_OUTSIDE_ROOT")
        if resolved.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("EXPERIMENT_FILE_TOO_LARGE")
        return resolved, resolved.read_bytes()
    except OSError:
        raise ValueError("EXPERIMENT_FILE_UNAVAILABLE") from None


def prepare_package(record: dict, project_root: Path, output_root: Path) -> dict:
    """Stage an allowlisted immutable package before any external write."""
    project_root = project_root.resolve(strict=True)
    record_id = record.get("record_id", "")
    if not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", record_id):
        raise ValueError("EXPERIMENT_ID_INVALID")
    payload = checked_value({k: record.get(k) for k in FIELDS}, project_root)
    if record.get("source_report"):
        report_path, report_bytes = contained_file(project_root, record["source_report"])
        if sha256(report_bytes) != record.get("source_report_sha256"):
            raise ValueError("EXPERIMENT_REPORT_HASH_MISMATCH")
        payload["source_report"] = report_path.relative_to(project_root).as_posix()
    payload["schema_version"] = SCHEMA_VERSION
    payload["import_kind"] = "retrospective_saved_evidence"
    payload["quality_score"] = None
    payload["evaluation_status"] = "NOT_SCORED"
    payload["cost_category"] = record.get("cost_category") or "research_experiment"
    payload["actual_cost_known"] = payload.get("actual_cost_usd") is not None
    payload["source_recorded_estimated_cost_usd"] = record.get("estimated_cost_usd")
    estimate, origin, basis = historical_estimate(record)
    payload.update(estimated_cost_usd=estimate, estimate_origin=origin, historical_rate_basis=basis)
    payload["images"] = []
    media: dict[str, bytes] = {}
    for role, paths in (("input", record.get("source_paths", [])), ("output", record.get("output_paths", []))):
        hashes = record.get("output_sha256s", []) if role == "output" else (record.get("data_provenance") or {}).get("source_sha256s", [])
        if role == "output" and len(hashes) != len(paths):
            raise ValueError("EXPERIMENT_OUTPUT_HASH_REQUIRED")
        for index, path in enumerate(paths):
            image_path, raw = contained_file(project_root, path)
            digest = sha256(raw)
            if role == "output" and digest != hashes[index]:
                raise ValueError("EXPERIMENT_OUTPUT_HASH_MISMATCH")
            if role == "input" and hashes and (len(hashes) != len(paths) or digest != hashes[index]):
                raise ValueError("EXPERIMENT_INPUT_HASH_MISMATCH")
            from PIL import Image
            from io import BytesIO
            try:
                with Image.open(BytesIO(raw)) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 16_000_000:
                        raise ValueError("EXPERIMENT_IMAGE_INVALID")
                    image.verify()
                    width, height = image.size
                    extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[image.format]
            except (OSError, SyntaxError):
                raise ValueError("EXPERIMENT_IMAGE_INVALID") from None
            filename = f"images/{digest}.{extension}"
            media[filename] = raw
            payload["images"].append({"role": role, "index": index, "path": filename,
                                      "sha256": digest, "width": width, "height": height})
    for key in ("elapsed_seconds", "estimated_cost_usd", "actual_cost_usd"):
        val = payload.get(key)
        if val is not None and (type(val) not in (float, int) or val < 0 or not math.isfinite(val)):
            raise ValueError("EXPERIMENT_METRIC_INVALID")
    raw_payload = canonical(payload)
    digest = sha256(raw_payload)
    stage = output_root / "packages" / record_id / digest
    stage.mkdir(parents=True, exist_ok=True)
    for name, raw in media.items():
        target = stage / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(raw)
    (stage / "record.json").write_bytes(raw_payload)
    if payload.get("prompt_text") is not None:
        (stage / "prompt.txt").write_text(payload["prompt_text"], encoding="utf-8")
    return {"record_id": record_id, "run_id": "img-" + sha256(record_id.encode())[:20],
            "manifest_sha256": digest, "payload": payload, "stage": stage}


def finops_summary(packages: list[dict]) -> dict:
    records = [p["payload"] for p in packages]
    api = [r for r in records if r["family"] == "api"]
    known_estimates = [r["estimated_cost_usd"] for r in api if r["estimated_cost_usd"] is not None]
    known_bills = [r["actual_cost_usd"] for r in api if r["actual_cost_usd"] is not None]
    fully_billed = bool(api) and len(known_bills) == len(api)
    result = {
        "policy_status": "PROVISIONAL_NOT_TEAM_APPROVED",
        "release_weight_finops_pct": 40, "release_weight_image_quality_pct": 40,
        "release_weight_service_pct": 20,
        "record_count": len(records), "api_call_count": len(api),
        "local_attempt_count": sum(r["family"] == "local_model" for r in records),
        "successful_execution_count": sum(r["status"] == "succeeded" for r in records),
        "failed_execution_count": sum(r["status"] == "failed" for r in records),
        "api_estimate_known_count": len(known_estimates),
        "api_source_estimate_count": sum(r.get("estimate_origin") == "RECORDED_IN_SOURCE" for r in api),
        "api_reconstructed_estimate_count": sum(r.get("estimate_origin") == "RECONSTRUCTED_WITH_RECORDED_B12_RATES" for r in api),
        "estimated_api_cost_usd": round(sum(known_estimates), 8) if known_estimates else None,
        "api_actual_cost_known_count": len(known_bills),
        "recorded_actual_api_cost_usd": round(sum(known_bills), 8) if fully_billed else None,
        "billing_reconciliation_coverage_pct": round(100 * sum(r.get("billing_status") == "RECONCILED" and r.get("actual_cost_usd") is not None for r in api) / len(api), 2) if api else None,
        "local_compute_cost_usd": None,
        "accepted_final_ad_count": 0,
        "actual_cost_per_accepted_final_ad_usd": None,
        "estimated_cost_per_accepted_final_ad_usd": None,
        "cost_per_ad_status": "NO_ACCEPTED_FINAL_ADS_IN_IMPORTED_EVIDENCE",
        "provided_team_budget_usd": 30,
        "team_account_budget_verified": False,
        "team_budget_spent_usd": None, "team_budget_remaining_usd": None,
        "team_budget_utilization_pct": None,
        "production_waste_ratio_pct": None,
        "waste_status": "RESEARCH_EXPERIMENTS_NOT_PRODUCTION_WASTE_SAMPLE",
        "finops_score": None, "release_score": None,
        "finops_gate": "BLOCKED_MISSING_ACCOUNT_BILLING_AND_FINAL_AD_EVIDENCE",
        "cost_scope": "recorded_image_experiments_only_not_team_total_or_full_ad_pipeline",
    }
    # Preserve historical overview bytes; add the new semantics only for v1 trials.
    if any((r.get("data_provenance") or {}).get("execution_schema") == "beautyai-experiment-execution-v1"
           for r in records):
        result.update(api_call_count=None, api_attempt_record_count=len(api),
            api_call_count_basis="UNKNOWN_FOR_NEW_RUNNER_SUBMISSION_ATTEMPTS",
            unresolved_execution_count=sum(r["status"] in {"unknown", "timed_out", "runner_error"} for r in records))
    return result


def validate_staged_package(package: dict) -> list[str]:
    """Reject staging changes before making any external upload."""
    stage = package["stage"].resolve(strict=True)
    payload = package["payload"]
    _, record_raw = contained_file(stage, stage / "record.json")
    if canonical(payload) != record_raw or sha256(record_raw) != package["manifest_sha256"]:
        raise ValueError("EXPERIMENT_STAGED_RECORD_MISMATCH")
    names = {"record.json"}
    for item in payload["images"]:
        _, raw = contained_file(stage, stage / item["path"])
        if sha256(raw) != item["sha256"]:
            raise ValueError("EXPERIMENT_STAGED_IMAGE_MISMATCH")
        names.add(item["path"])
    if payload.get("prompt_text") is not None:
        _, raw = contained_file(stage, stage / "prompt.txt")
        if raw != payload["prompt_text"].encode("utf-8"):
            raise ValueError("EXPERIMENT_STAGED_PROMPT_MISMATCH")
        names.add("prompt.txt")
    return sorted(names)


def table_row(package: dict, sdk: Any, run_url: str | None) -> list:
    p, stage = package["payload"], package["stage"]
    def image_for(role):
        item = next((i for i in p["images"] if i["role"] == role), None)
        return sdk.Image(str(stage / item["path"])) if item else None
    return [p["record_id"], p["family"], p["experiment_group"], p["operation"], p["status"], p["model"],
            image_for("input"), image_for("output"), p.get("prompt_name"),
            str(p["prompt_version"]) if p.get("prompt_version") is not None else None,
            p.get("prompt_text"), p.get("started_at"), p.get("elapsed_seconds"), p.get("timing_scope"),
            p.get("estimated_cost_usd"), p.get("estimate_origin"), p.get("actual_cost_usd"), p.get("billing_status"),
            p.get("cost_category"), p.get("quality_status"), p.get("data_kind"), p.get("review_notes"),
            p.get("langfuse_url"), run_url]


def verify_comparison_table(table: Any, packages: list[dict], urls: dict[str, str]) -> None:
    """Verify all scalar cells and the displayed input/output image pixels."""
    from types import SimpleNamespace
    from PIL import Image
    if table.columns != TABLE_COLUMNS or len(table.data) != len(packages):
        raise ValueError("WANDB_COMPARISON_TABLE_MISMATCH")
    expected = {p["record_id"]: table_row(p, SimpleNamespace(Image=lambda path: Path(path)), urls[p["record_id"]]) for p in packages}
    if len({row[0] for row in table.data}) != len(expected):
        raise ValueError("WANDB_COMPARISON_DUPLICATE_ROW")
    for row in table.data:
        wanted = expected.get(row[0])
        if wanted is None or len(row) != len(TABLE_COLUMNS):
            raise ValueError("WANDB_COMPARISON_TABLE_MISMATCH")
        for index, (actual, value) in enumerate(zip(row, wanted, strict=True)):
            if TABLE_COLUMNS[index] in {"original_input", "result"} and value is not None:
                # Image.from_json in pinned SDK downloads the image to _path.
                if not getattr(actual, "_path", None):
                    raise ValueError("WANDB_COMPARISON_IMAGE_MISSING")
                with Image.open(actual._path) as remote, Image.open(value) as local:
                    if remote.size != local.size or sha256(remote.convert("RGBA").tobytes()) != sha256(local.convert("RGBA").tobytes()):
                        raise ValueError("WANDB_COMPARISON_IMAGE_MISMATCH")
            elif actual != value:
                raise ValueError("WANDB_COMPARISON_CELL_MISMATCH")


def ensure_private_project(api: Any, entity: str, project: str) -> dict:
    """Pinned SDK GraphQL bridge: Public create_project has no access argument."""
    query = "query($name:String!,$entity:String!){project(name:$name,entityName:$entity){name access}}"
    variables = {"name": project, "entity": entity}
    result = api._service_api.execute_graphql(query, variables)
    current = result.get("project")
    if current:
        if current.get("access") != "PRIVATE":
            raise ValueError("WANDB_PROJECT_NOT_PRIVATE")
        return current
    from wandb.apis._generated import CREATE_PROJECT_GQL, UpsertModelInput
    args = UpsertModelInput(name=project, entity_name=entity, access="PRIVATE",
                           description="BeautyAI image experiments and provisional evaluation / Langfuse retained")
    api._service_api.execute_graphql(CREATE_PROJECT_GQL, {"input": args.model_dump(exclude_none=True)})
    check = api._service_api.execute_graphql(query, variables).get("project")
    if not check or check.get("access") != "PRIVATE":
        raise ValueError("WANDB_PROJECT_PRIVACY_UNVERIFIED")
    return check


def summary_for(package: dict) -> dict:
    p = package["payload"]
    result = {"manifest_sha256": package["manifest_sha256"], "record_id": p["record_id"],
              "source_status": p["status"], "quality_status": p["quality_status"],
              "actual_cost_known": p["actual_cost_usd"] is not None,
              "estimated_cost_known": p["estimated_cost_usd"] is not None,
              "billing_status": p["billing_status"], "timing_scope": p["timing_scope"],
              "original_started_at_known": p["started_at"] is not None,
              "image_count": len(p["images"]), "quality_score_status": "NOT_SCORED"}
    for key in ("elapsed_seconds", "estimated_cost_usd", "actual_cost_usd"):
        if p.get(key) is not None:
            result[key] = p[key]
    for key, val in (p.get("usage") or {}).items():
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            result[f"usage_{key}"] = val
    return result


def verify_package(api: Any, package: dict, remote_path: str, root: Path) -> dict:
    """Download the committed artifact, including images, and verify every hash."""
    remote = api.run(remote_path)
    summary = dict(remote.summary)
    if summary.get("manifest_sha256") != package["manifest_sha256"]:
        raise ValueError("WANDB_RUN_MANIFEST_MISMATCH")
    for key, expected in summary_for(package).items():
        if summary.get(key) != expected:
            raise ValueError("WANDB_RUN_SUMMARY_MISMATCH")
    artifacts = [a for a in remote.logged_artifacts() if a.type == "image-experiment"]
    matches = [a for a in artifacts if a.metadata.get("manifest_sha256") == package["manifest_sha256"]]
    if len(matches) != 1:
        raise ValueError("WANDB_EXPERIMENT_ARTIFACT_MISSING_OR_AMBIGUOUS")
    downloaded = Path(matches[0].download(root=str(root / package["run_id"])))
    if sha256((downloaded / "record.json").read_bytes()) != package["manifest_sha256"]:
        raise ValueError("WANDB_REMOTE_RECORD_MISMATCH")
    for item in package["payload"]["images"]:
        if sha256((downloaded / item["path"]).read_bytes()) != item["sha256"]:
            raise ValueError("WANDB_REMOTE_IMAGE_MISMATCH")
    prompt = package["payload"].get("prompt_text")
    if prompt is not None and (downloaded / "prompt.txt").read_text() != prompt:
        raise ValueError("WANDB_REMOTE_PROMPT_MISMATCH")
    return {"record_id": package["record_id"], "run_id": package["run_id"], "run_url": remote.url,
            "manifest_sha256": package["manifest_sha256"], "artifact": matches[0].qualified_name,
            "image_count_verified": len(package["payload"]["images"]), "status": "READBACK_VERIFIED"}


def _write(path: Path, value: dict) -> None:
    path.write_bytes(canonical(value))


def publish(packages: list[dict], plan_files: list[Path], root: Path, *, entity: str, project: str,
            sdk: Any = None, api: Any = None) -> dict:
    if not packages:
        raise ValueError("EXPERIMENTS_EMPTY")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", entity) or not re.fullmatch(r"[A-Za-z0-9_-]+", project):
        raise ValueError("WANDB_DESTINATION_INVALID")
    if sdk is None:
        import wandb as sdk
    api = api or sdk.Api(timeout=45)
    root.mkdir(parents=True, exist_ok=True)
    plan_data = {p.name: p.read_bytes() for p in plan_files}
    # Explicit plan files only; reject accidental secrets before an upload.
    for raw in plan_data.values():
        if _SECRET.search(raw.decode("utf-8")):
            raise ValueError("EXPERIMENT_SECRET_VALUE")
    for package in packages:
        validate_staged_package(package)
    privacy = ensure_private_project(api, entity, project)
    plan_sha = sha256(canonical({name: sha256(raw) for name, raw in plan_data.items()}))
    staged_plan = root / "plans" / plan_sha
    staged_plan.mkdir(parents=True, exist_ok=True)
    for name, raw in plan_data.items():
        (staged_plan / name).write_bytes(raw)
    batch_sha = sha256(canonical({"schema": SCHEMA_VERSION, "records": sorted(p["manifest_sha256"] for p in packages), "plan_sha256": plan_sha}))
    receipt_path = root / "upload-receipt.json"
    receipt = {"schema_version": SCHEMA_VERSION, "started_at": datetime.now(timezone.utc).isoformat(),
               "entity": entity, "project": project, "project_access": privacy["access"],
               "sdk_version": sdk.__version__, "batch_sha256": batch_sha, "records": [],
               "new_image_model_calls": 0, "status": "UPLOADING", "finops": finops_summary(packages)}
    _write(receipt_path, receipt)
    existing = {r.id: r for r in api.runs(f"{entity}/{project}", per_page=100)}
    settings = sdk.Settings(console="off", disable_code=True, disable_git=True, save_code=False,
                            x_disable_meta=True, x_disable_stats=True, silent=True,
                            init_timeout=60, finish_timeout=120, finish_timeout_raises=True)
    try:
        for package in packages:
            run_id, p = package["run_id"], package["payload"]
            old = existing.get(run_id)
            if old is not None:
                if dict(old.summary).get("manifest_sha256") is None:
                    raise ValueError("WANDB_PARTIAL_UPLOAD_REQUIRES_RECOVERY")
                if dict(old.summary).get("manifest_sha256") != package["manifest_sha256"]:
                    raise ValueError("WANDB_EXISTING_RUN_CONTENT_CONFLICT")
            else:
                names = validate_staged_package(package)
                run = sdk.init(entity=entity, project=project, id=run_id, resume="never", mode="online",
                               name=f'{p["experiment_group"]} / {p["operation"]} / {p["record_id"][:8]}',
                               group=p["experiment_group"], job_type="historical-image-experiment",
                               tags=["beautyai", p["family"], "historical-import", p["data_kind"], "research-cost"],
                               config={k: p.get(k) for k in ("schema_version", "record_id", "model", "operation", "family", "experiment_group", "started_at", "prompt_name", "prompt_version", "prompt_sha256", "request_hash", "timing_scope", "settings", "cost_category", "langfuse_url")},
                               dir=str(root), settings=settings)
                try:
                    artifact = sdk.Artifact(f"image-{run_id}", type="image-experiment",
                                            metadata={"manifest_sha256": package["manifest_sha256"], "record_id": p["record_id"]})
                    for name in names:
                        artifact.add_file(str(package["stage"] / name), name=name)
                    run.log_artifact(artifact)
                    run.log({"comparison": sdk.Table(columns=TABLE_COLUMNS, data=[table_row(package, sdk, run.url)])})
                    run.summary.update(summary_for(package))
                except Exception:
                    run.finish(exit_code=1)
                    raise
                else:
                    run.finish()
            api.flush()
            verified = verify_package(api, package, f"{entity}/{project}/{run_id}", root / "readback")
            receipt["records"].append(verified)
            _write(receipt_path, receipt)
            print(json.dumps({"event": "experiment_verified", "record_id": p["record_id"], "count": len(receipt["records"]), "total": len(packages)}), flush=True)
        overview_id = "overview-" + batch_sha[:20]
        overview_old = existing.get(overview_id)
        urls = {v["record_id"]: v["run_url"] for v in receipt["records"]}
        overview_payload = {"schema_version": SCHEMA_VERSION, "batch_sha256": batch_sha,
                            "finops": receipt["finops"], "plan_sha256": plan_sha,
                            "records": [{"record_id": p["record_id"], "manifest_sha256": p["manifest_sha256"], "run_url": urls[p["record_id"]]} for p in packages]}
        overview_json = root / "overview.json"
        _write(overview_json, overview_payload)
        if overview_old is None:
            run = sdk.init(entity=entity, project=project, id=overview_id, resume="never", mode="online",
                           name=f"BeautyAI history comparison / {len(packages)} attempts", job_type="comparison-and-finops",
                           tags=["beautyai", "comparison", "finops-40-percent", "provisional-evaluation"],
                           config={"batch_sha256": batch_sha, "evaluation_status": "PROVISIONAL_NOT_TEAM_APPROVED"},
                           dir=str(root), settings=settings)
            try:
                for family, key in ((None, "all_experiments"), ("api", "api_comparison"), ("local_model", "local_model_comparison")):
                    selected = [p for p in packages if family is None or p["payload"]["family"] == family]
                    if selected:
                        run.log({key: sdk.Table(columns=TABLE_COLUMNS, data=[table_row(p, sdk, urls[p["record_id"]]) for p in selected])})
                run.log({"finops": sdk.Table(columns=["metric", "value", "known"], data=[
                    [k, str(v) if v is not None else None, v is not None] for k, v in receipt["finops"].items()])})
                artifact = sdk.Artifact(f"comparison-{batch_sha[:20]}", type="image-comparison",
                                        metadata={"batch_sha256": batch_sha})
                artifact.add_file(str(overview_json), name="overview.json")
                run.log_artifact(artifact)
                plan = sdk.Artifact("beautyai-image-evaluation-plan", type="evaluation-plan",
                                    metadata={"plan_sha256": plan_sha, "status": "PROVISIONAL_NOT_TEAM_APPROVED", "finops_weight_pct": 40})
                for name, raw in plan_data.items():
                    if (staged_plan / name).read_bytes() != raw:
                        raise ValueError("EXPERIMENT_STAGED_PLAN_MISMATCH")
                    plan.add_file(str(staged_plan / name), name=name)
                run.log_artifact(plan)
                run.summary.update({"batch_sha256": batch_sha, "record_count": len(packages), "finops_weight_pct": 40,
                                    "estimated_api_cost_usd": receipt["finops"]["estimated_api_cost_usd"],
                                    "actual_cost_known": False, "release_score_status": "BLOCKED_MISSING_EVIDENCE",
                                    "finops_status": receipt["finops"]["finops_gate"]})
            except Exception:
                run.finish(exit_code=1)
                raise
            else:
                run.finish()
        elif dict(overview_old.summary).get("batch_sha256") != batch_sha:
            raise ValueError("WANDB_OVERVIEW_CONTENT_CONFLICT")
        api.flush()
        overview = api.run(f"{entity}/{project}/{overview_id}")
        if dict(overview.summary).get("batch_sha256") != batch_sha:
            raise ValueError("WANDB_OVERVIEW_READBACK_MISMATCH")
        artifacts = list(overview.logged_artifacts())
        comparison = next(a for a in artifacts if a.type == "image-comparison" and a.metadata.get("batch_sha256") == batch_sha)
        retrieved = Path(comparison.download(root=str(root / "readback" / overview_id)))
        if json.loads((retrieved / "overview.json").read_text()) != overview_payload:
            raise ValueError("WANDB_OVERVIEW_ARTIFACT_MISMATCH")
        table_artifacts = [a for a in artifacts if a.type == "run_table"]
        count_table = next((a.get("all_experiments") for a in table_artifacts if "all_experiments" in a.name), None)
        if count_table is None:
            raise ValueError("WANDB_COMPARISON_TABLE_MISMATCH")
        verify_comparison_table(count_table, packages, urls)
        plan_artifact = next(a for a in artifacts if a.type == "evaluation-plan" and a.metadata.get("plan_sha256") == plan_sha)
        downloaded_plan = Path(plan_artifact.download(root=str(root / "readback" / "evaluation-plan")))
        if any((downloaded_plan / name).read_bytes() != raw for name, raw in plan_data.items()):
            raise ValueError("WANDB_EVALUATION_PLAN_MISMATCH")
        receipt.update(status="READBACK_VERIFIED", overview_url=overview.url,
                       project_url=api.project(project, entity=entity).url,
                       evaluation_artifact=plan_artifact.qualified_name,
                       comparison_row_count=len(count_table.data),
                       finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        receipt.update(status="INCOMPLETE", error_code=str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[A-Z_]+", str(exc)) else type(exc).__name__)
        _write(receipt_path, receipt)
        raise ValueError(receipt["error_code"]) from None
    _write(receipt_path, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--entity")
    parser.add_argument("--project", default=PROJECT_NAME)
    parser.add_argument("--publish", action="store_true", help="Upload existing evidence only; never generates images")
    args = parser.parse_args()
    from .experiment_records import collect_experiments
    records = collect_experiments(args.project_root)
    args.output.mkdir(parents=True, exist_ok=True)
    packages = [prepare_package(r, args.project_root, args.output) for r in records]
    _write(args.output / "prepared.json", {"status": "PREPARED", "record_count": len(packages), "finops": finops_summary(packages),
        "records": [{"record_id": p["record_id"], "manifest_sha256": p["manifest_sha256"]} for p in packages]})
    if not args.publish:
        print(json.dumps({"status": "PREPARED", "record_count": len(packages), "new_image_model_calls": 0}))
        return
    import wandb
    api = wandb.Api(timeout=45)
    entity = args.entity or api.default_entity
    if not entity:
        raise ValueError("WANDB_ENTITY_REQUIRED")
    source_root = Path(__file__).resolve().parents[1]
    plan_files = [source_root / p for p in ["docs/imageEvaluationPlan.md", "examples/evaluation/image-eval-v0.1.json", "examples/evaluation/real-product-manifest.template.json"]]
    result = publish(packages, plan_files, args.output, entity=entity, project=args.project, sdk=wandb, api=api)
    print(json.dumps({"status": result["status"], "overview_url": result["overview_url"], "record_count": len(result["records"])}))


if __name__ == "__main__":
    main()
