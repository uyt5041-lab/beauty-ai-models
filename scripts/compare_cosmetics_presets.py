"""Explicitly authorized three-image comparison; no automatic provider retries.

Requires the test and observability extras. Without --execute no files or calls
are made. --publish-prompts creates new production versions (use intentionally).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def settings_from_file(path: Path) -> dict[str, str]:
    result = {}
    allowed = {"OPENAI_API_KEY", "INFERENCE_INTERNAL_TOKEN", "LANGFUSE_PUBLIC_KEY",
               "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_HOST"}
    for line in path.read_text().splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
        if match and match[1] in allowed:
            words = shlex.split(match[2], comments=True)
            if len(words) == 1:
                result[match[1]] = words[0]
    return result


def langfuse_cli(arguments: list[str], settings: dict[str, str]) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k in {"PATH", "HOME", "TMPDIR", "SYSTEMROOT", "LANG"}}
    env.update({k: v for k, v in settings.items() if k.startswith("LANGFUSE_")})
    env["LANGFUSE_HOST"] = settings.get("LANGFUSE_BASE_URL", settings.get("LANGFUSE_HOST", ""))
    try:
        result = subprocess.run(["npx", "--yes", "langfuse-cli@latest", "api", *arguments, "--json"],
                                env=env, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("LANGFUSE_CLI_FAILED") from None
    # Never include CLI stderr/body in an exception: these can contain credentials.
    try:
        envelope = json.loads(result.stdout)
        allowed_status = {200, 201} if arguments[:2] == ["prompts", "create"] else {200}
        if result.returncode or envelope.get("status") not in allowed_status:
            raise ValueError
        body = envelope["body"]
        if not isinstance(body, dict):
            raise ValueError
        return body
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("LANGFUSE_CLI_FAILED") from None


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def finish_trace(trace: object | None, **values: object) -> dict:
    """Tracing must never trigger another image request or erase its success."""
    if trace is None:
        return {"status": "BEGIN_FAILED"}
    try:
        return trace.finish(**values)
    except Exception as error:
        return {"status": "FINISH_FAILED", "error_type": type(error).__name__}


def run(args: argparse.Namespace) -> Path:
    if not args.execute:
        raise RuntimeError("LIVE_EXECUTION_NOT_AUTHORIZED")
    import httpx
    from fastapi.testclient import TestClient
    from PIL import Image
    from beauty_ai.contracts import AssetRef, ImageProviderUsage, content_hash
    from beauty_ai.cosmetics_prompts import PRESETS, CosmeticsPromptBrief, build_cosmetics_request
    from beauty_ai.inference_runtime import build_app
    from beauty_ai.langfuse_prompts import (cosmetics_prompt_definition, cosmetics_prompt_variables,
                                           pin_cosmetics_prompt, begin_image_generation)
    from beauty_ai.openai_images import OpenAIImagesConfig

    settings = settings_from_file(args.env_file)
    for key in ("OPENAI_API_KEY", "INFERENCE_INTERNAL_TOKEN", "LANGFUSE_PUBLIC_KEY",
                "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        if not settings.get(key):
            raise RuntimeError("MISSING_REQUIRED_LOCAL_SETTING")
    source_bytes = args.source.read_bytes()
    with Image.open(args.source) as source_image:
        source_image.verify()
    os.environ.update({k: v for k, v in settings.items() if k.startswith("LANGFUSE_")})
    os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = "beautyai-preset-comparison"
    run_dir = args.output.resolve() / ("run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                      + "-" + uuid4().hex[:12])
    run_dir.mkdir(parents=True, exist_ok=False)
    report = {"mode": "LIVE_USER_AUTHORIZED", "run_id": run_dir.name,
              "started_at": datetime.now(timezone.utc).isoformat(), "operations": [],
              "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
              "quality": "medium", "size": [1152, 1440], "layout": "product_right",
              "model": "gpt-image-2.5-sunburst-2026-09-08", "http_post_attempts": 0,
              "max_provider_posts": 3, "quality_approval": "NOT_ASSESSED",
              "actual_cost_usd": None, "billing_status": "UNRECONCILED",
              "pricing_source": "https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst"}
    def save():
        write_json(run_dir / "report.json", report)
    source_path = run_dir / "source.png"
    source_path.write_bytes(source_bytes)
    original_transport = httpx.HTTPTransport.handle_request
    def observed_transport(transport, request):
        if request.url.host == "api.openai.com" and request.method == "POST":
            if report["http_post_attempts"] >= 3:
                raise RuntimeError("PROVIDER_CALL_LIMIT")
            report["http_post_attempts"] += 1
            save()
        return original_transport(transport, request)
    save()
    print(json.dumps({"event": "started", "run_dir": str(run_dir)}), flush=True)
    httpx.HTTPTransport.handle_request = observed_transport
    try:
        app = build_app(run_dir / "runtime", uuid4(), settings["INFERENCE_INTERNAL_TOKEN"],
                        openai_config=OpenAIImagesConfig(live_calls=True, api_key=settings["OPENAI_API_KEY"],
                                                        timeout_seconds=300),
                        image_budget_usd="3", image_reservation_usd="1")
        with TestClient(app, headers={"Authorization": "Bearer " + settings["INFERENCE_INTERNAL_TOKEN"]}) as client:
            uploaded = client.post("/internal/v1/assets", content=source_bytes)
            if uploaded.status_code != 201:
                raise RuntimeError("SOURCE_UPLOAD_FAILED")
            source_ref = AssetRef.model_validate(uploaded.json())
            pending = []
            # Resolve every prompt before crossing the paid API boundary.
            for preset in PRESETS:
                folder = run_dir / preset.id
                folder.mkdir()
                definition = cosmetics_prompt_definition(preset.id)
                write_json(folder / "prompt-definition.json", definition)
                if args.publish_prompts:
                    created = langfuse_cli(["prompts", "create", "--body-file", str(folder / "prompt-definition.json")], settings)
                    remote = langfuse_cli(["prompts", "get", definition["name"], "--version", str(created["version"])], settings)
                else:
                    remote = langfuse_cli(["prompts", "get", definition["name"], "--label", "production"], settings)
                brief = CosmeticsPromptBrief(product_description=args.product_description, preset_id=preset.id,
                                              layout_id="product_right", source_revision=1)
                request = build_cosmetics_request(brief, product_images=[source_ref], quality="medium")
                request = pin_cosmetics_prompt(request, remote, cosmetics_prompt_variables(brief, request))
                write_json(folder / "request.json", request.model_dump(mode="json"))
                write_json(folder / "remote-prompt.json", remote)
                (folder / "provider-prompt.txt").write_text(request.managed_prompt.rendered_text)
                pending.append((preset, folder, request))
            for preset, folder, request in pending:
                job_id = uuid4()
                start = datetime.now(timezone.utc)
                entry = {"preset_id": preset.id, "title": preset.title, "job_id": str(job_id),
                         "request_hash": content_hash(request), "started_at": start.isoformat(),
                         "prompt_name": request.managed_prompt.name, "prompt_version": request.managed_prompt.version,
                         "prompt_sha256": request.managed_prompt.sha256}
                report["operations"].append(entry)
                save()
                try:
                    trace = begin_image_generation(request, run_id=report["run_id"], job_id=str(job_id),
                                                   request_hash=entry["request_hash"])
                except Exception:
                    trace = None
                submitted = client.put(f"/internal/v1/jobs/{job_id}", json=request.model_dump(mode="json"))
                if submitted.status_code != 202:
                    entry["status"] = "REJECTED_BEFORE_QUEUE"
                    entry["langfuse"] = finish_trace(trace, error_code="REJECTED_BEFORE_QUEUE")
                    save()
                    break
                print(json.dumps({"event": "submitted", "preset": preset.id}), flush=True)
                deadline = time.monotonic() + 330
                while time.monotonic() < deadline:
                    response = client.get(f"/internal/v1/jobs/{job_id}")
                    if response.status_code != 200:
                        raise RuntimeError("INTERNAL_JOB_READ_FAILED")
                    job = response.json()
                    if job["status"] not in {"queued", "running"}:
                        break
                    time.sleep(1)
                else:
                    entry["status"] = "WAIT_ENDED_JOB_MAY_CONTINUE"
                    entry["langfuse"] = finish_trace(trace, error_code="WAIT_ENDED_JOB_MAY_CONTINUE")
                    save()
                    break
                end = datetime.now(timezone.utc)
                receipt_response = client.get(f"/internal/v1/jobs/{job_id}/provider")
                receipt = receipt_response.json() if receipt_response.status_code == 200 else {}
                entry.update(status=job["status"], finished_at=end.isoformat(),
                             elapsed_seconds=round((end-start).total_seconds(), 3), provider_receipt=receipt)
                usage = ImageProviderUsage.model_validate(receipt["usage"]) if receipt.get("usage") else None
                estimate = None
                if usage and all(getattr(usage, k) is not None for k in ("input_text_tokens", "input_image_tokens", "output_tokens")):
                    estimate = round((usage.input_text_tokens * 5 + usage.input_image_tokens * 8 + usage.output_tokens * 30) / 1_000_000, 8)
                entry["estimated_cost_usd"] = estimate
                output_sha = None
                if job["status"] == "succeeded":
                    result_ref = AssetRef.model_validate(job["result"]["image"])
                    downloaded = client.get(f"/internal/v1/assets/{result_ref.asset_id}")
                    if downloaded.status_code != 200:
                        raise RuntimeError("INTERNAL_ASSET_DOWNLOAD_FAILED")
                    output_sha = hashlib.sha256(downloaded.content).hexdigest()
                    if output_sha != result_ref.sha256:
                        raise RuntimeError("OUTPUT_HASH_MISMATCH")
                    (folder / "image.png").write_bytes(downloaded.content)
                    entry["output_sha256"] = output_sha
                    entry["image_path"] = str(folder / "image.png")
                save()  # Successful image is durable before observability work.
                entry["langfuse"] = finish_trace(trace, output_sha256=output_sha, usage=usage,
                    provider_request_id=receipt.get("provider_request_id"),
                    estimated_cost_usd=estimate, error_code=job.get("error_code"))
                save()
                print(json.dumps({"event": "completed", "preset": preset.id, "status": entry["status"],
                                  "elapsed_seconds": entry["elapsed_seconds"], "langfuse": entry["langfuse"]}), flush=True)
                if job["status"] != "succeeded":
                    break
        report["status"] = "SUCCEEDED" if len(report["operations"]) == 3 and all(x.get("status") == "succeeded" for x in report["operations"]) else "INCOMPLETE"
    except Exception as error:
        report["status"] = "RUNNER_ERROR"
        report["runner_error_type"] = type(error).__name__
        # Deliberately omit arbitrary exception strings and HTTP bodies.
    finally:
        httpx.HTTPTransport.handle_request = original_transport
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
        print(json.dumps({"event": "finished", "status": report.get("status"), "posts": report["http_post_attempts"],
                          "report": str(run_dir / "report.json")}), flush=True)
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Explicit approval for exactly three paid image edits")
    parser.add_argument("--publish-prompts", action="store_true", help="Create new production Langfuse prompt versions")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs/cosmetics-comparison"))
    parser.add_argument("--product-description", default="One fictional unbranded lavender skincare serum bottle with a coral pump and a blank cream label. No efficacy claims.")
    args = parser.parse_args()
    if not args.execute:
        parser.error("No calls made. Add --execute only after authorizing three live image edits.")
    try:
        run_dir = run(args)
        if json.loads((run_dir / "report.json").read_text()).get("status") != "SUCCEEDED":
            raise SystemExit(1)
    except Exception as error:
        print(json.dumps({"status": "PREPARATION_FAILED", "error_type": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
