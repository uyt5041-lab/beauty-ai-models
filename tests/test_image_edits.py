from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from PIL import Image
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import BackgroundRequest, BackgroundResult, VisualBrief, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.image_edits import ImageEditStore
from beauty_ai.image_integrity import composite_approved_foreground
from beauty_ai.job_ledger import JobLedger
from beauty_ai.product_approval import ProductApprovalStore


def request_for(base, assets, project):
    source_image = Image.new("RGBA", (30, 50))
    source_image.paste((23, 75, 146, 255), (5, 5, 25, 45))
    source = assets.put_image(source_image, project_id=project)
    visual = VisualBrief(concept="bright test", style_preset_id="bright-ad", style_preset_version="1",
        layout_template_id="product-right", layout_template_version="1",
        generation_width=256, generation_height=256,
        product_box={"x": .6, "y": .1, "width": .3, "height": .8},
        text_safe_zone={"x": .05, "y": .1, "width": .4, "height": .8})
    candidate = ProductApprovalStore(base / "approvals.sqlite3", assets).prepare(project, source, source, visual)
    return BackgroundRequest(source=source, product_rgba=candidate.product_rgba,
        preserve_mask=candidate.preserve_mask, visual=visual, seed=1,
        pipeline_profile_id="bright-template", pipeline_revision="1")


@pytest.fixture
def setup(tmp_path):
    project = uuid4()
    assets = LocalAssetStore(tmp_path / "assets")
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    store = ImageEditStore(tmp_path / "edits.sqlite3", assets, ledger)
    return store, assets, ledger, project, request_for(tmp_path, assets, project)


def result_for(assets, project, request):
    with assets.read(request.product_rgba, project_id=project) as product:
        scene = Image.new("RGB", product.size, "pink")
        preview = composite_approved_foreground(scene, product)
        return BackgroundResult(scene_underlay=assets.put_image(scene, project_id=project),
            product_rgba=request.product_rgba,
            composed_preview=assets.put_image(preview, project_id=project), warnings=["TEST_FIXTURE"])


def complete(ledger, project, request, result=None, *, error=None):
    job_id = uuid4()
    ledger.submit(job_id, project, request)
    claimed = ledger.claim_next("test-owner")
    assert claimed.job.job_id == job_id
    ledger.finish(job_id, "test-owner", result=result, error_code=error)
    return job_id


def error_code(code, operation):
    with pytest.raises(GatewayError) as caught:
        operation()
    assert caught.value.code == code


def test_selection_snapshot_and_restart_are_durable(setup):
    store, assets, ledger, project, request = setup
    campaign = uuid4()
    selected = store.select(campaign, project, 0, request, 1)
    assert selected.revision == selected.copy_revision == 1
    assert selected.active_job_id is None
    assert selected.request_hash == content_hash(request)
    request.visual.concept = "caller changed mutable object"
    reopened = ImageEditStore(store.path, assets, ledger)
    assert reopened.get(campaign, project) == selected
    assert reopened.get(campaign, project).request.visual.concept == "bright test"


def test_select_revision_preconditions_and_copy_revision_never_go_back(setup):
    store, _, _, project, request = setup
    campaign = uuid4()
    error_code("EDIT_CONFLICT", lambda: store.select(campaign, project, 1, request, 1))
    store.select(campaign, project, 0, request, 2)
    error_code("EDIT_CONFLICT", lambda: store.select(campaign, project, 0, request, 3))
    error_code("EDIT_CONFLICT", lambda: store.select(campaign, project, 1, request, 1))
    assert store.get(campaign, project).revision == 1
    assert store.get(campaign, project).copy_revision == 2


@pytest.mark.parametrize("expected,copy", [(True, 1), (-1, 1), (0, 0), (0, "1"), (0, 2**63)])
def test_revision_inputs_are_strict_integers(setup, expected, copy):
    store, _, _, project, request = setup
    error_code("INVALID_IMAGE_REQUEST", lambda: store.select(uuid4(), project, expected, request, copy))


