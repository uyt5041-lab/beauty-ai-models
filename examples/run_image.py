"""Internal HTTP client for a separate prepare -> inspect -> approve -> render flow."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from beauty_ai.contracts import BackgroundRequest, CutoutRequest, VisualBrief
from beauty_ai.image_gateway import HTTPImageGateway


async def run(args) -> dict:
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
            request = CutoutRequest(source=source, pipeline_profile_id="uploaded-alpha", pipeline_revision="1")
            job_id = uuid4()
            print(json.dumps({"job_id": str(job_id)}), flush=True)
            await gateway.submit(request, job_id=job_id)
            job = await gateway.wait(job_id, expected_request=request)
            if job.status != "succeeded":
                return job.model_dump(mode="json")
            visual = VisualBrief.model_validate_json(args.visual.read_text())
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
                pipeline_profile_id="bright-template", pipeline_revision="1")
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
    prepare = commands.add_parser("prepare", help="Existing transparent RGBA only; no model segmentation")
    prepare.add_argument("input", type=Path)
    prepare.add_argument("visual", type=Path)
    approve = commands.add_parser("approve", help="Run only after a person inspected the candidate")
    approve.add_argument("candidate_id", type=UUID)
    approve.add_argument("candidate_hash")
    render = commands.add_parser("render")
    render.add_argument("candidate_id", type=UUID)
    render.add_argument("--job-id", type=UUID, required=True, help="Keep and reuse on uncertain submission")
    render.add_argument("--seed", type=int, default=42)
    get_job = commands.add_parser("get-job")
    get_job.add_argument("job_id", type=UUID)
    select = commands.add_parser("select", help="Compare-and-set the current image input; reuse a valid cached result")
    select.add_argument("candidate_id", type=UUID)
    select.add_argument("campaign_id", type=UUID)
    select.add_argument("--expected-revision", type=int, required=True)
    select.add_argument("--copy-revision", type=int, required=True)
    select.add_argument("--seed", type=int, default=42)
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
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
