import json
import multiprocessing
import sqlite3
from uuid import UUID, uuid4

from PIL import Image
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import BackgroundRequest, Rect, VisualBrief, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.product_approval import ProductApprovalStore


def visual(**changes):
    values = dict(concept="bright studio", style_preset_id="bright", style_preset_version="1",
                  layout_template_id="split", layout_template_version="1",
                  generation_width=256, generation_height=256,
                  product_box=Rect(x=.5, y=.125, width=.375, height=.75),
                  text_safe_zone=Rect(x=0, y=0, width=.375, height=1))
    values.update(changes)
    return VisualBrief(**values)


def setup_store(tmp_path):
    project = uuid4()
    assets = LocalAssetStore(tmp_path / "assets")
    source = assets.put_image(Image.new("RGB", (50, 70), "white"), project_id=project)
    rgba = Image.new("RGBA", (30, 50))
    rgba.paste((30, 80, 120, 255), (5, 5, 25, 45))
    cutout = assets.put_image(rgba, project_id=project)
    approvals = ProductApprovalStore(tmp_path / "products.sqlite3", assets)
    return approvals, assets, project, source, cutout


def background(candidate, **changes):
    values = dict(source=candidate.source, product_rgba=candidate.product_rgba,
                  preserve_mask=candidate.preserve_mask, visual=candidate.visual,
                  seed=3, pipeline_profile_id="bright-template", pipeline_revision="1")
    values.update(changes)
    return BackgroundRequest(**values)


def fingerprint(candidate):
    return content_hash({key: getattr(candidate, key).model_dump(mode="json")
                         for key in ["source", "product_rgba", "preserve_mask", "visual"]})


def _approve_process(base, candidate_id, project_id, candidate_hash, barrier, queue):
    from pathlib import Path
    base = Path(base)
    store = ProductApprovalStore(base / "products.sqlite3", LocalAssetStore(base / "assets"))
    barrier.wait(timeout=20)
    candidate = store.approve(UUID(candidate_id), UUID(project_id), candidate_hash, confirmed=True)
    queue.put(candidate.model_dump_json())


def test_prepare_persists_unapproved_working_assets_and_precise_fingerprint(tmp_path):
    store, assets, project, source, cutout = setup_store(tmp_path)
    candidate = store.prepare(project, source, cutout, visual())
    assert not candidate.approved
    assert candidate.source == source
    assert candidate.candidate_hash == fingerprint(candidate)
    assert candidate.product_rgba != cutout
    assert assets.read(candidate.product_rgba, project_id=project).size == (256, 256)
    assert assets.read(candidate.product_rgba, project_id=project).mode == "RGBA"
    assert assets.read(candidate.preserve_mask, project_id=project).size == (256, 256)
    reopened = ProductApprovalStore(store.path, assets)
    assert reopened.get(candidate.candidate_id, project) == candidate
    with pytest.raises(GatewayError) as raised:
        reopened.require_approved(background(candidate), project)
    assert raised.value.code == "PRODUCT_APPROVAL_REQUIRED"


@pytest.mark.parametrize("confirmed", [False, 1, "true", None])
def test_approval_requires_literal_confirmation(tmp_path, confirmed):
    store, _, project, source, cutout = setup_store(tmp_path)
    candidate = store.prepare(project, source, cutout, visual())
    with pytest.raises(GatewayError) as raised:
        store.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=confirmed)
    assert raised.value.code == "PRODUCT_APPROVAL_REQUIRED"
    assert not store.get(candidate.candidate_id, project).approved


