import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from beauty_ai.contracts import AssetRef, ExternalImageRequest, ImagePrompt, content_hash
from beauty_ai.cosmetics_prompts import render_cosmetics_provider_prompt
from beauty_ai.korean_image_policy import build_korean_cutout_request, WATERMARK_POLICY_KO
from beauty_ai.openai_images import OpenAIImagesAdapter


def asset(n=1):
    return AssetRef(asset_id=UUID(int=n), sha256='0'*64, width=64, height=80, mime_type='image/png')


def request(op='edit', negative=''):
    if op == 'cutout':
        return build_korean_cutout_request(asset())
    return ExternalImageRequest(operation=op, pipeline_revision='2',
        product_images=[asset(),asset(2)] if op=='edit' else [], reference_images=[asset(3)],
        prompt=ImagePrompt(positive='원문 그대로\n밝은 배경', negative=negative, language='ko',
                           prompt_version='team-ko-v1', source_revision=1),
        cosmetics_policy_version='ko-v1', watermark_policy='exclude-v1')


@pytest.mark.parametrize('op', ['edit','generate','cutout'])
def test_korean_common_policy_has_preservation_and_watermark_rules(op):
    text=OpenAIImagesAdapter._prompt(request(op))
    assert WATERMARK_POLICY_KO in text
    assert '전체 폭·길이' in text and '무광' in text and '뚜껑과 본체의 비율' in text
    assert '광고 문구' in text and '로고·라벨 글자' in text
    for old in ['PRODUCT REFERENCES','REQUIRED COSMETICS','WATERMARK POLICY','Keep','Avoid:']:
        assert old not in text
    if op=='edit':
        assert text.startswith('원문 그대로\n밝은 배경')
        assert '1번 사진' in text and '3번부터 3번' in text
    if op=='cutout':
        assert '투명한 알파 채널' in text and '체크무늬' in text


def test_negative_is_wrapped_in_korean_without_translation():
    assert '피해야 할 내용\n추가 제품 금지' in OpenAIImagesAdapter._prompt(request(negative='추가 제품 금지'))


@pytest.mark.parametrize('change', [
    {'cosmetics_policy_version':'ko-v999'}, {'pipeline_revision':'1'}, {'watermark_policy':None},
])
def test_incompatible_or_unknown_policy_cannot_call_provider(change):
    data=request().model_dump(mode='json');data.update(change)
    with pytest.raises(ValidationError):
        ExternalImageRequest.model_validate(data)


def test_english_declaration_is_rejected_for_new_korean_policy():
    data=request().model_dump(mode='json');data['prompt']['language']='en'
    with pytest.raises(ValidationError):ExternalImageRequest.model_validate(data)


def test_old_requests_keep_exact_hash_serialization_and_effective_text():
    items=json.loads((Path(__file__).parent/'fixtures/pre_korean_policy_requests.json').read_text())
    for item in items:
        restored=ExternalImageRequest.model_validate(item['request'])
        assert restored.model_dump(mode='json')==item['request']
        assert content_hash(restored)==item['request_sha256']
        assert render_cosmetics_provider_prompt(restored)==item['effective_prompt']
        assert 'cosmetics_policy_version' not in restored.model_dump()


def test_policy_version_is_part_of_request_hash():
    current=request(); old=current.model_dump(mode='json');old.pop('cosmetics_policy_version')
    assert content_hash(current)!=content_hash(ExternalImageRequest.model_validate(old))
