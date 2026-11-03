"""Prepare a local freeform image request; submit only with explicit execution flags.

Preparation normalizes photos using the existing asset store and never reads
credentials. Execution shares the normal processor, attempt ledger and account
budget gate. Reuse a saved preparation with --prepared to retain its job identity.
"""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
from uuid import UUID, uuid4

import httpx

from .account_budget import AccountBudgetStore, load_account_budget
from .api_image_processor import ExternalImageProcessor
from .assets import LocalAssetStore
from .contracts import AssetRef, ExternalImageRequest, ImagePrompt, content_hash
from .errors import GatewayError
from .experiment_inputs import (_absolute_without_links, _destination, _read_source,
                                MAX_PROVIDER_IMAGE_BYTES, MAX_PROVIDER_TOTAL_BYTES)
from .experiment_runner import _read_json, write_json
from .experiment_plan import DEFAULT_MODEL
from .openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from .provider_attempts import ProviderAttemptStore, money_units

SCHEMA = "beautyai-freeform-preparation-v1"


def _prompt_file(path: Path) -> str:
    try:
        source = _absolute_without_links(path)
        if not source.is_file():
            raise ValueError
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(16001)
        if len(raw) > 16000:
            raise ValueError
        return raw.decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        raise ValueError("FREEFORM_PROMPT_FILE_INVALID") from None


def prepare_freeform(*, products: list[Path], references: list[Path], prompt_file: Path,
                     output: Path, project_id: UUID | None = None, job_id: UUID | None = None,
                     prompt_version: str = "freeform-v1", language: str = "ko",
                     width: int = 1152, height: int = 1440, quality: str = "medium",
                     model: str = DEFAULT_MODEL) -> Path:
    """Save a revision-1 request and escaped preview without provider submissions."""
    if not 1 <= len(products) <= 5 or not 0 <= len(references) <= 5:
        raise ValueError("FREEFORM_INPUT_COUNT_INVALID")
    project_id, job_id = project_id or uuid4(), job_id or uuid4()
    if not isinstance(project_id, UUID) or not isinstance(job_id, UUID):
        raise ValueError("FREEFORM_ID_INVALID")
    prompt = ImagePrompt(positive=_prompt_file(prompt_file), prompt_version=prompt_version,
                         source_revision=1, language=language)
    root = _destination(output)
    sources = [(role, index, *_read_source(path))
               for role, paths in (("product", products), ("reference", references))
               for index, path in enumerate(paths, 1)]
    root.mkdir(parents=True, exist_ok=False)
    (root / "inputs").mkdir()
    assets = LocalAssetStore(root / "runtime/assets")
    inputs, hashes, total = [], set(), 0
    for role, index, name, raw in sources:
        ref = assets.put_upload(raw, project_id=project_id)
        normalized = assets.read_bytes(ref, project_id=project_id)
        total += len(normalized)
        if len(normalized) > MAX_PROVIDER_IMAGE_BYTES or total > MAX_PROVIDER_TOTAL_BYTES:
            raise ValueError("FREEFORM_INPUT_TOO_LARGE")
        if ref.sha256 in hashes:
            raise ValueError("FREEFORM_DUPLICATE_INPUT")
        hashes.add(ref.sha256)
        relative = f"inputs/{role}-{index:02d}.png"
        with (root / relative).open("xb") as stream:
            stream.write(normalized)
        inputs.append({"role": role, "index": index, "name": name, "path": relative,
                       "source_sha256": hashlib.sha256(raw).hexdigest(),
                       "asset_ref": ref.model_dump(mode="json")})
    refs = lambda role: [AssetRef.model_validate(item["asset_ref"]) for item in inputs if item["role"] == role]
    request = ExternalImageRequest(operation="edit", pipeline_revision="1", prompt=prompt,
        product_images=refs("product"), reference_images=refs("reference"), width=width,
        height=height, quality=quality, model=model)
    provider_prompt = OpenAIImagesAdapter._prompt(request)
    prepared = {"schema_version": SCHEMA, "project_id": str(project_id), "job_id": str(job_id),
        "request": request.model_dump(mode="json"), "request_sha256": content_hash(request),
        "provider_prompt": provider_prompt,
        "provider_prompt_sha256": hashlib.sha256(provider_prompt.encode()).hexdigest(),
        "inputs": inputs, "image_api_calls": 0, "status": "PREPARED_NOT_RUN"}
    write_json(root / "request.json", prepared)
    pictures = "".join(f'<figure><img src="{item["path"]}" alt="{item["role"]} {item["index"]}">'
                       f'<figcaption>{escape(item["name"])}</figcaption></figure>' for item in inputs)
    page = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>Freeform 미리보기</title>'
            '<style>body{max-width:960px;margin:32px auto;font:16px sans-serif}img{max-width:240px}'
            'figure{display:inline-block}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            '<h1>Freeform 미리보기</h1><p>PREPARED_NOT_RUN · 이미지 API 호출 0회</p>'
            f'<p>프로젝트 {project_id} · 작업 {job_id}</p>{pictures}'
            f'<p>{escape(model)} · {width} × {height} · {escape(quality)}</p>'
            f'<pre>{escape(provider_prompt)}</pre></html>')
    (root / "index.html").write_text(page, encoding="utf-8")
    return root / "request.json"


