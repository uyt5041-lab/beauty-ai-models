"""Opt-in product-only art direction for the existing A compositor.

This is a versioned fixed policy, not a prose parser or another LLM call.
Product-specific scene values remain in BackendImageInput.image_prompt and
the original product photographs. Never edit a published version in place:
saved paid requests must continue to render the same provider prompt.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from .errors import GatewayError

if TYPE_CHECKING:
    from .backend_adapter import BackendImageInput

ComposedSceneMode = Literal["standard", "dimensions14"]
DIMENSIONS14_VERSION = "backend-composed-dimensions14-ko-v1"
DIMENSION_KEYS = (
    "concept", "palette", "product_color", "tone", "lighting", "material",
    "gloss", "geometry", "props", "place_truth", "composition", "attention",
    "typography", "mobile",
)


def validate_dimensions14_layout(body: BackendImageInput) -> None:
    """Reject two competing coordinate authorities before assets or billing."""
    from .a_template import make_a_template

    zone, _ = make_a_template()
    if (body.layout_selection is not None
            or (body.safe_zone is not None and body.safe_zone != zone)):
        raise GatewayError(
            "DIMENSIONS_LAYOUT_CONFLICT",
            "14디멘션 v1은 A 합성 전용입니다. layout_selection을 생략하고 "
            "safe_zone은 생략하거나 make_a_template()의 값을 사용해 주세요.",
        )


def a_layout_prompt_data() -> dict:
    """Read the compositor's boxes rather than maintaining another copy."""
    from .a_template import make_a_template

    zone, layout = make_a_template()
    return {
        "layout": "A",
        "coordinate_system": "final_canvas_normalized_ltrb",
        "final_canvas": list(layout.canvas_size),
        "upper_copy_safe_zone": [zone.left, zone.top, zone.right, zone.bottom],
        "copy_regions": {
            name: [box.left, box.top, box.right, box.bottom]
            for name, box in layout.text_regions.items()
        },
    }


