"""Image work tracking retains durable receipts without repeating submissions."""
import base64
from io import BytesIO
import json
from uuid import UUID, uuid4

import httpx
from PIL import Image
import pytest

from beauty_ai import freeform_image
from beauty_ai.api_image_processor import ExternalImageProcessor
from beauty_ai.assets import LocalAssetStore
from beauty_ai.backend_adapter import BackendImageAdapter, BackendImageInput
from beauty_ai.contracts import ExternalImageRequest, ImagePrompt, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from beauty_ai.provider_attempts import ProviderAttemptStore
from beauty_ai.work_ledger import AdWorkLedger
from beauty_ai.work_tracking import track_ad_work


def png(size=(1024, 1024), color="coral"):
    out = BytesIO()
    with Image.new("RGB", size, color) as image:
        image.save(out, "PNG")
    return out.getvalue()


def response(size=(1024, 1024), *, valid=True):
    raw = png(size) if valid else b"invalid-image"
    return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(raw).decode()}],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}},
        headers={"x-request-id": "req-work-image"})


def request():
    return ExternalImageRequest(operation="generate", width=1024, height=1024,
        prompt=ImagePrompt(positive="제품 배경", prompt_version="work-v1", source_revision=1, language="ko"))


def setup(tmp_path, handler, *, tracked=True):
    ledger = AdWorkLedger(tmp_path / "works.sqlite3")
    project, work = uuid4(), uuid4()
    ledger.create_work(project, work, "product-1", required_stages=["image_generation"])
    processor = ExternalImageProcessor(LocalAssetStore(tmp_path / "assets"),
        OpenAIImagesAdapter(OpenAIImagesConfig(), transport=httpx.MockTransport(handler)),
        ProviderAttemptStore(tmp_path / "attempts.sqlite3"), budget_limit_usd="10",
        work_ledger=ledger if tracked else None)
    return processor, ledger, project, work


def forbid(*_args, **_kwargs):
    pytest.fail("Work ownership must be checked before assets, budget or SDK access")


def test_image_success_restart_and_closed_replay_keep_one_post_and_immutable_work_receipt(tmp_path):
    calls = []
    processor, ledger, project, work = setup(tmp_path, lambda req: (calls.append(req), response())[1])
    job, payload = uuid4(), request()
    original_hash = content_hash(payload)
    with track_ad_work(ledger, project, work):
        first = processor.process_job(payload, project, job)
    observed = ledger.export_dataset(project)
    assert len(observed["attempts"]) == len(observed["receipts"]) == 1
    receipt = observed["receipts"][0]
    assert receipt["usage"]["input_tokens"] == 10
    assert receipt["usage"]["output_tokens"] == 20
    assert receipt["artifact_sha256"] == first.image.sha256
    assert receipt["elapsed_seconds"] >= 0
    assert receipt["estimated_cost_usd"] is receipt["actual_cost_usd"] is None
    assert content_hash(payload) == original_hash
    reopened = AdWorkLedger(ledger.path)
    processor.work_ledger = reopened
    second = processor.process_job(payload, project, job)
    assert first == second
    assert reopened.export_dataset(project) == observed
    reopened.complete(project, work, {"artifact_sha256": first.image.sha256, "reviewer_ref": "reviewer-1",
        "quality_policy_version": "quality-v1", "quality_pass": True,
        "expected_attempt_ids": [receipt["attempt_id"]]})
    assert processor.process_job(payload, project, job) == first
    assert len(calls) == 1
    assert reopened.export_dataset(project)["receipts"] == observed["receipts"]


@pytest.mark.parametrize("failure,state,usage", [("output", "FAILED", 30), ("timeout", "UNKNOWN", None)])
def test_failed_unknown_image_receipts_are_captured_and_never_resubmitted(tmp_path, failure, state, usage):
    calls = []
    def handle(req):
        calls.append(req)
        if failure == "timeout":
            raise httpx.ReadTimeout("private response loss")
        return response(valid=False)
    processor, ledger, project, work = setup(tmp_path, handle)
    job = uuid4()
    with track_ad_work(ledger, project, work):
        with pytest.raises(GatewayError):
            processor.process_job(request(), project, job)
    first = ledger.export_dataset(project)
    receipt = first["receipts"][0]
    assert receipt["status"] == state
    assert (receipt["usage"]["total_tokens"] if receipt["usage"] else None) == usage
    with pytest.raises(GatewayError) as denied:
        processor.process_job(request(), project, job)
    assert denied.value.code == "PROVIDER_ATTEMPT_CONFLICT"
    assert ledger.export_dataset(project) == first
    assert len(calls) == 1


