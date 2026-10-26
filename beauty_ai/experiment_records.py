"""Read historical image evidence without importing models, templates or network clients.

Only known live experiment families are scanned. A record is a real job, not a
PNG copy: repeated executions remain distinct even if output bytes are equal.
Stored snapshots are authoritative; absent prompt text/cost stays unknown.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse
from uuid import UUID

_API_GROUPS = ("openai-live-smoke", "cosmetics-comparison", "cosmetics-matte-refinement")
_LOCAL = re.compile(r"b(?:6-model-smoke|7-model-smoke(?:-attempt[1-9][0-9]*)?|9-model-benchmark)(?:-run-[A-Za-z0-9-]+)?$")
_RUN = re.compile(r"run-(?:[0-9]{8}T[0-9]{6}Z|[0-9]{8}-[0-9]{6})-[0-9a-f]{8,32}$")
_SHA = re.compile(r"[0-9a-f]{64}$")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,79}$")
_MODELS = {"birefnet-lite": "ZhengPeng7/BiRefNet_lite",
           "sdxl-inpaint": "stabilityai/stable-diffusion-xl-base-1.0"}


def _fail(code: str):
    raise ValueError(code) from None


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        _fail("EXPERIMENT_INVALID_EVIDENCE")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("EXPERIMENT_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _parse(text: str) -> dict:
    try:
        return _object(json.loads(text, object_pairs_hook=_pairs,
                                 parse_constant=lambda _: _fail("EXPERIMENT_INVALID_EVIDENCE")))
    except (TypeError, UnicodeError, json.JSONDecodeError):
        _fail("EXPERIMENT_INVALID_EVIDENCE")


def _file(root: Path, path: Path, *, limit: int = 64 * 1024 * 1024) -> Path:
    try:
        candidate = path if path.is_absolute() else root / path
        relative = candidate.relative_to(root)
        if ".." in relative.parts:
            _fail("EXPERIMENT_UNSAFE_PATH")
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                _fail("EXPERIMENT_UNSAFE_PATH")
        candidate = candidate.resolve(strict=True)
        candidate.relative_to(root)
        if not candidate.is_file() or candidate.stat().st_size > limit:
            _fail("EXPERIMENT_INVALID_FILE")
        return candidate
    except FileNotFoundError:
        _fail("EXPERIMENT_MISSING_FILE")
    except (OSError, RuntimeError):
        _fail("EXPERIMENT_INVALID_FILE")
    except ValueError as error:
        if str(error).startswith("EXPERIMENT_"):
            raise
        _fail("EXPERIMENT_UNSAFE_PATH")


def _read(root: Path, path: Path) -> dict:
    try:
        return _parse(_file(root, path, limit=4 * 1024 * 1024).read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        _fail("EXPERIMENT_INVALID_EVIDENCE")


def _digest(value: object) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _fail("EXPERIMENT_INVALID_HASH")
    return value


def _hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        _fail("EXPERIMENT_INVALID_FILE")


def _json_hash(value: dict) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()
    except (ValueError, TypeError, UnicodeError):
        _fail("EXPERIMENT_INVALID_EVIDENCE")


def _number(value: object) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        _fail("EXPERIMENT_INVALID_NUMBER")
    return float(value)


def _time(value: object) -> str | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            _fail("EXPERIMENT_INVALID_TIME")
    except ValueError:
        _fail("EXPERIMENT_INVALID_TIME")
    return value


def _uuid(value: object) -> str:
    try:
        return UUID(value).hex
    except (ValueError, TypeError, AttributeError):
        _fail("EXPERIMENT_INVALID_ID")


def _jobs(root: Path, run: Path) -> tuple[Path, list[dict]]:
    database = _file(root, run / "runtime/jobs.sqlite3")
    wal = database.with_name(database.name + "-wal")
    if wal.exists() and _file(root, wal).stat().st_size:
        # immutable=1 deliberately ignores WAL. Reject a still-active snapshot
        # rather than silently reporting a partial history or checkpointing it.
        _fail("EXPERIMENT_UNCHECKPOINTED_LEDGER")
    try:
        # Immutable historical files: read only, without WAL/SHM side effects.
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute(
                "SELECT job_id, project_id, request_hash, request_json, status, result_json, "
                "error_code, created_at, updated_at FROM jobs ORDER BY created_at, job_id LIMIT 1001")]
    except sqlite3.Error:
        _fail("EXPERIMENT_INVALID_LEDGER")
    if len(rows) > 1000:
        _fail("EXPERIMENT_TOO_MANY_RECORDS")
    for row in rows:
        _uuid(row["job_id"])
        _uuid(row["project_id"])
        row["request"] = _parse(row.pop("request_json"))
        if _json_hash(row["request"]) != _digest(row["request_hash"]):
            _fail("EXPERIMENT_REQUEST_HASH_MISMATCH")
        result_json = row.pop("result_json")
        row["result"] = _parse(result_json) if result_json is not None else None
    return database, rows


def _asset(root: Path, run: Path, project: str, ref: dict) -> tuple[str, str]:
    ref = _object(ref)
    expected = _digest(ref.get("sha256"))
    path = _file(root, run / "runtime/assets/files" / _uuid(project) / (_uuid(ref.get("asset_id")) + ".png"),
                 limit=24 * 1024 * 1024)
    if _hash(path) != expected:
        _fail("EXPERIMENT_ASSET_HASH_MISMATCH")
    # The decoder/asset store validated this PNG at ingestion; still reject non-PNG files.
    try:
        with path.open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                _fail("EXPERIMENT_INVALID_IMAGE")
    except OSError:
        _fail("EXPERIMENT_INVALID_FILE")
    return str(path), expected


def _api_rows(root: Path, run: Path, report: dict, entries: list[dict]) -> list[dict]:
    if (run / "runtime/jobs.sqlite3").exists():
        return _jobs(root, run)[1]
    # B13 saved the exact request and provider receipt, without retaining its
    # transient runtime DB. Only its explicit source.png/image.png can be used.
    rows = []
    for entry in entries:
        preset = entry.get("preset_id")
        if preset is not None and preset not in {"clean_studio", "premium_minimal", "vivid_pop"}:
            _fail("EXPERIMENT_INVALID_PROMPT")
        folder = run / preset if preset else run
        request = _read(root, folder / "request.json")
        receipt = _object(entry.get("provider_receipt", {}))
        job_id = entry.get("job_id")
        _uuid(job_id)
        request_hash = _digest(entry.get("request_hash"))
        if request_hash != _json_hash(request):
            _fail("EXPERIMENT_REQUEST_HASH_MISMATCH")
        status = entry.get("status", report.get("status", "")).lower()
        rows.append({"job_id": job_id, "project_id": None, "request": request,
                     "request_hash": request_hash, "result": receipt.get("result"),
                     "status": status, "error_code": receipt.get("error_code"),
                     "created_at": entry.get("started_at", receipt.get("created_at")),
                     "updated_at": entry.get("finished_at", receipt.get("updated_at")),
                     "saved_folder": folder})
    return rows


def _prompt(root: Path, run: Path, row: dict, entry: dict) -> tuple:
    request = row["request"]
    managed = request.get("managed_prompt")
    if managed is not None:
        managed = _object(managed)
        text, sha = managed.get("rendered_text"), _digest(managed.get("sha256"))
        if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != sha:
            _fail("EXPERIMENT_PROMPT_HASH_MISMATCH")
        name, version = managed.get("name"), managed.get("version")
        if not isinstance(name, str) or type(version) is not int or version < 1:
            _fail("EXPERIMENT_INVALID_PROMPT")
        for field, value in (("prompt_name", name), ("prompt_version", version), ("prompt_sha256", sha)):
            if field in entry and entry[field] != value:
                _fail("EXPERIMENT_PROMPT_HASH_MISMATCH")
        preset = entry.get("preset_id")
        if preset is not None and preset not in {"clean_studio", "premium_minimal", "vivid_pop"}:
            _fail("EXPERIMENT_INVALID_PROMPT")
        folder = run / preset if preset else run
        stored = folder / "provider-prompt.txt"
        if stored.exists():
            if _hash(_file(root, stored, limit=100_000)) != sha:
                _fail("EXPERIMENT_PROMPT_HASH_MISMATCH")
        return text, name, version, sha, "stored_final_provider_prompt"
    prompt = request.get("prompt") or request.get("image_prompt")
    if prompt is None:
        return None, None, None, None, "not_recorded"
    prompt = _object(prompt)
    text, version = prompt.get("positive"), prompt.get("prompt_version")
    if text is not None and (not isinstance(text, str) or not text):
        _fail("EXPERIMENT_INVALID_PROMPT")
    if version is not None and not ((isinstance(version, str) and version) or
                                    (type(version) is int and version > 0)):
        _fail("EXPERIMENT_INVALID_PROMPT")
    # Do not reconstruct historical adapter suffixes with current code.
    return (text, None, version, hashlib.sha256(text.encode()).hexdigest() if text is not None else None,
            "stored_positive_prompt_only" if text is not None else "not_recorded")


def _quality(report: dict, entry: dict, operation: str) -> tuple[str, str]:
    visual = report.get("visual_review", {})
    visual = visual if isinstance(visual, dict) else {}
    candidates = [entry.get("human_review"), report.get("human_review"), visual.get("human_review"),
                  visual.get("quality_approval"), report.get("quality_approval")]
    status = next((x for x in candidates if x in {"PENDING", "NOT_APPROVED", "NOT_ASSESSED"}), "NOT_ASSESSED")
    notes = entry.get("assistant_visual_review") or report.get("assistant_visual_review") or visual.get(operation)
    if isinstance(notes, dict):
        notes = json.dumps(notes, ensure_ascii=False, sort_keys=True)
    return status, notes if isinstance(notes, str) else "No human quality approval is recorded."


def _url(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail("EXPERIMENT_UNSAFE_URL")
    parsed = urlparse(value)
    if (parsed.scheme != "https" or parsed.hostname not in {"jp.cloud.langfuse.com", "cloud.langfuse.com", "us.cloud.langfuse.com"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not re.fullmatch(r"/project/[A-Za-z0-9_-]+/traces/[0-9a-f]{32}", parsed.path)):
        _fail("EXPERIMENT_UNSAFE_URL")
    return value


def _record(root: Path, run: Path, row: dict, report_path: Path, report: dict, entry: dict, family: str) -> dict:
    request, result = row["request"], row["result"]
    status = row["status"]
    if status not in {"succeeded", "failed"}:
        _fail("EXPERIMENT_UNFINISHED_JOB")
    if entry.get("job_id", row["job_id"]) != row["job_id"] or entry.get("request_hash", row["request_hash"]) != row["request_hash"]:
        _fail("EXPERIMENT_RECEIPT_MISMATCH")
    if status == "succeeded" and not isinstance(result, dict):
        _fail("EXPERIMENT_INVALID_EVIDENCE")
    operation = request.get("operation") if family == "api" else request.get("kind")
    if operation not in {"generate", "edit", "cutout", "render_background"}:
        _fail("EXPERIMENT_UNSUPPORTED_OPERATION")
    sources = request.get("product_images", []) + request.get("reference_images", []) if family == "api" else [request["source"]]
    def asset(ref: dict, *, source: bool) -> tuple[str, str]:
        if row["project_id"] is not None:
            return _asset(root, run, row["project_id"], ref)
        if family != "api" or len(sources) > 1:
            _fail("EXPERIMENT_MISSING_FILE")
        path = run / "source.png" if source else row["saved_folder"] / "image.png"
        path = _file(root, path, limit=24 * 1024 * 1024)
        sha = _digest(_object(ref).get("sha256"))
        if _hash(path) != sha or not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            _fail("EXPERIMENT_ASSET_HASH_MISMATCH")
        return str(path), sha
    source_assets = [asset(ref, source=True) for ref in sources]
    recorded_source_sha = report.get("source_sha256")
    code_snapshot = None
    if isinstance(recorded_source_sha, dict) and family == "local_model":
        # B9 uses this field for CODE hashes, not an input image hash. Never
        # resolve these labels against today's source tree or hash current code.
        code_snapshot = {}
        for label, digest in recorded_source_sha.items():
            if not isinstance(label, str) or not re.fullmatch(r"beauty_ai/[A-Za-z0-9_]+\.py", label):
                _fail("EXPERIMENT_INVALID_EVIDENCE")
            code_snapshot[label] = _digest(digest)
    elif recorded_source_sha is not None and source_assets:
        if _digest(recorded_source_sha) != source_assets[0][1]:
            _fail("EXPERIMENT_ASSET_HASH_MISMATCH")
    output_refs = []
    if status == "succeeded":
        if family == "api":
            output_refs = [result["image"]]
        elif operation == "cutout":
            output_refs = [result["product_rgba"], result["preserve_mask"]]
        else:
            output_refs = [result[name] for name in ("scene_underlay", "product_rgba", "composed_preview")]
    outputs = [asset(ref, source=False) for ref in output_refs]
    if entry.get("output_sha256") is not None and isinstance(entry["output_sha256"], str):
        if not outputs or outputs[0][1] != _digest(entry["output_sha256"]):
            _fail("EXPERIMENT_ASSET_HASH_MISMATCH")
    # Explicit report paths are never trusted as arbitrary upload candidates.
    image_path = entry.get("image_path") or (entry.get("image") or {}).get("path")
    if image_path is not None:
        if not isinstance(image_path, str) or not Path(image_path).is_absolute():
            _fail("EXPERIMENT_UNSAFE_PATH")
        named = _file(root, Path(image_path), limit=24 * 1024 * 1024)
        if not outputs or _hash(named) != outputs[0][1]:
            _fail("EXPERIMENT_ASSET_HASH_MISMATCH")
        outputs[0] = (str(named), outputs[0][1])
    elif family == "api" and outputs:
        candidate = run / "image.png"
        if candidate.exists():
            named = _file(root, candidate, limit=24 * 1024 * 1024)
            if _hash(named) != outputs[0][1]:
                _fail("EXPERIMENT_ASSET_HASH_MISMATCH")
            outputs[0] = (str(named), outputs[0][1])
    for source_name in ("source.png", "synthetic-product.png"):
        candidate = run / source_name
        if candidate.exists() and source_assets:
            candidate = _file(root, candidate, limit=24 * 1024 * 1024)
            digest = _hash(candidate)
            if digest == source_assets[0][1]:
                source_assets[0] = (str(candidate), digest)
    text, name, version, prompt_sha, prompt_scope = _prompt(root, run, row, entry)
    receipt = _object(entry.get("provider_receipt", {}))
    if receipt and (receipt.get("job_id") != row["job_id"] or receipt.get("request_hash") != row["request_hash"]):
        _fail("EXPERIMENT_RECEIPT_MISMATCH")
    if receipt.get("result") is not None and receipt["result"] != result:
        _fail("EXPERIMENT_RECEIPT_MISMATCH")
    usage = _object(receipt.get("usage") or {})
    if any(type(v) is not int or v < 0 for v in usage.values() if v is not None):
        _fail("EXPERIMENT_INVALID_NUMBER")
    elapsed = entry.get("elapsed_seconds")
    timing_scope = "client_submit_poll_elapsed_seconds"
    if elapsed is None:
        timing = entry.get("ledger_timing", {})
        elapsed = timing.get("ledger_elapsed_ms")
        if elapsed is not None:
            elapsed = _number(elapsed) / 1000
        else:
            first, last = _time(row["created_at"]), _time(row["updated_at"])
            elapsed = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds() if first and last else None
        timing_scope = "ledger_created_to_updated_wall_clock_seconds" if elapsed is not None else "not_recorded"
    quality, notes = _quality(report, entry, operation)
    settings = {key: request[key] for key in ("pipeline_profile_id", "pipeline_revision", "width", "height", "quality", "seed", "visual") if key in request}
    legacy_prompt = request.get("prompt") or request.get("image_prompt") or {}
    if legacy_prompt.get("negative") is not None:
        settings["negative_prompt"] = legacy_prompt["negative"]
    if family == "local_model":
        settings["device"] = report.get("device", "mps")
    declared_data = report.get("data_kind")
    declaration = " ".join(str(v) for v in (report.get("input", report.get("fixture", "")),
                            text or "", report.get("visual_review", {}))).lower()
    data_kind = (declared_data if declared_data in {"synthetic_fixture", "real_product", "unknown"}
                 else "synthetic_fixture" if any(word in declaration for word in ("synthetic", "fictional"))
                 or (run / "synthetic-product.png").exists() else "unknown")
    error_code = row.get("error_code")
    if error_code is not None and (not isinstance(error_code, str) or not _CODE.fullmatch(error_code)):
        _fail("EXPERIMENT_INVALID_ERROR_CODE")
    trace = entry.get("langfuse") or {}
    model = request.get("model") if family == "api" else _MODELS.get(request.get("pipeline_profile_id"))
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9/_.-]{1,200}", model):
        _fail("EXPERIMENT_INVALID_MODEL")
    billing = receipt.get("billing_status", report.get("billing_status", "UNRECONCILED"))
    if family == "api" and billing not in {"UNRECONCILED", "RECONCILED", "UNKNOWN"}:
        _fail("EXPERIMENT_INVALID_BILLING_STATUS")
    return {
        "record_id": hashlib.sha256((family + ":" + row["job_id"]).encode()).hexdigest(),
        "family": family, "experiment_group": str(run.relative_to(root / "outputs")),
        "status": status, "error_code": error_code, "operation": operation,
        "model": model,
        "source_paths": list(dict.fromkeys(p for p, _ in source_assets)),
        "output_paths": [p for p, _ in outputs], "output_sha256s": [h for _, h in outputs],
        "prompt_text": text, "prompt_name": name, "prompt_version": version, "prompt_sha256": prompt_sha,
        "request_hash": row["request_hash"], "source_report": str(report_path), "source_report_sha256": _hash(report_path),
        "started_at": _time(entry.get("started_at", row["created_at"])), "elapsed_seconds": _number(elapsed),
        "timing_scope": timing_scope,
        "estimated_cost_usd": _number(entry.get("estimated_cost_usd")) if family == "api" else None,
        "actual_cost_usd": _number(receipt.get("actual_cost_usd", entry.get("actual_cost_usd"))) if family == "api" else None,
        "cost_category": "research_experiment",
        "billing_status": billing if family == "api" else "NOT_APPLICABLE_LOCAL_BILLING",
        "usage": usage, "settings": settings, "langfuse_url": _url(trace.get("trace_url")),
        "quality_status": quality, "review_notes": notes, "data_kind": data_kind,
        "data_provenance": {"job_id": row["job_id"], "prompt_scope": prompt_scope,
                            "started_at_basis": "operation_receipt" if entry.get("started_at") else "ledger_created_at",
                            "source_sha256s": [h for _, h in source_assets],
                            "code_snapshot_sha256s": code_snapshot,
                            "model_invocation": "recorded_success" if status == "succeeded" else "failed_before_successful_output",
                            "no_new_model_calls": True},
    }


def collect_experiments(project_root: Path) -> list[dict]:
    """Normalize canonical historical records; reject unsafe or inconsistent evidence.

    New dated run folders in the three known API families are discovered. Local
    B6/B7/B9 families and their numbered attempts are supported; backups, prompt
    previews, mock demos, arbitrary folders and loose copied PNGs are excluded.
    """
    try:
        root = Path(project_root)
        if root.is_symlink() or not root.is_dir() or not (root / "outputs").is_dir():
            _fail("EXPERIMENT_ROOT_MISSING")
        root = root.resolve()
        outputs = root / "outputs"
        if outputs.is_symlink():
            _fail("EXPERIMENT_UNSAFE_PATH")
        records = []
        for family_name in _API_GROUPS:
            parent = outputs / family_name
            if not parent.exists():
                continue
            if parent.is_symlink() or not parent.is_dir():
                _fail("EXPERIMENT_UNSAFE_PATH")
            for run in sorted(parent.iterdir()):
                if not _RUN.fullmatch(run.name):
                    continue
                report_path = _file(root, run / "report.json", limit=4 * 1024 * 1024)
                report = _read(root, report_path)
                if report.get("mode") not in (None, "LIVE_USER_AUTHORIZED") or report.get("http_post_attempts", 0) == 0:
                    continue
                entries = report.get("operations")
                entries = entries if isinstance(entries, list) else [report]
                by_id = {_object(entry).get("job_id"): entry for entry in entries}
                jobs = _api_rows(root, run, report, entries)
                for row in jobs:
                    if row["request"].get("kind") != "api_image" or row["job_id"] not in by_id:
                        continue
                    records.append(_record(root, run, row, report_path, report, by_id[row["job_id"]], "api"))
        trials = outputs / "experiment-trials"
        if trials.exists():
            if trials.is_symlink() or not trials.is_dir():
                _fail("EXPERIMENT_UNSAFE_PATH")
            from .experiment_trial_records import collect_trial
            for run in sorted(trials.iterdir()):
                if run.is_symlink():
                    _fail("EXPERIMENT_UNSAFE_PATH")
                if run.is_dir() and (run / "plan.json").exists() and (run / "report.json").exists():
                    records.extend(collect_trial(run / "plan.json"))
        for run in sorted(outputs.iterdir()):
            if not _LOCAL.fullmatch(run.name):
                continue
            database, jobs = _jobs(root, run)
            receipt_path = run / "receipt.json"
            report = _read(root, receipt_path) if receipt_path.exists() else {}
            report_path = _file(root, receipt_path) if receipt_path.exists() else database
            if report.get("model_invoked") is False or report.get("device", "mps") != "mps":
                continue
            entries = {_object(x).get("job_id"): x for x in report.get("jobs", [])}
            for row in jobs:
                if row["request"].get("pipeline_profile_id") not in _MODELS:
                    continue
                records.append(_record(root, run, row, report_path, report, entries.get(row["job_id"], {}), "local_model"))
        unique = {}
        for record in sorted(records, key=lambda x: (x["started_at"] or "", x["record_id"], x["experiment_group"])):
            previous = unique.get(record["record_id"])
            if previous is not None:
                if any(previous[key] != record[key] for key in ("request_hash", "output_sha256s", "status", "prompt_sha256")):
                    _fail("EXPERIMENT_CONFLICTING_DUPLICATE")
                continue
            unique[record["record_id"]] = record
        return list(unique.values())
    except ValueError as error:
        if str(error).startswith("EXPERIMENT_"):
            raise
        _fail("EXPERIMENT_INVALID_EVIDENCE")
    except (KeyError, TypeError, AttributeError, OSError, UnicodeError):
        _fail("EXPERIMENT_INVALID_EVIDENCE")
