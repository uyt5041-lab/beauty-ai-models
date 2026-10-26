"""Prepare and inspect an immutable cosmetics experiment before explicit execution."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
from urllib.parse import quote
from uuid import UUID, uuid4

from .assets import LocalAssetStore
from .contracts import ExternalImageRequest, content_hash
from .cosmetics_prompts import PRESETS, PROMPT_VERSION, render_cosmetics_provider_prompt
from .experiment_inputs import (_absolute_without_links, prepare_inputs,
                                MAX_PROVIDER_IMAGE_BYTES, MAX_PROVIDER_TOTAL_BYTES)
from .experiment_plan import DEFAULT_MODEL, build_cases, input_refs

SCHEMA = "beautyai-experiment-v1"


def write_json(path: Path, value: object) -> None:
    """Atomically write an artifact without embedding credentials in error text."""
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("EXPERIMENT_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _read_json(path: Path) -> dict:
    source = _absolute_without_links(path)
    if not source.is_file() or source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("EXPERIMENT_JSON_UNAVAILABLE")
    with source.open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("EXPERIMENT_JSON_TOO_LARGE")
    def invalid_constant(_):
        raise ValueError("EXPERIMENT_JSON_INVALID")
    parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=invalid_constant)
    if type(parsed) is not dict:
        raise ValueError("EXPERIMENT_JSON_INVALID")
    return parsed


def _limits(max_calls: int, planned_calls: int) -> None:
    if (type(max_calls) is not int or not 1 <= max_calls <= 30
            or type(planned_calls) is not int or not 1 <= planned_calls <= max_calls):
        raise ValueError("EXPERIMENT_PLANNED_CALLS_EXCEED_LIMIT_OR_INVALID")


def _preview(plan: dict, digest: str) -> str:
    pictures = []
    for item in plan["inputs"]:
        label = "제품" if item["role"] == "product" else "참고"
        ref = item["asset_ref"]
        pictures.append(f'<figure><img src="{quote(item["path"])}" alt="{label} 사진 {item["index"]}">'
                        f'<figcaption><strong>{label} {item["index"]}</strong> · {escape(item["name"])}'
                        f'<br>{ref["width"]} × {ref["height"]} · PNG</figcaption></figure>')
    cases = []
    for case in plan["cases"]:
        request = case["request"]
        source = "로컬 템플릿" if case["prompt_source"] == "local" else "저장한 Langfuse 스냅샷"
        cases.append(f'<article><h3>{escape(case["case_id"])} · {escape(case["title"])}</h3>'
                     f'<p>{source} · 버전 {escape(case["prompt_version"])} · 반복 {case["repeat_index"]}</p>'
                     f'<p>{escape(request["model"])} · {request["width"]} × {request["height"]}'
                     f' · {escape(request["quality"])} · 편집 1회</p>'
                     f'<details open><summary>제공자에게 보낼 최종 프롬프트</summary>'
                     f'<pre>{escape(case["provider_prompt"])}</pre></details>'
                     f'<p class="hash">프롬프트 SHA-256: {case["provider_prompt_sha256"]}</p></article>')
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>BeautyAI 실험 호출 전 미리보기</title><style>
body{{font:16px/1.65 system-ui,sans-serif;margin:0;background:#f5f7ff;color:#17233f}}
main{{max-width:1120px;margin:auto;padding:32px 20px}}h1{{margin:0 0 12px;font-size:32px}}
.tag{{color:#4338ca;font-weight:700}}.metrics{{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}}
.metric,article,.notice{{background:white;border:1px solid #dce1f5;border-radius:16px;padding:20px}}
.metric{{flex:1;min-width:160px}}.metric b{{display:block;font-size:30px;color:#4338ca}}
.notice{{border-left:6px solid #ffb426}}.photos{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px}}
figure{{margin:0;background:white;border:1px solid #dce1f5;border-radius:16px;overflow:hidden}}
img{{width:100%;height:300px;object-fit:contain;background:#e8ebf5}}figcaption{{padding:12px;overflow-wrap:anywhere}}
article{{margin:16px 0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5fb;padding:16px;border-radius:8px;font:14px/1.65 ui-monospace,monospace}}
.hash{{font-size:12px;overflow-wrap:anywhere;color:#59657e}}a{{color:#4338ca}}
</style><main><p class="tag">BeautyAI · 호출 전 미리보기</p><h1>입력과 프롬프트를 먼저 확인하세요.</h1>
<p>이 화면은 준비 시점의 기록입니다. 이미지 API를 호출하지 않았습니다. 실행 후 결과는 별도 report.json에 남습니다.</p>
<div class="metrics"><div class="metric">계획한 호출<b>{plan["planned_calls"]}회</b></div>
<div class="metric">허용한 최대 시도<b>{plan["max_calls"]}회</b></div>
<div class="metric">미리보기의 실제 호출<b>0회</b></div></div>
<div class="notice"><strong>제품 확인과 광고 승인은 아직 하지 않았습니다.</strong><br>
첫 제품 사진이 주 시점입니다. 나머지 사진이 같은 제품인지 확인하세요. 참고 사진은 배경 구도와 조명에만 씁니다.
제품명·색·재질·라벨 설명을 확인하세요. 실제 청구액과 팀 잔액은 미확인입니다.
시도 횟수 제한은 달러 비용 상한을 보장하지 않습니다. 실패·미확인 결과 뒤에는 다음 시도를 멈춥니다.</div>
<h2>입력 사진</h2><div class="photos">{''.join(pictures)}</div>
<h2>선택한 실험</h2>{''.join(cases)}
<h2>고정 계획</h2><p><a href="plan.json">계획 JSON 보기</a> · 검수 상태: NOT_APPROVED · 청구 상태: UNRECONCILED</p>
<p class="hash">계획 SHA-256: {digest}</p><p>실행 시 이 해시를 지정합니다. 변경한 계획이나 이미지는 다시 준비해야 합니다.</p>
</main></html>'''


def prepare_experiment(*, products: list[Path], references: list[Path], output: Path,
                       product_description: str, presets: list[str], prompt_version: str,
                       prompt_snapshots: dict | None = None, repeats: int = 1,
                       max_calls: int = 1, quality: str = "medium",
                       layout_id: str = "product_right", model: str = DEFAULT_MODEL) -> Path:
    """Write a new, offline preview. Never read credentials or contact providers."""
    if type(presets) is not list or type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("EXPERIMENT_SELECTION_INVALID")
    _limits(max_calls, len(presets) * repeats)
    project = uuid4()
    records = prepare_inputs(products, references, output, project)
    root = _absolute_without_links(output)
    cases = build_cases(records, product_description=product_description, presets=presets,
                        prompt_version=prompt_version, prompt_snapshots=prompt_snapshots,
                        repeats=repeats, quality=quality, layout_id=layout_id, model=model)
    plan = {"schema_version": SCHEMA, "run_id": "run-" + uuid4().hex,
            "project_id": str(project), "created_at": datetime.now(timezone.utc).isoformat(),
            "max_calls": max_calls, "planned_calls": len(cases), "inputs": records, "cases": cases,
            "review_status": "NOT_APPROVED", "billing_status": "UNRECONCILED", "actual_cost_usd": None}
    digest = content_hash(plan)
    for case in cases:
        folder = root / "cases" / case["case_id"]
        folder.mkdir(parents=True)
        write_json(folder / "request.json", case["request"])
        (folder / "provider-prompt.txt").write_text(case["provider_prompt"], encoding="utf-8")
    if prompt_snapshots is not None:
        # Keep only prompt identity and template, never arbitrary remote config.
        safe_snapshots = {preset: {key: snapshot[key] for key in ("name", "type", "version", "prompt")}
                          for preset, snapshot in prompt_snapshots.items()}
        write_json(root / "prompt-snapshots.json", safe_snapshots)
    write_json(root / "plan.json", {"plan": plan, "sha256": digest})
    (root / "index.html").write_text(_preview(plan, digest), encoding="utf-8")
    return root / "plan.json"


def load_verified_plan(plan_path: Path, expected_sha256: str | None = None, *,
                       verify_runtime_assets: bool = True) -> tuple[Path, dict, str]:
    """Read only frozen inputs; current originals or production labels are unused."""
    try:
        path = _absolute_without_links(plan_path)
        root = path.parent
        if path.name != "plan.json" or not any(parent.name == "outputs" for parent in root.parents):
            raise ValueError
        envelope = _read_json(path)
        if set(envelope) != {"plan", "sha256"} or type(envelope["plan"]) is not dict:
            raise ValueError
        plan, digest = envelope["plan"], envelope["sha256"]
        if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest) or content_hash(plan) != digest:
            raise ValueError
        if expected_sha256 is not None and expected_sha256 != digest:
            raise ValueError
        if (plan["schema_version"] != SCHEMA or plan["review_status"] != "NOT_APPROVED"
                or plan["billing_status"] != "UNRECONCILED" or plan["actual_cost_usd"] is not None):
            raise ValueError
        project = UUID(plan["project_id"])
        if str(project) != plan["project_id"] or not re.fullmatch(r"run-[a-f0-9]{32}", plan["run_id"]):
            raise ValueError
        if datetime.fromisoformat(plan["created_at"]).tzinfo is None:
            raise ValueError
        cases = plan["cases"]
        _limits(plan["max_calls"], plan["planned_calls"])
        if type(cases) is not list or len(cases) != plan["planned_calls"]:
            raise ValueError
        products, references = input_refs(plan["inputs"])
        store = None
        if verify_runtime_assets:
            store_path = _absolute_without_links(root / "runtime/assets")
            if not (store_path / "assets.sqlite3").is_file():
                raise ValueError
            store = LocalAssetStore(store_path)
        total = 0
        for item, ref in zip(plan["inputs"], products + references, strict=True):
            expected_path = f'inputs/{item["role"]}-{item["index"]:02d}.png'
            if item["path"] != expected_path or type(item["name"]) is not str:
                raise ValueError
            image_path = _absolute_without_links(root / expected_path)
            if not image_path.is_file() or not 0 < image_path.stat().st_size <= MAX_PROVIDER_IMAGE_BYTES:
                raise ValueError
            with image_path.open("rb") as stream:
                data = stream.read(MAX_PROVIDER_IMAGE_BYTES + 1)
            total += len(data)
            if (len(data) > MAX_PROVIDER_IMAGE_BYTES or total > MAX_PROVIDER_TOTAL_BYTES
                    or hashlib.sha256(data).hexdigest() != ref.sha256
                    or (store is not None and store.read_bytes(ref, project_id=project) != data)):
                raise ValueError
        jobs = set()
        remote_presets = {case["preset_id"] for case in cases if case.get("prompt_source") == "langfuse"}
        snapshots = _read_json(root / "prompt-snapshots.json") if remote_presets else {}
        if set(snapshots) != remote_presets:
            raise ValueError
        for index, case in enumerate(cases, 1):
            if (case["case_id"] != f"case-{index:03d}" or case["job_id"] in jobs
                    or str(UUID(case["job_id"])) != case["job_id"]):
                raise ValueError
            jobs.add(case["job_id"])
            request = ExternalImageRequest.model_validate(case["request"])
            if (request.operation != "edit" or request.pipeline_revision != "2"
                    or request.product_images != products or request.reference_images != references
                    or case["request_sha256"] != content_hash(request)
                    or case["request_sha256"] != content_hash(case["request"])):
                raise ValueError
            text = request.managed_prompt.rendered_text if request.managed_prompt else render_cosmetics_provider_prompt(request)
            if (case["provider_prompt"] != text or hashlib.sha256(text.encode()).hexdigest() != case["provider_prompt_sha256"]
                    or type(case["repeat_index"]) is not int or not 1 <= case["repeat_index"] <= 10
                    or not re.fullmatch(r"[0-9a-f]{64}", case["template_sha256"])):
                raise ValueError
            parts = request.prompt.prompt_version.split("/")
            if len(parts) != 3 or parts[0] != PROMPT_VERSION or parts[1] != case["preset_id"]:
                raise ValueError
            if request.managed_prompt:
                if case["prompt_source"] != "langfuse" or case["prompt_version"] != str(request.managed_prompt.version):
                    raise ValueError
                remote = snapshots[case["preset_id"]]
                if (remote["type"] != "text" or remote["name"] != request.managed_prompt.name
                        or type(remote["version"]) is not int or remote["version"] != request.managed_prompt.version
                        or hashlib.sha256(remote["prompt"].encode()).hexdigest() != case["template_sha256"]):
                    raise ValueError
            elif case["prompt_source"] != "local" or case["prompt_version"] != PROMPT_VERSION:
                raise ValueError
            else:
                from .langfuse_prompts import cosmetics_prompt_definition
                template = cosmetics_prompt_definition(case["preset_id"])["prompt"]
                if hashlib.sha256(template.encode()).hexdigest() != case["template_sha256"]:
                    raise ValueError
            folder = root / "cases" / case["case_id"]
            if _read_json(folder / "request.json") != case["request"]:
                raise ValueError
            prompt_path = _absolute_without_links(folder / "provider-prompt.txt")
            if prompt_path.stat().st_size > 128_000 or prompt_path.read_text(encoding="utf-8") != text:
                raise ValueError
        return root, plan, digest
    except Exception as error:
        # Never echo a malformed file, provider body, path or a secret-like value.
        raise ValueError("EXPERIMENT_PLAN_OR_ASSET_INTEGRITY_INVALID") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="유료 호출 없이 JSON/HTML 미리보기 저장")
    prepare.add_argument("--product", "--source", dest="products", type=Path, action="append", required=True)
    prepare.add_argument("--reference", dest="references", type=Path, action="append", default=[])
    prepare.add_argument("--preset", dest="presets", choices=[p.id for p in PRESETS], action="append", required=True)
    prepare.add_argument("--prompt-version", required=True)
    prepare.add_argument("--prompt-snapshot", type=Path)
    prepare.add_argument("--product-description", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--repeats", type=int, default=1)
    prepare.add_argument("--max-calls", type=int, default=1)
    prepare.add_argument("--quality", choices=["low", "medium", "high"], default="medium")
    prepare.add_argument("--layout", dest="layout_id", choices=["product_right", "product_left", "product_center_bottom"], default="product_right")
    prepare.add_argument("--model", default=DEFAULT_MODEL)
    execute = commands.add_parser("execute", help="검토한 고정 계획만 명시적으로 실행")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--expect-sha256", required=True)
    execute.add_argument("--env-file", type=Path, required=True)
    execute.add_argument("--budget-usd", required=True)
    execute.add_argument("--reservation-usd", default="1")
    execute.add_argument("--execute", action="store_true")
    execute.add_argument("--wandb", action="store_true", help="로컬 결과 저장 후 W&B에 자동 등록")
    execute.add_argument("--wandb-entity")
    execute.add_argument("--wandb-project", default="beautyai-image-experiments")
    sync = commands.add_parser("sync-wandb", help="저장된 결과만 W&B 등록. 이미지 API 호출 없음")
    sync.add_argument("--plan", type=Path, required=True)
    sync.add_argument("--expect-sha256", required=True)
    sync.add_argument("--entity", required=True)
    sync.add_argument("--project", default="beautyai-image-experiments")
    sync.add_argument("--prepare-only", action="store_true", help="원격 업로드 없이 패키지 검토")
    args = parser.parse_args(argv)
    if args.command == "execute" and not args.execute:
        parser.error("호출하지 않았습니다. 검토한 계획을 실행할 때만 --execute를 지정하세요.")
    if args.command == "execute" and (args.wandb != bool(args.wandb_entity)):
        parser.error("W&B 기록은 --wandb와 --wandb-entity를 함께 지정하세요.")
    try:
        if args.command == "sync-wandb":
            from .experiment_wandb import sync_trial
            receipt = sync_trial(args.plan, expected_sha256=args.expect_sha256, entity=args.entity,
                project=args.project, publish=not args.prepare_only)
            print(json.dumps({key: receipt.get(key) for key in
                ("status", "record_count", "readback_verified", "overview_url", "new_image_model_calls")}, ensure_ascii=False))
            return 1 if receipt["status"] == "INCOMPLETE" else 0
        if args.command == "prepare":
            snapshots = _read_json(args.prompt_snapshot) if args.prompt_snapshot else None
            path = prepare_experiment(products=args.products, references=args.references, output=args.output,
                product_description=args.product_description, presets=args.presets, prompt_version=args.prompt_version,
                prompt_snapshots=snapshots, repeats=args.repeats, max_calls=args.max_calls, quality=args.quality,
                layout_id=args.layout_id, model=args.model)
            _, plan, digest = load_verified_plan(path)
            print(json.dumps({"status": "PREPARED", "plan": str(path), "preview": str(path.parent / "index.html"),
                "sha256": digest, "planned_calls": plan["planned_calls"], "max_calls": plan["max_calls"],
                "image_api_calls": 0}, ensure_ascii=False))
            return 0
        from .experiment_execution import execute_plan
        report = execute_plan(args.plan, expected_sha256=args.expect_sha256, env_file=args.env_file,
            budget_usd=args.budget_usd, reservation_usd=args.reservation_usd, allow_live=True,
            **({"wandb_entity": args.wandb_entity, "wandb_project": args.wandb_project} if args.wandb else {}))
        receipt = _read_json(args.plan.parent / "wandb-sync.json") if args.wandb else None
        print(json.dumps({"status": report["status"], "report": str(args.plan.parent / "report.json"),
            "wandb_status": receipt["status"] if receipt else "DISABLED"}, ensure_ascii=False))
        if receipt and receipt["status"] != "READBACK_VERIFIED":
            return 1
        return 0 if report["status"] == "SUCCEEDED" else 1
    except Exception as error:
        print(json.dumps({"status": "STOPPED", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