def test_active_scope_uses_its_ledger_when_processor_has_none(tmp_path):
    processor, ledger, project, work = setup(tmp_path, lambda _: response(), tracked=False)
    with track_ad_work(ledger, project, work):
        processor.process_job(request(), project, uuid4())
    assert len(ledger.export_dataset(project)["receipts"]) == 1


@pytest.mark.parametrize("conflict", ["path", "project", "bound-work"])
def test_scope_conflicts_fail_before_assets_budget_and_sdk(tmp_path, monkeypatch, conflict):
    processor, ledger, project, work = setup(tmp_path, forbid)
    job = uuid4()
    scope_ledger, scope_project, scope_work = ledger, project, work
    if conflict == "path":
        scope_ledger = AdWorkLedger(tmp_path / "other-works.sqlite3")
        scope_ledger.create_work(project, work, "product-1")
    elif conflict == "project":
        scope_project, scope_work = uuid4(), uuid4()
        ledger.create_work(scope_project, scope_work, "product-1")
    else:
        other = uuid4()
        ledger.create_work(project, other, "product-1")
        ledger.bind_image_job(project, other, job)
    monkeypatch.setattr(processor.assets, "read_bytes", forbid)
    monkeypatch.setattr(processor.assets, "put_upload", forbid)
    monkeypatch.setattr(processor.attempts, "begin", forbid)
    with track_ad_work(scope_ledger, scope_project, scope_work):
        with pytest.raises(GatewayError) as denied:
            processor.process_job(request(), project, job)
    assert denied.value.code == "WORK_SCOPE_CONFLICT"


def test_cancelled_bound_job_without_provider_success_cannot_start(tmp_path, monkeypatch):
    processor, ledger, project, work = setup(tmp_path, forbid)
    job = uuid4()
    ledger.bind_image_job(project, work, job)
    ledger.cancel(project, work)
    monkeypatch.setattr(processor.assets, "read_bytes", forbid)
    monkeypatch.setattr(processor.attempts, "begin", forbid)
    with pytest.raises(GatewayError) as denied:
        processor.process_job(request(), project, job)
    assert denied.value.code == "WORK_CLOSED"
    assert processor.attempts.get(job, project) is None


def test_work_capture_failure_preserves_provider_result_then_reuses_it_to_recover(tmp_path, monkeypatch):
    calls = []
    processor, ledger, project, work = setup(tmp_path, lambda req: (calls.append(req), response())[1])
    job, payload = uuid4(), request()
    original = ledger.sync_image_attempt
    def unavailable(*_args, **_kwargs):
        raise GatewayError("WORK_STORAGE_ERROR", "unavailable")
    monkeypatch.setattr(ledger, "sync_image_attempt", unavailable)
    with track_ad_work(ledger, project, work):
        with pytest.raises(GatewayError) as pending:
            processor.process_job(payload, project, job)
    assert pending.value.code == "WORK_CAPTURE_PENDING"
    provider_record = processor.attempts.get(job, project)
    assert provider_record["state"] == "succeeded"
    assert not ledger.export_dataset(project)["receipts"]
    monkeypatch.setattr(ledger, "sync_image_attempt", original)
    result = processor.process_job(payload, project, job)
    assert str(result.attempt_id) == provider_record["attempt_id"]
    assert len(ledger.export_dataset(project)["receipts"]) == 1
    assert len(calls) == 1