def execute_freeform(prepared_path: Path, *, project_id: UUID, job_id: UUID,
                     attempt_db: Path, env_file: Path | None = None,
                     account_budget: AccountBudgetStore | None = None,
                     budget_usd: str = "0", reservation_usd: str = "1",
                     transport: httpx.MockTransport | None = None,
                     allow_live: bool = False) -> dict:
    """Execute one saved request, preserving processor reuse and no-retry rules."""
    offline = isinstance(transport, httpx.MockTransport)
    if transport is not None and not offline:
        raise ValueError("FREEFORM_TRANSPORT_INVALID")
    if not offline and allow_live is not True:
        raise ValueError("FREEFORM_LIVE_NOT_AUTHORIZED")
    if not offline and account_budget is None:
        raise GatewayError("ACCOUNT_BUDGET_REQUIRED", "ACCOUNT_BUDGET_REQUIRED")
    if not offline and env_file is None:
        raise ValueError("FREEFORM_ENV_FILE_REQUIRED")
    if not isinstance(project_id, UUID) or not isinstance(job_id, UUID):
        raise ValueError("FREEFORM_ID_INVALID")
    source = _absolute_without_links(prepared_path)
    prepared = _read_json(source)
    request = ExternalImageRequest.model_validate(prepared["request"])
    if (prepared.get("schema_version") != SCHEMA or prepared.get("project_id") != str(project_id)
            or prepared.get("job_id") != str(job_id) or request.pipeline_revision != "1"
            or request.operation != "edit" or content_hash(request) != prepared.get("request_sha256")
            or OpenAIImagesAdapter._prompt(request) != prepared.get("provider_prompt")
            or hashlib.sha256(prepared["provider_prompt"].encode()).hexdigest()
            != prepared.get("provider_prompt_sha256")):
        raise ValueError("FREEFORM_PREPARATION_MISMATCH")
    if money_units(budget_usd) < money_units(reservation_usd, positive=True):
        raise ValueError("FREEFORM_BUDGET_INSUFFICIENT")
    assets = LocalAssetStore(source.parent / "runtime/assets")
    products = [assets.read_bytes(ref, project_id=project_id) for ref in request.product_images]
    references = [assets.read_bytes(ref, project_id=project_id) for ref in request.reference_images]
    # Validate the exact normalized inputs without loading credentials or posting.
    OpenAIImagesAdapter(OpenAIImagesConfig(), transport=httpx.MockTransport(
        lambda _: (_ for _ in ()).throw(AssertionError("Preflight cannot submit"))
    )).validate_input(request, products, references)
    database = _absolute_without_links(attempt_db)
    if account_budget is not None and database.resolve() == account_budget.path.resolve():
        raise ValueError("FREEFORM_ATTEMPT_DATABASE_CONFLICT")
    settings = {}
    if not offline:
        from .experiment_execution import _load_settings
        settings = _load_settings(env_file)
    attempts = ProviderAttemptStore(database)
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(live_calls=not offline,
        api_key=settings.get("OPENAI_API_KEY"), timeout_seconds=300), transport=transport)
    processor = ExternalImageProcessor(assets, adapter, attempts, budget_limit_usd=budget_usd,
        reservation_usd=reservation_usd, account_budget=account_budget)
    result = processor.process_job(request, project_id, job_id)
    from .experiment_execution import _write_bytes
    _write_bytes(source.parent / "image.png", assets.read_bytes(result.image, project_id=project_id))
    report = {"schema_version": "beautyai-freeform-execution-v1", "status": "SUCCEEDED",
        "mode": "OFFLINE_MOCK" if offline else "LIVE_USER_AUTHORIZED",
        "project_id": str(project_id), "job_id": str(job_id),
        "request_sha256": content_hash(request), "result": result.model_dump(mode="json"),
        "provider_receipt": attempts.get(job_id, project_id), "image_path": "image.png",
        "actual_cost_usd": None, "billing_status": "UNRECONCILED", "review_status": "NOT_APPROVED"}
    write_json(source.parent / "generation-result.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, help="Use an existing request.json and its stable job identity")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--product", type=Path, action="append", default=[])
    parser.add_argument("--reference", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prompt-version", default="freeform-v1")
    parser.add_argument("--language", choices=["en", "ko"], default="ko")
    parser.add_argument("--width", type=int, default=1152)
    parser.add_argument("--height", type=int, default=1440)
    parser.add_argument("--quality", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--project-id", type=UUID)
    parser.add_argument("--job-id", type=UUID)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--attempt-db", type=Path)
    parser.add_argument("--account-budget-config", type=Path)
    parser.add_argument("--budget-usd", default="0")
    parser.add_argument("--reservation-usd", default="1")
    args = parser.parse_args(argv)
    if args.prepared and (args.prompt_file or args.product or args.reference or args.output):
        parser.error("--prepared cannot be combined with new preparation inputs.")
    if not args.prepared and not (args.prompt_file and args.product and args.output):
        parser.error("New preparation requires --prompt-file, --product and --output.")
    try:
        account_budget = None
        if args.execute:
            # Gate before preparation, credentials, local attempts or provider access.
            account_budget = load_account_budget(args.account_budget_config)
            if not all((args.project_id, args.job_id, args.env_file, args.attempt_db)):
                raise ValueError("FREEFORM_EXECUTION_ARGUMENTS_REQUIRED")
        prepared = args.prepared or prepare_freeform(products=args.product, references=args.reference,
            prompt_file=args.prompt_file, output=args.output, project_id=args.project_id, job_id=args.job_id,
            prompt_version=args.prompt_version, language=args.language, width=args.width,
            height=args.height, quality=args.quality, model=args.model)
        if not args.execute:
            saved = _read_json(prepared)
            print(json.dumps({"status": "PREPARED_NOT_RUN", "prepared": str(prepared),
                "preview": str(prepared.parent / "index.html"), "project_id": saved["project_id"],
                "job_id": saved["job_id"], "image_api_calls": 0}, ensure_ascii=False))
            return 0
        report = execute_freeform(prepared, project_id=args.project_id, job_id=args.job_id,
            attempt_db=args.attempt_db, env_file=args.env_file, account_budget=account_budget,
            budget_usd=args.budget_usd, reservation_usd=args.reservation_usd, allow_live=True)
        print(json.dumps({"status": report["status"], "report": str(prepared.parent / "generation-result.json")}))
        return 0
    except Exception as error:
        code = getattr(error, "code", "") or (str(error) if type(error) is ValueError else "")
        if not re.fullmatch(r"(?:ACCOUNT_BUDGET|FREEFORM|EXPERIMENT|PROVIDER|ASSET)_[A-Z_]{1,70}", code):
            code = "FREEFORM_FAILED"
        print(json.dumps({"status": "STOPPED", "error_code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