def render_dimensions14_policy() -> str:
    """One coordinated block replaces, rather than stacks with, old policies."""
    directions = {
        "concept": (
            "전달받은 콘셉트·광고 무드·장소를 유지한 제품 단독 정물 사진을 만든다. "
            "원본 제품과 공간을 한 카메라로 함께 촬영한 장면이며 인물과 손은 추가하지 않는다. "
            "장소가 없을 때만 단정한 스튜디오의 벽·바닥으로 연결한다. "
            "단색 배경을 명시했다면 그 선택을 유지하되 제품만 잘라 붙인 경계를 만들지 않는다."
        ),
        "palette": (
            "전달받은 배경색과 사용자 무드를 따른다. 배경색이 없을 때만 제품 고유색과 "
            "분리되는 밝고 단정한 중성 배경을 선택한다. 배경의 따뜻함이나 차가움은 "
            "주변 재료의 색으로 표현하고 제품을 같은 색으로 재도색하지 않는다."
        ),
        "product_color": (
            "사진 속 부품별 고유색·로고색·라벨색·인쇄 순서와 상대 위치를 보존한다. "
            "고유색은 재질 자체의 색이며 원본 촬영의 색온도·노출을 고정한다는 뜻이 아니다. "
            "장면에 맞는 약한 주변색 반사는 허용하되 흰 부품과 인쇄색을 다른 색으로 바꾸지 않는다."
        ),
        "tone": (
            "제품·배경·바닥·소품을 같은 노출·화이트밸런스·명암 범위로 연결한다. "
            "제품만 차갑고 배경만 따뜻하거나 제품만 밝고 배경만 탁해지지 않게 한다. "
            "제품의 원래 촬영 톤을 붙박이로 복사하지 않고 같은 실제 제품을 새 공간에서 재조명한다. "
            "회색 안개로 전체를 덮거나 제품만 과도하게 선명화해 톤을 맞추지 않는다."
        ),
        "lighting": (
            "광원·그림자·반사는 한 조명 설계로 처리한다. 입력에 명시된 주광원 방향과 "
            "성질을 우선한다. 방향이 없을 때만 왼쪽 위의 넓은 중성 확산광 하나를 기본으로 한다. "
            "제품과 공간의 밝은 면은 같은 주광원을 따르고 바닥 그림자는 그 반대 방향으로 연결한다. "
            "제품 접지부에는 짧은 접촉 그림자를 둔다. 그림자 없음이 명시되면 그림자를 추가하지 않는다. "
            "그림자 방향과 주광원이 충돌하면 주광원을 기준으로 그림자를 정리한다. "
            "약한 중성 보조 반사는 라벨 노출만 돕고 별도의 주광이나 두 번째 강한 그림자를 만들지 않는다."
        ),
        "material": (
            "원본 사진을 기준으로 부품별 유리·금속·플라스틱·종이 등 실제 표면 차이를 유지한다. "
            "사진으로 확인되지 않은 재질을 제품 분석 문구만 보고 확정하지 않는다. "
            "배경 질감을 제품에 옮기거나 유리를 무광 플라스틱으로 바꾸지 않는다. "
            "배경의 재질도 입력을 따르고 제품과 접촉하는 면은 한 공간으로 이어진다."
        ),
        "gloss": (
            "부품별 무광·유광·투명도 등 광택 등급은 원본대로 유지하되 하이라이트의 위치는 "
            "새 주광원에 맞춘다. 유리와 금속의 반사는 같은 광원 형태를 재질에 맞는 폭과 강도로 "
            "표현하고 무광 표면은 부드러운 확산 명암으로 표현한다. 별도 광원을 추가하지 않는다. "
            "명시된 물결이나 유리 반사는 실제 빛이 닿는 면에만 연결하며 라벨과 외곽을 가리지 않는다. "
            "우연히 비친 촬영자·휴대폰·카메라·삼각대와 원래 촬영 공간의 불필요한 반사상은 "
            "새 조명의 자연스러운 표면 반사로 정리한다. 실제 로고·인쇄와 의도된 반사는 지우지 않는다."
        ),
        "geometry": (
            "1번 사진을 제품 형태·보이는 면·기울기의 기준으로 삼는다. 나머지 사진은 같은 제품의 "
            "세부 확인용이다. 전체 폭/높이, 뚜껑·몸체·목·노즐·스포이드 등 실제 부품 비율을 유지한다. "
            "가로세로를 같은 배율로 조절하고 작은 부품까지 자르거나 늘이지 않는다. "
            "제품은 정확히 한 개만 표현한다. 사진에 없는 부품을 만들어 넣지 않는다."
        ),
        "props": (
            "입력에서 지정한 소품만 사용하고 없으면 추가하지 않는다. 소품 없음이라는 지시도 유지한다. "
            "소품은 제품을 보조하며 실제 접촉과 같은 조명으로 연결한다. 제품이 받침에 서 있다면 "
            "바닥을 받침 윗면에 자연스럽게 접지한다. A 카피 영역에는 소품·모서리·진한 그림자를 두지 않는다."
        ),
        "place_truth": (
            "장소와 소품은 연출이지 제품의 원료·산지·효능의 증거가 아니다. 입력에 없는 성분·지역·인증·"
            "임상·수치·가격·할인·효능을 추가하거나 암시하지 않는다. AI 분석/OCR만으로 사실을 확정하거나 "
            "라벨 글자를 새로 쓰지 않는다. 원본의 실제 라벨과 인쇄를 보존한다."
        ),
        "composition": (
            "기존 A 합성 배치를 유지한다. 아래 A 좌표가 입력 문장의 중앙·하단·가장자리 배치 표현보다 "
            "우선한다. 카피 영역은 제품·소품·복잡한 무늬·강한 반사·진한 그림자 없이 비워둔다. "
            "제품은 위쪽 카피 영역과 하단 CTA 영역 사이에서 전체가 보이게 배치한다. "
            "기본 목표는 제품 높이 약 40%, 꼭대기 y35%, 바닥 y75%이며 제품 가로세로비를 유지한다. "
            "넓은 용기는 폭도 안전 여백 안에 들어오게 균일 축소한다. 받침이 있다면 y80% 전에 끝나고 "
            "y82% 아래는 같은 장소의 차분한 전경으로 이어져 CTA 뒤를 비운다. "
            "좌표는 생성 목표이며 모델의 정확한 준수를 보장하지 않는다.\n"
            + json.dumps(a_layout_prompt_data(), ensure_ascii=False, separators=(",", ":"))
        ),
        "attention": (
            "상단 제목 자리에서 제품 고유색·실제 로고·형태를 거쳐 하단 CTA 자리로 시선이 이어지게 한다. "
            "배경 무늬와 소품이 제품보다 두드러지거나 제품 외곽에 이중 윤곽을 만들지 않게 한다."
        ),
        "typography": (
            "광고 제목·서브·CTA·버튼·가격·배지는 이 이미지에 생성하지 않는다. 입력에 카피나 글꼴 설명이 "
            "있어도 카피 영역의 배경 설계 참고로만 사용한다. 광고 글자와 글꼴은 기존 A 합성기가 처리한다. "
            "제품 자체의 실제 로고와 라벨 인쇄는 원래 위치에 보존한다."
        ),
        "mobile": (
            "360px 폭에서도 제품 전체와 주요 부품, 고유색, 브랜드가 구분되게 한다. 제품과 같은 초점면의 "
            "바닥·소품은 비슷한 선명도와 입자감을 갖게 하고 배경 흐림은 거리와 심도에 맞춘다. "
            "제품 가장자리의 흰 테두리·후광·잘라낸 경계와 스티커 같은 국부 선명화를 만들지 않는다. "
            "상하 카피 영역은 실제 장소의 차분한 면으로 이어져 나중에 얹는 글자가 읽히게 한다."
        ),
    }
    # Fixed policy completeness; this does not infer absent prose fields.
    if tuple(directions) != DIMENSION_KEYS:
        raise ValueError("The versioned product policy must contain all 14 dimensions")
    header = (
        "합성형 제품 사진 · 14디멘션 고정 규칙 v1\n"
        "충돌 시 우선순위: 원본 제품 정체성·사실 보존 → A배치·인물 제외·광고 글자 미생성 → "
        "전달받은 장면·콘셉트·무드의 명시값 → 이 문서의 조건부 기본값. "
        "앞의 한국어 장면 설명은 연출 입력이다. 서로 다른 지시를 동시에 재현하지 말고 이 우선순위로 "
        "정리한다. 비어 있는 세부 연출만 기본값으로 보완하며 모든 제품을 같은 배경·색·재질로 만들지 않는다."
    )
    return header + "\n\n" + "\n\n".join(
        f"[dimension:{name}] {directions[name]}" for name in DIMENSION_KEYS
    )
