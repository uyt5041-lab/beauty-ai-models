import sys
from pathlib import Path
import pytest
from pydantic import ValidationError
from src.image_generation import BackendImageInput, ImagePrompt
from src.image_generation.contracts import ExternalImageRequest
from src.image_generation.korean_image_policy import render_korean_image_policy
from src.image_generation.openai_images import OpenAIImagesAdapter

def prompt():
    return ImagePrompt(positive="사용자 무드: 청량\n원본 제품은 무광", prompt_version="ko-v1", source_revision=1)

def test_only_team_package_loaded():
    import src.image_generation as package
    assert Path(package.__file__).resolve().parent == Path(__file__).resolve().parents[2] / "src/image_generation"
    assert not any(n == "beauty_ai" or n.startswith("beauty_ai.") for n in sys.modules)
    assert not any(n in sys.modules for n in ["langfuse", "wandb", "torch", "diffusers"])

def test_korean_request_is_default_and_original_is_prefix():
    request = ExternalImageRequest(operation="generate", prompt=prompt())
    assert request.prompt.language == "ko"
    assert request.cosmetics_policy_version == "ko-v1"
    wire = OpenAIImagesAdapter._prompt(request)
    assert wire.startswith(prompt().positive)
    assert "무광 표면을 유광으로 바꾸거나" in wire
    assert "워터마크 제외 정책" in wire

@pytest.mark.parametrize("changes", [{"pipeline_revision":"1"}, {"watermark_policy":None},
    {"cosmetics_policy_version":None}, {"managed_prompt":None}, {"width":1024,"height":1024}])
def test_legacy_experiment_inputs_are_not_part_of_mvp(changes):
    with pytest.raises(ValidationError):
        ExternalImageRequest(operation="generate",prompt=prompt(),**changes)

def test_english_declaration_rejected():
    with pytest.raises(ValidationError):
        ImagePrompt(positive="text",language="en",prompt_version="v1",source_revision=1)

def test_public_input_schema_does_not_expose_experiments():
    schema=BackendImageInput.model_json_schema()
    assert {"product_analysis","image_prompt","product_images","copy_hash","copy_revision"} <= set(schema["required"])
    assert not {"preset_id","managed_prompt","langfuse","wandb"} & set(schema["properties"])
