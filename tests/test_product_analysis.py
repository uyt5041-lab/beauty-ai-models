import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from beauty_ai.product_analysis import ProductAnalysis, ProductAnalysisResult


def analysis_result(**updates):
    result = dict(analysis=dict(is_cosmetic=True, product_type="색조",
        container_type="어플리케이터", colors=["연분홍", "아이보리"],
        label_text=["unverified OCR"], mood=["산뜻한"], is_transparent=True),
        analysis_succeeded=True, error=None)
    result.update(updates)
    return result


@pytest.mark.parametrize("container", ["어플리케이터", "스틱", "병", "튜브"])
def test_team_result_parses_without_reanalysis(container):
    body = analysis_result()
    body["analysis"]["container_type"] = container
    parsed = ProductAnalysisResult.model_validate(body)
    assert parsed.analysis.container_type == container
    assert parsed.analysis.label_text == ["unverified OCR"]
    assert parsed.analysis_succeeded is True


def test_failure_default_is_not_success_and_raw_error_is_not_serialized():
    result = ProductAnalysisResult.model_validate(analysis_result(
        analysis_succeeded=False, error="private provider error"))
    assert result.analysis.is_cosmetic is True
    assert result.analysis_succeeded is False
    assert "private provider error" not in repr(result)
    assert "private provider error" not in result.model_dump_json()


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_success_flag_is_strict(value):
    with pytest.raises(ValidationError):
        ProductAnalysisResult.model_validate(analysis_result(analysis_succeeded=value))


def test_missing_success_flag_is_rejected():
    body = analysis_result()
    del body["analysis_succeeded"]
    with pytest.raises(ValidationError):
        ProductAnalysisResult.model_validate(body)


@pytest.mark.parametrize("model", [ProductAnalysis, ProductAnalysisResult])
def test_receiving_schema_is_published(model):
    path = Path(__file__).resolve().parents[1] / "schemas" / f"{model.__name__}.schema.json"
    assert json.loads(path.read_text()) == model.model_json_schema()
