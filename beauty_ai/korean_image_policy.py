"""Versioned Korean service policy; historical English experiments stay intact."""
from typing import Literal

from .contracts import AssetRef, ExternalImageRequest, ImageProviderModel

KOREAN_POLICY_VERSION = "ko-v1"
WATERMARK_POLICY_KO = (
    "워터마크 제외 정책\n"
    "새 워터마크·스톡 사진 표시·제작자 서명·플랫폼 표시·출처 배지를 추가하지 않는다. "
    "입력 사진 위에 덧씌워진 워터마크는 결과에 복제하지 않는다. "
    "실제 제품에 인쇄된 브랜드 로고·라벨 글자·포장 그림은 워터마크가 아니므로 보존한다."
)
PRESERVE_KO = (
    "제품 보존 조건\n"
    "원본 제품의 전체 폭·길이·세로 가로 비율, 뚜껑과 본체의 비율, "
    "펌프·노즐·목 부분과 세부 모양을 유지한다. "
    "원본 색·무광 또는 유광 재질·투명도·반사 특성·로고·라벨·포장 그림을 유지한다. "
    "무광 표면을 유광으로 바꾸거나 제품을 다시 디자인하지 않는다. "
    "제품 전체가 보이게 하고 작은 부품을 자르거나 늘이지 않는다. "
    "사진에 없는 효능·성분·인증 표시·포장 정보를 만들어 넣지 않는다. "
    "색감 변경은 배경과 주변 연출에만 적용한다. "
    "광고 문구·가격·배지를 새로 그리지 않는다. 요청한 카피 영역은 별도 한글 합성을 위해 비운다."
)
CUTOUT_KO = (
    "첫 번째 화장품 사진의 배경만 제거한다. 원본 시점과 모든 보이는 부품을 유지한다. "
    "가는 펌프 경계·투명 소재·반사 소재·라벨을 보존한다. "
    "실제로 투명한 알파 채널이 있는 PNG로 반환한다. "
    "배경 잔여물·후광·색 번짐·없는 구멍을 만들지 않는다. "
    "제품을 다시 칠하거나 광택을 더하거나 재설계하지 않는다. "
    "체크무늬·단색 배경·그림자·새 소품을 그리지 않는다."
)


def render_korean_image_policy(request: ExternalImageRequest) -> str:
    """Called only for explicitly versioned requests after contract validation."""
    if request.operation == "cutout":
        return "\n\n".join((CUTOUT_KO, PRESERVE_KO, WATERMARK_POLICY_KO))
    assert request.prompt is not None
    parts = [request.prompt.positive]
    if request.prompt.negative:
        parts.append("피해야 할 내용\n" + request.prompt.negative)
    parts.extend((PRESERVE_KO, WATERMARK_POLICY_KO))
    products, references = len(request.product_images), len(request.reference_images)
    if products:
        parts.append("제품 사진의 역할\n1번 사진이 주 제품과 정면 시점의 기준이다. "
            "첫 사진의 시점과 제품 정체성을 유지하고 제품은 정확히 하나만 표현한다. "
            "나머지 사진의 제품을 별개 물체로 추가하지 않는다.")
        if products > 1:
            parts.append(f"2번부터 {products}번까지는 같은 제품의 세부 정보를 참고한다.")
    else:
        parts.append("실물 제품 사진 없음\n브랜드가 없는 화장품 콘셉트만 표현한다. "
            "라벨 문구를 만들어 넣거나 실제 제품을 충실히 재현했다고 주장하지 않는다.")
    if references:
        parts.append(f"배경 참고 사진의 역할\n{products + 1}번부터 {products + references}번은 "
            "배경 구도와 조명만 참고한다. 해당 사진의 제품·글자·로고는 복제하지 않는다. "
            "원본 제품 보존과 카피 여백을 우선한다.")
    return "\n\n".join(parts)


def build_korean_cutout_request(product_image: AssetRef, *,
        model: ImageProviderModel = "gpt-image-2.5-sunburst-2026-09-08",
        quality: Literal["low", "medium", "high"] = "medium") -> ExternalImageRequest:
    """New Korean cutout entry point; do not rewrite saved English requests."""
    return ExternalImageRequest(operation="cutout", pipeline_revision="2",
        watermark_policy="exclude-v1", cosmetics_policy_version=KOREAN_POLICY_VERSION,
        product_images=[product_image], model=model, quality=quality)
