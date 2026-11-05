from contextlib import closing
import base64
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from src.image_generation import BackendImageInput, CompositionInput, ImagePrompt, compose_ad_png
from src.image_generation.contracts import content_hash
from src.image_generation.errors import GatewayError


from . import test_backend_adapter as backend
from . import helpers as composition


@pytest.mark.parametrize('value', [
    '  밝은 한국어 배경\n원본 제품의 무광을 유지한다.  ',
    {'prompt': '밝은 한국어 배경'},
    {'prompt': '밝은 한국어 배경', 'mood': ['청량']},
    {'concept': '일상 루틴', 'prompt': '밝은 한국어 배경', 'mood': ['청량']},
])
def test_prompt_forms_preserve_and_revalidate(value):
    body = backend.body(image_prompt=value)
    expected = value if isinstance(value, str) else value['prompt']
    assert body.image_prompt.prompt == expected
    if isinstance(value, str) or 'concept' not in value:
        assert body.image_prompt.concept is None
    assert BackendImageInput.model_validate(body.model_dump()).image_prompt == body.image_prompt


@pytest.mark.parametrize('value', ['', '  ', 123, True, ['문구'], {'prompt': ''},
    {'prompt': '문구', 'concept': ''}, {'prompt': '문구', 'mood': '청량'},
    {'positive': '문구'}, {'prompt': '문구', 'unexpected': 1}, '글'*3001])
def test_invalid_prompt_rejected(value):
    with pytest.raises(ValidationError):
        backend.body(image_prompt=value)


@pytest.mark.parametrize('revision', ['mock-copy-r01', '1', '01', '카피-v1', 1, 17])
def test_revision_identity_survives_all_models(revision):
    body = backend.body(copy_revision=revision)
    prompt = ImagePrompt(positive='한국어 문구', prompt_version='v2', source_revision=revision)
    comp = composition.body(copy_revision=revision)
    for actual in (body.copy_revision, prompt.source_revision, comp.copy_revision,
                   ImagePrompt.model_validate_json(prompt.model_dump_json()).source_revision):
        assert actual == revision and type(actual) is type(revision)


@pytest.mark.parametrize('revision', [True, False, 0, -1, 1.0, None, '', '   ', 'r\n1', 'r\x001', 'r'*129])
def test_invalid_revisions_rejected(revision):
    for make in (lambda: backend.body(copy_revision=revision),
                 lambda: composition.body(copy_revision=revision),
                 lambda: ImagePrompt(positive='한글', prompt_version='v2', source_revision=revision)):
        with pytest.raises(ValidationError):
            make()


def test_no_invented_concept_or_mood_and_same_representation_replays(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return backend.response()
    adapter, project = backend.setup(tmp_path, handler), uuid4()
    korean = '  분홍빛의 밝은 광고 배경.\n무광 제품은 그대로.  '
    body = backend.body(image_prompt=korean, copy_revision='mock-copy-r01')
    result = adapter.generate(body, project_id=project)
    with adapter._connect() as db:
        saved = json.loads(db.execute('SELECT request_json FROM backend_image_bindings').fetchone()[0])
    positive = saved['prompt']['positive']
    assert positive.startswith(korean + '\n\n')
    assert '콘셉트:' not in positive
    assert '위 한국어 프롬프트의 분위기 지시를 따르고' in positive
    assert saved['prompt']['language'] == 'ko'
    assert saved['prompt']['source_revision'] == result.copy_revision == 'mock-copy-r01'
    assert saved['prompt']['prompt_version'] == 'backend-v02-ko-v2'
    replay = adapter.generate(backend.body(image_prompt={'prompt': korean},
                                         copy_revision='mock-copy-r01'), project_id=project)
    assert replay == result and len(requests) == 1


def test_revision_type_change_never_reuses_paid_job(tmp_path):
    calls = []
    adapter = backend.setup(tmp_path, lambda req: calls.append(1) or backend.response())
    project = uuid4()
    adapter.generate(backend.body(copy_revision=1), project_id=project)
    with pytest.raises(GatewayError) as exc:
        adapter.generate(backend.body(copy_revision='1'), project_id=project)
    assert exc.value.code == 'BACKEND_INPUT_CONFLICT' and len(calls) == 1


def test_string_revision_generate_to_compose(tmp_path):
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(composition.picture()).decode()}]})
    copy = composition.copy_output()
    generated = backend.setup(tmp_path, handler).generate(backend.body(
        copy_revision='mock-copy-r01', copy_hash=content_hash(copy),
        image_prompt='노란 배경, 무광 화장품의 원형을 유지한다.',
        ad_copy=copy['ad_copy'], caption=copy['caption'], hashtags=copy['hashtags']), project_id=uuid4())
    output = compose_ad_png(CompositionInput(
        job_id=generated.job_id, image_bytes=generated.image_bytes, image_sha256=generated.sha256,
        copy_revision=generated.copy_revision, copy_hash=generated.copy_hash,
        copy_output=copy, layout=composition.layout()), font_path=composition.FONT)
    assert output.copy_revision == 'mock-copy-r01' and len(calls) == 1
    assert (output.width, output.height) == (1080, 1350)
    assert output.receipt['provider_calls'] == 0


@pytest.mark.parametrize('case', ['no_mood', 'with_mood'])
def test_old_published_binding_replays_without_another_call(tmp_path, case):
    # Exported from the actual d99dee0 code with MockTransport. The historical
    # request/receipt hashes are fixed, not recalculated with today's code.
    import base64
    import sqlite3
    from uuid import UUID
    fixture = json.loads((Path(__file__).parent / 'fixtures/legacy_d99dee0.json').read_text())
    assert fixture['source_commit'] == 'd99dee0d056e1fc91ab57d1b3817cb9e1dd27f03'
    record = fixture['cases'][case]
    adapter = backend.setup(tmp_path, lambda request: pytest.fail('Existing paid attempt was resubmitted'))
    for db_path, tables in record['databases'].items():
        with closing(sqlite3.connect(tmp_path / db_path)) as db, db:
            for table, saved in tables.items():
                columns = ','.join(saved['columns'])
                placeholders = ','.join('?' for _ in saved['columns'])
                db.executemany(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', saved['rows'])
    for relative, encoded in record['asset_files'].items():
        target = tmp_path / relative
        assert target.resolve().is_relative_to(tmp_path.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded))
    payload = dict(record['input'])
    for key in ('product_images', 'reference_images'):
        payload[key] = [base64.b64decode(value) for value in payload[key]]
    result = adapter.generate(BackendImageInput(**payload), project_id=UUID(record['project_id']))
    assert result.copy_revision == 1 and result.receipt['state'] == 'succeeded'
    assert result.sha256 == record['expected_image_sha256']


def test_schema_exposes_both_prompt_and_revision_forms():
    schema = BackendImageInput.model_json_schema()
    assert any(v.get('type') == 'string' for v in schema['properties']['image_prompt']['anyOf'])
    assert {v['type'] for v in schema['properties']['copy_revision']['anyOf']} == {'string','integer'}