def test_approval_hash_scope_and_idempotent_replay(tmp_path):
    store, assets, project, source, cutout = setup_store(tmp_path)
    candidate = store.prepare(project, source, cutout, visual())
    for call in [lambda: store.get(candidate.candidate_id, uuid4()),
                 lambda: store.approve(candidate.candidate_id, uuid4(), candidate.candidate_hash, confirmed=True)]:
        with pytest.raises(GatewayError) as raised:
            call()
        assert raised.value.code == "PRODUCT_NOT_FOUND"
    with pytest.raises(GatewayError) as raised:
        store.approve(candidate.candidate_id, project, "0" * 64, confirmed=True)
    assert raised.value.code == "PRODUCT_APPROVAL_CONFLICT"
    approved = store.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=True)
    assert approved.approved
    assert approved.candidate_hash == candidate.candidate_hash
    with sqlite3.connect(store.path) as db:
        stamp = db.execute("SELECT approved_at FROM product_candidates").fetchone()[0]
    assert store.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=True) == approved
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT approved_at FROM product_candidates").fetchone()[0] == stamp
    assert ProductApprovalStore(store.path, assets).require_approved(background(candidate), project) == approved


def test_changed_visual_or_assets_need_new_approval_but_seed_and_profile_do_not(tmp_path):
    store, assets, project, source, cutout = setup_store(tmp_path)
    candidate = store.prepare(project, source, cutout, visual())
    approved = store.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=True)
    assert store.require_approved(background(candidate, seed=999, pipeline_profile_id="other"), project) == approved
    new_source = assets.put_image(Image.new("RGB", (50, 70), "white"), project_id=project)
    for changed in [background(candidate, visual=visual(concept="different intent")),
                    background(candidate, source=new_source)]:
        with pytest.raises(GatewayError) as raised:
            store.require_approved(changed, project)
        assert raised.value.code == "PRODUCT_APPROVAL_REQUIRED"
    with pytest.raises(GatewayError) as raised:
        store.require_approved(background(candidate), uuid4())
    assert raised.value.code == "PRODUCT_APPROVAL_REQUIRED"


def test_prepare_rejects_foreign_broken_or_non_rgba_cutout(tmp_path):
    store, assets, project, source, cutout = setup_store(tmp_path)
    foreign = assets.put_image(Image.new("RGBA", (5, 5), "red"), project_id=uuid4())
    empty = assets.put_image(Image.new("RGBA", (5, 5)), project_id=project)
    for invalid in [foreign, source, empty, cutout.model_copy(update={"sha256": "0" * 64})]:
        with pytest.raises(GatewayError) as raised:
            store.prepare(project, source, invalid, visual())
        assert raised.value.code == "INVALID_IMAGE_REQUEST"
    assert store.prepare(project, source, cutout, visual()).approved is False


def test_tampered_files_prevent_approval_and_future_use(tmp_path):
    store, assets, project, source, cutout = setup_store(tmp_path)
    candidate = store.prepare(project, source, cutout, visual())
    path = assets.files_dir / project.hex / f"{candidate.product_rgba.asset_id.hex}.png"
    original = path.read_bytes()
    path.write_bytes(b"damaged")
    with pytest.raises(GatewayError) as raised:
        store.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=True)
    assert raised.value.code == "INVALID_IMAGE_REQUEST"
    assert not store.get(candidate.candidate_id, project).approved
    path.write_bytes(original)
    store.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=True)
    path.unlink()
    with pytest.raises(GatewayError) as raised:
        store.require_approved(background(candidate), project)
    assert raised.value.code == "INVALID_IMAGE_REQUEST"