def test_successful_attach_replay_copy_only_reuse_and_new_campaign_reuse(setup):
    store, assets, ledger, project, request = setup
    campaign = uuid4()
    store.select(campaign, project, 0, request, 1)
    job_id = complete(ledger, project, request, result_for(assets, project, request))
    attached = store.attach(campaign, project, 1, job_id)
    assert attached.active_job_id == job_id and attached.revision == 1
    assert store.attach(campaign, project, 1, job_id) == attached
    copied = store.select(campaign, project, 1, request, 2)
    assert copied.active_job_id == job_id and copied.revision == 2
    assert copied.request_hash == attached.request_hash
    other = store.select(uuid4(), project, 0, request, 1)
    assert other.active_job_id == job_id
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_changed_visual_seed_profile_or_assets_have_different_cache_keys(setup):
    store, assets, ledger, project, request = setup
    campaign = uuid4()
    store.select(campaign, project, 0, request, 1)
    job_id = complete(ledger, project, request, result_for(assets, project, request))
    store.attach(campaign, project, 1, job_id)
    changed_visual = request.visual.model_copy(update={"concept": "different visual intent"})
    with assets.read(request.source, project_id=project) as source_image:
        other_source = assets.put_image(source_image, project_id=project)
    for change in ({"seed": 2}, {"visual": changed_visual}, {"pipeline_revision": "2"},
                   {"source": other_source}):
        selection = store.select(uuid4(), project, 0, request.model_copy(update=change), 1)
        assert selection.request_hash != content_hash(request)
        assert selection.active_job_id is None


def test_late_or_mismatched_result_does_not_replace_current_selection(setup):
    store, assets, ledger, project, request = setup
    campaign = uuid4()
    store.select(campaign, project, 0, request, 1)
    old_job = complete(ledger, project, request, result_for(assets, project, request))
    changed = request.model_copy(update={"seed": 2})
    current = store.select(campaign, project, 1, changed, 2)
    error_code("EDIT_CONFLICT", lambda: store.attach(campaign, project, 1, old_job))
    error_code("EDIT_CONFLICT", lambda: store.attach(campaign, project, 2, old_job))
    assert store.get(campaign, project) == current


@pytest.mark.parametrize("state", ["queued", "running", "failed", "interrupted"])
def test_non_success_job_never_attaches(setup, state):
    store, _, ledger, project, request = setup
    campaign, job_id = uuid4(), uuid4()
    selected = store.select(campaign, project, 0, request, 1)
    ledger.submit(job_id, project, request)
    if state != "queued":
        ledger.claim_next("owner")
    if state == "failed":
        ledger.finish(job_id, "owner", error_code="TEST_FAILED")
    if state == "interrupted":
        ledger.recover_interrupted()
    error_code("IMAGE_JOB_NOT_READY", lambda: store.attach(campaign, project, 1, job_id))
    assert store.get(campaign, project) == selected


def test_failed_new_job_preserves_existing_result_and_copy_revision(setup):
    store, assets, ledger, project, request = setup
    campaign = uuid4()
    store.select(campaign, project, 0, request, 3)
    good = complete(ledger, project, request, result_for(assets, project, request))
    attached = store.attach(campaign, project, 1, good)
    failed = complete(ledger, project, request, error="IMAGE_OUT_OF_MEMORY")
    error_code("IMAGE_JOB_NOT_READY", lambda: store.attach(campaign, project, 1, failed))
    assert store.get(campaign, project) == attached


def test_project_scope_cannot_read_foreign_selection_inputs_or_cache(setup, tmp_path):
    store, assets, ledger, project, request = setup
    campaign, foreign_project = uuid4(), uuid4()
    store.select(campaign, project, 0, request, 1)
    error_code("EDIT_NOT_FOUND", lambda: store.get(campaign, foreign_project))
    error_code("EDIT_NOT_FOUND", lambda: store.attach(campaign, foreign_project, 1, uuid4()))
    error_code("INVALID_IMAGE_REQUEST", lambda: store.select(campaign, foreign_project, 0, request, 1))
    job_id = complete(ledger, project, request, result_for(assets, project, request))
    store.attach(campaign, project, 1, job_id)
    foreign_request = request_for(tmp_path, assets, foreign_project)
    # Even corrupt cache metadata cannot publish a job owned by another project.
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO image_cache(project_id,request_hash,job_id) VALUES(?,?,?)",
                   (str(foreign_project), content_hash(foreign_request), str(job_id)))
    foreign = store.select(campaign, foreign_project, 0, foreign_request, 1)
    assert foreign.active_job_id is None
    assert store.get(campaign, project).active_job_id == job_id


@pytest.mark.parametrize("tamper", ["missing", "changed_bytes"])
def test_broken_result_cache_is_evicted_without_hiding_a_hit(setup, tamper):
    store, assets, ledger, project, request = setup
    campaign = uuid4()
    store.select(campaign, project, 0, request, 1)
    result = result_for(assets, project, request)
    job_id = complete(ledger, project, request, result)
    store.attach(campaign, project, 1, job_id)
    path = assets.files_dir / project.hex / f"{result.composed_preview.asset_id.hex}.png"
    if tamper == "missing": path.unlink()
    else: path.write_bytes(b"tampered")
    selected = store.select(campaign, project, 1, request, 2)
    assert selected.active_job_id is None
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM image_cache").fetchone()[0] == 0