def test_cancellation_during_existing_provider_call_preserves_late_receipt_and_replays_once(tmp_path):
    calls = []
    def handle(req):
        calls.append(req)
        ledger.cancel(project, work)
        return response()
    processor, ledger, project, work = setup(tmp_path, handle)
    job, payload = uuid4(), request()
    with track_ad_work(ledger, project, work):
        result = processor.process_job(payload, project, job)
    exported = ledger.export_dataset(project)
    assert exported["works"][0]["status"] == "CANCELLED"
    assert exported["receipts"][0]["status"] == "SUCCEEDED"
    assert processor.process_job(payload, project, job) == result
    assert len(calls) == 1
    assert ledger.export_dataset(project) == exported


def backend_body():
    return BackendImageInput(job_id="service-work-1", copy_revision=1, copy_hash="a" * 64,
        product_analysis={"analysis": {"is_cosmetic": True, "product_type": "색조",
            "container_type": "병", "colors": ["분홍"], "label_text": [],
            "mood": ["선명"], "is_transparent": False}, "analysis_succeeded": True, "error": None},
        image_prompt={"concept": "제품 배경", "prompt": "제품 사진을 보존하세요", "mood": ["청량"]},
        ad_copy={"headline": "오늘의 루틴"}, product_images=[png((32, 32)), png((32, 32), "blue")])


def test_backend_work_kwarg_binds_before_upload_and_preserves_existing_request_hash(tmp_path):
    calls = []
    processor, ledger, project, work = setup(tmp_path, lambda req: (calls.append(req), response((1152, 1440)))[1])
    backend = BackendImageAdapter(processor, tmp_path / "backend.sqlite3")
    body = backend_body()
    first = backend.generate(body, project_id=project, work_id=work)
    second = backend.generate(body, project_id=project, work_id=str(work))
    assert first == second and len(calls) == 1
    internal_job = backend._job(project, body.job_id)
    assert ledger.get_image_binding(project, internal_job) == str(work)
    data = ledger.export_dataset(project)
    assert data["attempts"][0]["request_hash"] == first.receipt["request_sha256"]
    assert len(data["receipts"]) == 1
    assert "work_id" not in body.model_dump()


def test_backend_conflicting_or_closed_work_fails_before_binding_or_asset_writes(tmp_path, monkeypatch):
    processor, ledger, project, work = setup(tmp_path, forbid)
    backend = BackendImageAdapter(processor, tmp_path / "backend.sqlite3")
    body = backend_body()
    job = backend._job(project, body.job_id)
    ledger.bind_image_job(project, work, job)
    other = uuid4()
    ledger.create_work(project, other, "product-1")
    monkeypatch.setattr(backend, "_bind", forbid)
    with pytest.raises(GatewayError) as conflict:
        backend.generate(body, project_id=project, work_id=other)
    assert conflict.value.code == "WORK_SCOPE_CONFLICT"
    ledger.cancel(project, work)
    with pytest.raises(GatewayError) as closed:
        backend.generate(body, project_id=project, work_id=work)
    assert closed.value.code == "WORK_CLOSED"


def prepared(tmp_path, project, job):
    product, prompt = tmp_path / "product.png", tmp_path / "prompt.txt"
    product.write_bytes(png((32, 32)))
    prompt.write_text("제품 사진을 보존하세요", encoding="utf-8")
    return freeform_image.prepare_freeform(products=[product], references=[], prompt_file=prompt,
        output=tmp_path / "outputs/freeform", project_id=project, job_id=job)


def test_freeform_tracks_with_path_ledger_without_changing_saved_preparation(tmp_path):
    calls = []
    _, ledger, project, work = setup(tmp_path, forbid)
    job = uuid4()
    path = prepared(tmp_path, project, job)
    before = path.read_bytes()
    args = {"project_id": project, "job_id": job, "attempt_db": tmp_path / "freeform-attempts.sqlite3",
            "budget_usd": "1", "work_id": work, "work_ledger": ledger.path,
            "transport": httpx.MockTransport(lambda req: (calls.append(req), response((1152, 1440)))[1])}
    first = freeform_image.execute_freeform(path, **args)
    assert freeform_image.execute_freeform(path, **args) == first
    assert path.read_bytes() == before
    assert len(calls) == len(ledger.export_dataset(project)["receipts"]) == 1
    assert first["request_sha256"] == json.loads(before)["request_sha256"]


