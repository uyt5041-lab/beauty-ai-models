"""Internal HTTP client for a separate prepare -> inspect -> approve -> render flow."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from beauty_ai.contracts import BackgroundRequest, CutoutRequest, VisualBrief, ImagePrompt
from beauty_ai.errors import GatewayError
from beauty_ai.image_gateway import HTTPImageGateway


async def run(args) -> dict:
    # Reject invalid local JSON before uploading or invoking a model.
    def read_json_input(path):
        with path.open("rb") as source:
            raw = source.read(32769)
        if len(raw) > 32768:
            raise ValueError("JSON input exceeds limit")
        return raw

    if args.command == "prepare":
        visual = VisualBrief.model_validate_json(read_json_input(args.visual))
    prompt = None
    if args.command in ("render", "select"):
        if args.prompt is not None:
            prompt = ImagePrompt.model_validate_json(read_json_input(args.prompt))
        if args.background_profile == "sdxl-inpaint" and prompt is None:
            raise ValueError("SDXL requires an explicit image prompt")
    token = os.environ.get("INFERENCE_INTERNAL_TOKEN", "")
    if not token:
        raise ValueError("Set INFERENCE_INTERNAL_TOKEN")
    async with httpx.AsyncClient(base_url=os.environ.get("INFERENCE_BASE_URL", "http://127.0.0.1:8001"),
                                 headers={"Authorization": "Bearer " + token}, timeout=30) as client:
        async def json_response(method, url, **kwargs):
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        gateway = HTTPImageGateway(client)
        if args.command == "prepare":
            with args.input.open("rb") as file:
                data = file.read(20 * 1024 * 1024 + 1)
            if len(data) > 20 * 1024 * 1024:
                raise ValueError("Input exceeds 20 MiB")
            source = await json_response("POST", "/internal/v1/assets", content=data)
            if args.cutout_profile == "birefnet-lite":
                from beauty_ai.model_store import MODEL_REVISION
                revision = MODEL_REVISION
            else:
                revision = "1"
            request = CutoutRequest(source=source, pipeline_profile_id=args.cutout_profile, pipeline_revision=revision)
            job_id = uuid4()
            print(json.dumps({"job_id": str(job_id)}), flush=True)
            await gateway.submit(request, job_id=job_id)
            job = await gateway.wait(job_id, expected_request=request)
            if job.status != "succeeded":
                return job.model_dump(mode="json")
            return await json_response("POST", "/internal/v1/products/prepare", json={
                "source": source, "cutout": job.result.product_rgba.model_dump(mode="json"),
                "visual": visual.model_dump(mode="json"),
            })
        if args.command == "approve":
            return await json_response("PUT", f"/internal/v1/products/{args.candidate_id}/approval",
                                       json={"candidate_hash": args.candidate_hash, "confirmed": True})
        if args.command in ("render", "select"):
            candidate = await json_response("GET", f"/internal/v1/products/{args.candidate_id}")
            request = BackgroundRequest(source=candidate["source"], product_rgba=candidate["product_rgba"],
                preserve_mask=candidate["preserve_mask"], visual=candidate["visual"], seed=args.seed,
                pipeline_profile_id=args.background_profile,
                pipeline_revision="sdxl-base-fp16-v1" if args.background_profile == "sdxl-inpaint" else "1",
                image_prompt=prompt)
            if args.command == "select":
                return await json_response("PUT", f"/internal/v1/edits/{args.campaign_id}", json={
                    "expected_revision": args.expected_revision, "copy_revision": args.copy_revision,
                    "request": request.model_dump(mode="json"),
                })
            # Keep the printed ID to resolve an uncertain submission without regenerating.
            job_id = args.job_id
            print(json.dumps({"job_id": str(job_id)}), flush=True)
            await gateway.submit(request, job_id=job_id)
            return (await gateway.wait(job_id, expected_request=request)).model_dump(mode="json")
        if args.command == "get-job":
            return await json_response("GET", f"/internal/v1/jobs/{args.job_id}")
        if args.command == "get-timing":
            return await json_response("GET", f"/internal/v1/jobs/{args.job_id}/timing")
        if args.command == "get-edit":
            return await json_response("GET", f"/internal/v1/edits/{args.campaign_id}")
        if args.command == "attach":
            return await json_response("PUT", f"/internal/v1/edits/{args.campaign_id}/result", json={
                "expected_revision": args.expected_revision, "job_id": str(args.job_id),
            })
        if args.command == "download":
            response = await client.get(f"/internal/v1/assets/{args.asset_id}")
            response.raise_for_status()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("xb") as file:
                file.write(response.content)
            return {"saved": str(args.output)}
    raise ValueError("Unknown command")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Prepare a product candidate using a configured cutout profile")
    prepare.add_argument("input", type=Path)
    prepare.add_argument("visual", type=Path)
    prepare.add_argument("--cutout-profile", choices=("uploaded-alpha", "birefnet-lite"), default="uploaded-alpha")
    approve = commands.add_parser("approve", help="Run only after a person inspected the candidate")
    approve.add_argument("candidate_id", type=UUID)
    approve.add_argument("candidate_hash")
    render = commands.add_parser("render")
    render.add_argument("candidate_id", type=UUID)
    render.add_argument("--job-id", type=UUID, required=True, help="Keep and reuse on uncertain submission")
    render.add_argument("--seed", type=int, default=42)
    get_job = commands.add_parser("get-job")
    get_job.add_argument("job_id", type=UUID)
    get_timing = commands.add_parser("get-timing", help="Server receipt-to-terminal wall time; not GPU time")
    get_timing.add_argument("job_id", type=UUID)
    select = commands.add_parser("select", help="Compare-and-set the current image input; reuse a valid cached result")
    select.add_argument("candidate_id", type=UUID)
    select.add_argument("campaign_id", type=UUID)
    select.add_argument("--expected-revision", type=int, required=True)
    select.add_argument("--copy-revision", type=int, required=True)
    select.add_argument("--seed", type=int, default=42)
    for command in (render, select):
        command.add_argument("--background-profile", choices=("bright-template", "sdxl-inpaint"), default="bright-template")
        command.add_argument("--prompt", type=Path, help="Explicit ImagePrompt JSON; required for SDXL")
    get_edit = commands.add_parser("get-edit")
    get_edit.add_argument("campaign_id", type=UUID)
    attach = commands.add_parser("attach", help="Attach a finished job only if the edit is still current")
    attach.add_argument("campaign_id", type=UUID)
    attach.add_argument("job_id", type=UUID)
    attach.add_argument("--expected-revision", type=int, required=True)
    download = commands.add_parser("download")
    download.add_argument("asset_id", type=UUID)
    download.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except GatewayError as exc:
        print(json.dumps({"error_code": exc.code}), file=sys.stderr)
        return 2
    except (OSError, ValueError, httpx.HTTPError):
        print(json.dumps({"error_code": "IMAGE_CLI_INPUT_OR_REQUEST_FAILED"}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