def test_corrupt_input_is_invalid_instead_of_triggering_regeneration(setup):
    store, assets, _, project, request = setup
    path = assets.files_dir / project.hex / f"{request.preserve_mask.asset_id.hex}.png"
    path.write_bytes(b"invalid")
    error_code("INVALID_IMAGE_REQUEST", lambda: store.select(uuid4(), project, 0, request, 1))


@pytest.mark.parametrize("bad", ["wrong_product", "wrong_canvas", "wrong_pixels"])
def test_result_layer_identity_geometry_and_pixels_must_match(setup, bad):
    store, assets, ledger, project, request = setup
    campaign = uuid4()
    selected = store.select(campaign, project, 0, request, 1)
    result = result_for(assets, project, request)
    if bad == "wrong_product":
        with assets.read(request.product_rgba, project_id=project) as product:
            result.product_rgba = assets.put_image(product, project_id=project)
    elif bad == "wrong_canvas":
        result.scene_underlay = assets.put_image(Image.new("RGB", (32, 32)), project_id=project)
    else:
        result.composed_preview = assets.put_image(Image.new("RGB", (256, 256), "black"), project_id=project)
    job_id = complete(ledger, project, request, result)
    error_code("INVALID_IMAGE_REQUEST", lambda: store.attach(campaign, project, 1, job_id))
    assert store.get(campaign, project) == selected


def test_storage_outage_is_not_swallowed_as_cache_miss(setup, monkeypatch):
    store, assets, ledger, project, request = setup
    campaign = uuid4()
    store.select(campaign, project, 0, request, 1)
    job_id = complete(ledger, project, request, result_for(assets, project, request))
    attached = store.attach(campaign, project, 1, job_id)
    def unavailable(*args, **kwargs):
        raise GatewayError("JOB_STORAGE_ERROR", "private database detail", retryable=True)
    monkeypatch.setattr(ledger, "get", unavailable)
    error_code("EDIT_STORAGE_ERROR", lambda: store.select(campaign, project, 1, request, 2))
    assert store.get(campaign, project) == attached
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM image_cache").fetchone()[0] == 1


def test_snapshot_corruption_and_edit_database_failure_are_stable_errors(setup):
    store, _, _, project, request = setup
    campaign = uuid4()
    store.select(campaign, project, 0, request, 1)
    with sqlite3.connect(store.path) as db:
        changed = request.model_dump(mode="json"); changed["seed"] = 99
        db.execute("UPDATE image_selections SET request_json=?", (json.dumps(changed),))
    error_code("EDIT_STORAGE_ERROR", lambda: store.get(campaign, project))
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE image_selections")
    error_code("EDIT_STORAGE_ERROR", lambda: store.get(campaign, project))


def _competing_edit(base, campaign, project, request_json, copy_revision, barrier, queue):
    base = Path(base)
    store = ImageEditStore(base / "edits.sqlite3", LocalAssetStore(base / "assets"),
                           JobLedger(base / "jobs.sqlite3"))
    barrier.wait(timeout=20)
    try:
        selected = store.select(UUID(campaign), UUID(project), 1,
                                BackgroundRequest.model_validate_json(request_json), copy_revision)
        queue.put(("selected", selected.revision, selected.copy_revision))
    except GatewayError as error:
        queue.put((error.code, None, None))


def test_real_process_compare_and_swap_allows_one_editor(setup, tmp_path):
    store, _, _, project, request = setup
    campaign = uuid4()
    store.select(campaign, project, 0, request, 1)
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(3), context.Queue()
    processes = [context.Process(target=_competing_edit, args=(str(tmp_path), str(campaign), str(project),
        request.model_dump_json(), copy_revision, barrier, queue)) for copy_revision in (2, 3, 4)]
    for process in processes: process.start()
    outcomes = [queue.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    assert [row[0] for row in outcomes].count("selected") == 1
    assert [row[0] for row in outcomes].count("EDIT_CONFLICT") == 2
    winner = next(row for row in outcomes if row[0] == "selected")
    selected = store.get(campaign, project)
    assert selected.revision == winner[1] == 2
    assert selected.copy_revision == winner[2]