@pytest.mark.parametrize("kind", ["gray", "all-white", "colored", "transparent"])
def test_valid_files_with_invalid_mask_semantics_cannot_be_approved(tmp_path, kind):
    store, assets, project, source, cutout = setup_store(tmp_path)
    candidate = store.prepare(project, source, cutout, visual())
    if kind == "gray":
        mask = Image.new("L", (256, 256), 128)
    elif kind == "all-white":
        mask = Image.new("L", (256, 256), 255)
    elif kind == "colored":
        mask = assets.read(candidate.preserve_mask, project_id=project).convert("RGB")
        mask.putpixel((0, 0), (0, 0, 1))  # RGB->L would round this to zero.
    else:
        mask = assets.read(candidate.preserve_mask, project_id=project).convert("RGBA")
        mask.putpixel((0, 0), (0, 0, 0, 0))
    invalid_ref = assets.put_image(mask, project_id=project)
    with sqlite3.connect(store.path) as db:
        snapshot = json.loads(db.execute("SELECT snapshot_json FROM product_candidates").fetchone()[0])
        snapshot["preserve_mask"] = invalid_ref.model_dump(mode="json")
        changed_hash = content_hash(snapshot)
        db.execute("UPDATE product_candidates SET snapshot_json=?, candidate_hash=?",
                   (json.dumps(snapshot), changed_hash))
    # Even a self-consistent snapshot from another producer must pass actual
    # mask semantics, not only metadata/hash checks, before approval.
    with pytest.raises(GatewayError) as raised:
        store.approve(candidate.candidate_id, project, changed_hash, confirmed=True)
    assert raised.value.code == "INVALID_IMAGE_REQUEST"
    assert not store.get(candidate.candidate_id, project).approved


def test_geometry_guard_rejects_nonzero_alpha_outside_product_box(tmp_path):
    store, assets, project, source, cutout = setup_store(tmp_path)
    candidate = store.prepare(project, source, cutout, visual())
    product = assets.read(candidate.product_rgba, project_id=project)
    product.putpixel((0, 0), (30, 80, 120, 255))
    product_ref = assets.put_image(product, project_id=project)
    mask_ref = assets.put_image(product.getchannel("A").point(lambda a: 255 if a else 0), project_id=project)
    with sqlite3.connect(store.path) as db:
        snapshot = json.loads(db.execute("SELECT snapshot_json FROM product_candidates").fetchone()[0])
        snapshot.update(product_rgba=product_ref.model_dump(mode="json"), preserve_mask=mask_ref.model_dump(mode="json"))
        changed_hash = content_hash(snapshot)
        db.execute("UPDATE product_candidates SET snapshot_json=?, candidate_hash=?", (json.dumps(snapshot), changed_hash))
    with pytest.raises(GatewayError) as raised:
        store.approve(candidate.candidate_id, project, changed_hash, confirmed=True)
    assert raised.value.code == "INVALID_IMAGE_REQUEST"


def test_corrupt_database_snapshot_is_rejected(tmp_path):
    store, _, project, source, cutout = setup_store(tmp_path)
    candidate = store.prepare(project, source, cutout, visual())
    with sqlite3.connect(store.path) as db:
        snapshot = json.loads(db.execute("SELECT snapshot_json FROM product_candidates").fetchone()[0])
        snapshot["visual"]["concept"] = "silently changed"
        db.execute("UPDATE product_candidates SET snapshot_json=?", (json.dumps(snapshot),))
    for call in [lambda: store.get(candidate.candidate_id, project),
                 lambda: store.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=True)]:
        with pytest.raises(GatewayError) as raised:
            call()
        assert raised.value.code == "PRODUCT_STORAGE_ERROR"
        assert not raised.value.retryable


def test_concurrent_process_approvals_are_idempotent(tmp_path):
    store, _, project, source, cutout = setup_store(tmp_path)
    candidate = store.prepare(project, source, cutout, visual())
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(3), context.Queue()
    processes = [context.Process(target=_approve_process,
        args=(str(tmp_path), str(candidate.candidate_id), str(project), candidate.candidate_hash, barrier, queue))
        for _ in range(3)]
    try:
        for process in processes:
            process.start()
        records = [queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
        assert len(set(records)) == 1
        assert json.loads(records[0])["approved"] is True
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        queue.close()
        queue.join_thread()


def test_storage_failure_has_stable_code_without_paths(tmp_path):
    assets = LocalAssetStore(tmp_path / "assets")
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory")
    with pytest.raises(GatewayError) as raised:
        ProductApprovalStore(occupied / "products.sqlite3", assets)
    assert raised.value.code == "PRODUCT_STORAGE_ERROR"
    assert str(occupied) not in str(raised.value)
