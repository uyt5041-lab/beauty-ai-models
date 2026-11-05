import base64
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image

from .helpers import FONT, copy_output, layout, picture
from src.image_generation import CompositionInput, compose_ad_png
from src.image_generation.contracts import content_hash

from . import test_backend_adapter as backend


def test_generated_candidate_to_approved_snapshot_composition(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, json={'data': [
            {'b64_json': base64.b64encode(picture()).decode()}]})
    copy = copy_output()
    payload = backend.body(copy_hash=content_hash(copy), ad_copy=copy['ad_copy'],
                           caption=copy['caption'], hashtags=copy['hashtags'])
    generated = backend.setup(tmp_path, handler).generate(payload, project_id=uuid4())
    # Server approval is represented by a supplied immutable snapshot in this
    # offline test. No claim is made about a live approval/JobStore endpoint.
    result = compose_ad_png(CompositionInput(
        job_id=generated.job_id, image_bytes=generated.image_bytes,
        image_sha256=generated.sha256, copy_revision=generated.copy_revision,
        copy_hash=generated.copy_hash, copy_output=copy, layout=layout()), font_path=FONT)
    assert len(calls) == 1  # mock generation only; composition makes no request
    assert result.receipt['provider_calls'] == 0
    assert result.copy_hash == generated.copy_hash
    assert result.source_image_sha256 == generated.sha256
    assert result.caption == copy['caption']
    assert result.hashtags == tuple(copy['hashtags'])
    with Image.open(BytesIO(result.image_bytes)) as image:
        assert image.size == (1080, 1350) and image.format == 'PNG'
