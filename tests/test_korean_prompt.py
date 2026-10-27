"""Korean content survives the real adapter serializer without paid calls."""
import base64
from email import policy
from email.parser import BytesParser
from io import BytesIO
from uuid import uuid4

import httpx
from PIL import Image

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import ExternalImageRequest, ImagePrompt, content_hash
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from beauty_ai.prompt_policy import WATERMARK_POLICY_TEXT


def test_korean_prompt_roundtrip_and_edit_transport(tmp_path):
    text = "밝은 코랄 배경. 제품의 무광 재질과 뚜껑 비율을 유지한다.\n라벨은 원본 그대로 둔다."
    prompt = ImagePrompt(positive=text, language="ko", prompt_version="team-v02", source_revision=1)
    restored = ImagePrompt.model_validate_json(prompt.model_dump_json())
    assert restored.positive == text and restored.language == "ko"
    assert content_hash(restored) == content_hash(prompt)
    image = BytesIO()
    Image.new("RGB", (1152, 1440), "coral").save(image, "PNG")
    data = image.getvalue()
    assets = LocalAssetStore(tmp_path)
    ref = assets.put_upload(data, project_id=uuid4())
    request = ExternalImageRequest(operation="edit", pipeline_revision="2",
                                   product_images=[ref], prompt=prompt, watermark_policy="exclude-v1")
    sent = []

    def handler(req):
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + req.headers["content-type"].encode() + b"\r\n\r\n" + req.read())
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                  for part in message.iter_parts()}
        sent.append(fields["prompt"].decode("utf-8"))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(data).decode()}]})

    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), transport=httpx.MockTransport(handler))
    adapter.execute(request, [data], [], client_request_id=uuid4())
    assert len(sent) == 1 and text in sent[0]
    assert WATERMARK_POLICY_TEXT in sent[0]


def test_legacy_english_hash_is_unchanged():
    body = {"positive": "Keep the matte bottle.", "negative": "", "prompt_version": "old-v1",
            "source_revision": 1, "language": "en"}
    assert ImagePrompt.model_validate(body).model_dump(mode="json") == body
    assert content_hash(ImagePrompt.model_validate(body)) == content_hash(body)
