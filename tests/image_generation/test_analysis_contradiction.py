from contextlib import closing
from uuid import uuid4
import sqlite3
import pytest
from pydantic import ValidationError
from src.image_generation.backend_adapter import BackendImageInput
from src.image_generation.errors import GatewayError
from .test_backend_adapter import setup, body, analysis_result, response

@pytest.mark.parametrize("error_text", ["private-provider-error", "", "   "])
@pytest.mark.parametrize("representation", ["parsed", "mutated", "constructed", "raw_nested"])
@pytest.mark.parametrize("cosmetic", [True, False])
def test_success_with_any_nonnull_error_stops_before_all_side_effects(
        tmp_path, monkeypatch, error_text, representation, cosmetic):
    result=analysis_result(error=error_text)
    result["analysis"]["is_cosmetic"]=cosmetic
    payload=body(product_analysis=result)
    if representation=="mutated":
        payload=body();payload.product_analysis.error=error_text
        payload.product_analysis.analysis.is_cosmetic=cosmetic
    elif representation=="constructed":
        payload=BackendImageInput.model_construct(**payload.__dict__)
    elif representation=="raw_nested":
        payload=body().model_copy(update={"product_analysis":result})
    adapter=setup(tmp_path,lambda req:pytest.fail("provider must not be called"))
    def forbidden(*args,**kwargs):pytest.fail("no binding, asset writes or provider attempts")
    monkeypatch.setattr(adapter,"_bind",forbidden)
    monkeypatch.setattr(adapter.processor.assets,"put_upload",forbidden)
    monkeypatch.setattr(adapter.processor,"process_job",forbidden)
    with pytest.raises(GatewayError) as rejected:
        adapter.generate(payload,project_id=uuid4())
    assert rejected.value.code=="PRODUCT_ANALYSIS_FAILED"
    if error_text.strip():assert error_text not in str(rejected.value)
    with closing(sqlite3.connect(adapter.path)) as db, db:
        assert db.execute("SELECT COUNT(*) FROM backend_image_bindings").fetchone()[0]==0
    with closing(sqlite3.connect(adapter.processor.attempts.path)) as db, db:
        assert db.execute("SELECT COUNT(*) FROM provider_attempts").fetchone()[0]==0
    assert not list(adapter.processor.assets.files_dir.glob("**/*.png"))


def test_success_without_error_still_runs_once_and_keeps_private_field_excluded(tmp_path):
    calls=[]
    adapter=setup(tmp_path,lambda req:(calls.append(req.read()),response())[1])
    payload=body()
    assert "error" not in payload.product_analysis.model_dump()
    assert payload.product_analysis.error is None
    result=adapter.generate(payload,project_id=uuid4())
    assert len(calls)==1 and result.receipt["state"]=="succeeded"


def test_mutated_invalid_error_type_is_revalidated_before_execution(tmp_path,monkeypatch):
    payload=body();payload.product_analysis.error={"bad":"value"}
    adapter=setup(tmp_path,lambda req:pytest.fail("provider must not be called"))
    monkeypatch.setattr(adapter,"_bind",lambda *args:pytest.fail("binding must not run"))
    with pytest.raises(ValidationError):adapter.generate(payload,project_id=uuid4())
