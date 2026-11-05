"""Local export of a server-resolved candidate. This is not an approval API.

Freeform already contains model-drawn copy: do not overlay it again. Serving
must review and bind the exported file hash before final download approval.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import warnings

from PIL import Image

from .ad_resize import resize_ad_png
from .backend_adapter import BackendGeneratedImage
from .composition import (ComposedAd, CompositionInput, CompositionLayout,
                          compose_ad_png, validate_copy_output)
from .errors import GatewayError


def export_candidate(candidate: BackendGeneratedImage, *, copy_output: dict,
                     layout: CompositionLayout | None = None,
                     font_path: str | Path | None = None) -> ComposedAd:
    """Export one trusted candidate without provider calls or state writes.

    Caller owns project/approval checks. Missing legacy mode means composed;
    modes on current candidates are recovered from the durable request receipt.
    Generated source bytes are retained by the caller for local export repair.
    """
    mode = candidate.receipt.get("generation_mode", "composed")
    if candidate.receipt.get("state") != "succeeded" or mode not in {"freeform", "composed"}:
        raise GatewayError("CANDIDATE_NOT_EXPORTABLE", "확정 성공한 후보와 방식을 확인해 주세요.")
    if sha256(candidate.image_bytes).hexdigest() != candidate.sha256:
        raise GatewayError("COMPOSITION_IMAGE_HASH_MISMATCH", "후보 이미지 해시를 확인해 주세요.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(candidate.image_bytes)) as image:
                if (candidate.mime_type != "image/png" or image.format != "PNG"
                        or image.size != (candidate.width, candidate.height)):
                    raise ValueError
    except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise GatewayError("CANDIDATE_IMAGE_METADATA_INVALID", "후보 이미지의 실제 형식과 크기를 확인해 주세요.") from None
    snapshot = deepcopy(copy_output)
    _, caption, tags = validate_copy_output(snapshot, candidate.copy_hash)
    if mode == "composed":
        if layout is None or font_path is None:
            raise GatewayError("CANDIDATE_COMPOSITION_REQUIRED", "합성 배치와 서버 폰트를 지정해 주세요.")
        result = compose_ad_png(CompositionInput(
            job_id=candidate.job_id, image_bytes=candidate.image_bytes,
            image_sha256=candidate.sha256, copy_revision=candidate.copy_revision,
            copy_hash=candidate.copy_hash, copy_output=snapshot, layout=layout), font_path=font_path)
        return replace(result, receipt={**result.receipt, "generation_mode": "composed"})
    if layout is not None or font_path is not None:
        raise GatewayError("CANDIDATE_FREEFORM_CONFIG_INVALID", "자유형에는 글자 합성 배치나 폰트를 지정하지 않습니다.")
    if candidate.width * 4 == candidate.height * 3:
        final_size = (1080, 1440)
    elif candidate.width * 5 == candidate.height * 4:
        final_size = (1080, 1350)
    elif candidate.width * 16 == candidate.height * 9:
        final_size = (1080, 1920)
    else:
        raise GatewayError("CANDIDATE_ASPECT_UNSUPPORTED", "지원하는 광고 비율을 확인해 주세요.")
    resized = resize_ad_png(candidate.image_bytes, final_size=final_size)
    caption_text = caption + ("\n\n" if caption and tags else "") + " ".join(tags)
    receipt = {"generation_mode": "freeform", "rendering": "provider_drawn_text",
               "provider_calls": 0, "protected_region_check": False,
               "needs_manual_check": True, "source_image_sha256": candidate.sha256,
               "image_sha256": resized.sha256, "copy_hash": candidate.copy_hash,
               "copy_revision": candidate.copy_revision, "job_id": candidate.job_id,
               "resize_method": resized.method, "warnings": ["GENERATED_COPY_REQUIRES_REVIEW"]}
    return ComposedAd(resized.image_bytes, caption_text.encode("utf-8"), caption, tuple(tags),
                      resized.mime_type, resized.width, resized.height, resized.sha256,
                      candidate.sha256, candidate.copy_hash, candidate.copy_revision,
                      candidate.job_id, receipt)