@pytest.mark.parametrize("failure", ["closed", "foreign", "database"])
def test_freeform_work_gate_precedes_prepared_assets_and_credentials(tmp_path, monkeypatch, failure):
    _, ledger, project, work = setup(tmp_path, forbid)
    args = {"project_id": project, "job_id": uuid4(), "attempt_db": tmp_path / "missing-attempts.sqlite3",
            "work_id": work, "work_ledger": ledger, "budget_usd": "1",
            "transport": httpx.MockTransport(forbid)}
    if failure == "closed":
        ledger.cancel(project, work)
    elif failure == "foreign":
        args["project_id"] = uuid4()
    else:
        args["attempt_db"] = ledger.path
    monkeypatch.setattr(freeform_image, "_read_json", forbid)
    with pytest.raises((GatewayError, ValueError)) as error:
        freeform_image.execute_freeform(tmp_path / "missing.json", **args)
    code = getattr(error.value, "code", str(error.value))
    assert code == {"closed": "WORK_CLOSED", "foreign": "WORK_NOT_FOUND", "database": "WORK_LEDGER_CONFLICT"}[failure]
    assert not (tmp_path / "missing-attempts.sqlite3").exists()


@pytest.mark.parametrize("flag", ["--work-id", "--work-ledger"])
def test_freeform_cli_requires_work_flags_together_before_preparation(flag):
    value = str(uuid4()) if flag == "--work-id" else "/unused/works.sqlite3"
    with pytest.raises(SystemExit) as error:
        freeform_image.main(["--prepared", "/unused/request.json", flag, value])
    assert error.value.code == 2


def test_processor_rejects_combined_work_and_provider_database(tmp_path):
    ledger = AdWorkLedger(tmp_path / "combined.sqlite3")
    with pytest.raises(GatewayError) as conflict:
        ExternalImageProcessor(LocalAssetStore(tmp_path / "assets"),
            OpenAIImagesAdapter(OpenAIImagesConfig(), transport=httpx.MockTransport(forbid)),
            ProviderAttemptStore(ledger.path), work_ledger=ledger)
    assert conflict.value.code == "WORK_LEDGER_CONFLICT"


def test_active_scope_cannot_bypass_separate_provider_database_guard(tmp_path, monkeypatch):
    processor, ledger, project, work = setup(tmp_path, forbid, tracked=False)
    processor.attempts = ProviderAttemptStore(ledger.path)
    monkeypatch.setattr(processor.assets, "read_bytes", forbid)
    with track_ad_work(ledger, project, work):
        with pytest.raises(GatewayError) as conflict:
            processor.process_job(request(), project, uuid4())
    assert conflict.value.code == "WORK_LEDGER_CONFLICT"


def test_freeform_rejects_nonexistent_overlapping_database_before_creating_it(tmp_path):
    shared = tmp_path / "must-not-create.sqlite3"
    with pytest.raises(ValueError, match="WORK_LEDGER_CONFLICT"):
        freeform_image.execute_freeform(tmp_path / "missing.json", project_id=uuid4(), job_id=uuid4(),
            work_id=uuid4(), work_ledger=shared, attempt_db=shared)
    assert not shared.exists()


def test_freeform_cli_passes_explicit_work_without_adding_it_to_prepared_request(tmp_path, monkeypatch, capsys):
    _, ledger, project, work = setup(tmp_path, forbid)
    job = uuid4()
    path = prepared(tmp_path, project, job)
    before = path.read_bytes()
    seen = []
    monkeypatch.setattr(freeform_image, "load_account_budget", lambda _: None)
    def execute(source, **kwargs):
        seen.append((source, kwargs))
        return {"status": "SUCCEEDED"}
    monkeypatch.setattr(freeform_image, "execute_freeform", execute)
    assert freeform_image.main(["--prepared", str(path), "--execute", "--project-id", str(project),
        "--job-id", str(job), "--env-file", str(tmp_path / "unused.env"),
        "--attempt-db", str(tmp_path / "cli-attempts.sqlite3"), "--work-id", str(work),
        "--work-ledger", str(ledger.path)]) == 0
    assert seen[0][0] == path
    assert seen[0][1]["work_id"] == work
    assert seen[0][1]["work_ledger"].path == ledger.path
    assert path.read_bytes() == before
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCEEDED"
