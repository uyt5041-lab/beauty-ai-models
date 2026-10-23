# 화장품 광고 프롬프트 사용 안내

2026-10-23 · B11. 화장품 제품 사진을 기준으로 광고 장면을 만든다. 이전의 꽃은 별도 제품 주제였으므로 꽃을 반드시 넣으라는 조건만 제거했다. 꽃 사용 자체는 금지하지 않으며 명시한 연출이나 참고 이미지에 따라 사용할 수 있다.

## 빠른 확인

프로젝트 루트에서 다음 명령을 실행한다. 세 프리셋의 프롬프트와 제공자 전달 문장, 검토표만 내보낸다. API 키를 읽거나 네트워크를 사용하지 않으며 이미지를 생성하지 않는다.

```bash
python -m beauty_ai.cosmetics_prompt_demo --output outputs/cosmetics-prompts
```

입력 예시는 [cosmetics_brief.json](../examples/cosmetics_brief.json)이다. 프리셋은 제품의 종류를 제한하는 분류가 아니라 광고의 연출 선택이다.

입력 파일을 바꿔 비교하려면 `--brief examples/cosmetics_brief.json`을 함께 지정한다. 새 run 폴더의 각 프리셋 아래에 `brief.json`, `image-prompt.json`, `request-template.json`, `provider-prompt.txt`가 생긴다. 누끼 요청/지시와 검토 기준은 run 루트에 저장한다. 요청 템플릿의 `AssetRef`는 가짜 예시이므로, 실제 실행 전 업로드한 제품 자산으로 바꿔야 한다. 그때 요청 hash도 달라진다.

| preset_id | 연출 방향 | 활용 예 |
| --- | --- | --- |
| `clean_studio` | 깨끗한 스튜디오 배경과 부드러운 조명 | 용기와 라벨이 주인공인 기본 상품 광고 |
| `premium_minimal` | 절제된 색과 정돈된 공간 | 고급스러운 화장품 브랜드 이미지 |
| `vivid_pop` | 선명한 색면과 또렷한 제품 대비 | 눈에 띄는 밝은 화장품 광고 |

| layout_id | 제품과 문구 영역 |
| --- | --- |
| `product_right` | 제품 오른쪽, 문구 여백 왼쪽 |
| `product_left` | 제품 왼쪽, 문구 여백 오른쪽 |
| `product_center_bottom` | 제품 중앙 아래, 문구 여백 위쪽 |

배치는 생성 모델에 전달하는 지시다. 실제 여백·라벨·비율은 결과에서 확인하며 최종 한글 문구는 렌더러가 별도로 합성한다.

## 입력과 호출

`CosmeticsPromptBrief`는 확인된 영어 `product_description`, `preset_id`, `layout_id`, 배경 강조색인 `brand_colors`(HEX 최대3개), 선택 영어 `creative_direction`, `source_revision`을 받는다. 설명이나 연출에서 확인되지 않은 성분·효능을 만들지 않는다.

```python
import json
from pathlib import Path
from beauty_ai.cosmetics_prompts import CosmeticsPromptBrief, build_cosmetics_prompt

brief = CosmeticsPromptBrief.model_validate_json(
    Path("examples/cosmetics_brief.json").read_text(encoding="utf-8")
)
prompt = build_cosmetics_prompt(brief)
print(json.dumps(prompt.model_dump(mode="json"), ensure_ascii=False, indent=2))
```

- `build_cosmetics_prompt(brief)`는 `ImagePrompt`를 만든다. 제품 보존 조건 → 배경·조명 → 배치·문구 여백 → 금지 변경 순서로 조립한다.
- `build_cosmetics_request(brief, product_images=[...], reference_images=[...], operation="edit")`는 실제 자산 저장소에서 받은 `AssetRef`로 편집 요청을 만든다. 반환된 요청을 제출하는 단계는 별도다.
- `build_cosmetics_cutout_request(product_image)`는 한 제품 자산의 누끼 요청을 만든다. 요청의 `prompt`는 `null`이고 래퍼가 고정된 누끼 지시를 사용한다.
- 제품 사진이 없는 `generate`는 가상 콘셉트용이다. 실제 상품의 형태·라벨을 보존해야 하는 광고에는 `edit`와 제품 사진을 사용한다.

새 빌더는 `pipeline_revision="2"`를 사용하며 생성/편집은4:5, 기본1152×1440이다. 기존 요청의 기본 revision1과 제공자 지시는 유지한다. 프리셋·배치·색·연출·원본 revision을 바꾸면 프롬프트 또는 요청 hash도 달라진다. 저장된 요청을 다른 지시로 재해석하지 않는다.

## 제품 보존과 담당 경계

- 제품 사진1번을 대표 시점으로 사용한다. 나머지 제품 사진은 같은 제품의 형태·라벨·세부 확인 자료다. 배열 순서도 요청의 일부다.
- 배경 참고는 배치·조명·색을 설명한다. 참고 사진의 다른 제품·로고·광고 문구를 새로 복사하지 않는다.
- 브랜드색은 배경과 강조색에만 사용한다. 원본 용기·뚜껑·내용물 색을 바꾸지 않는다.
- 기본 프리셋은 꽃을 필수 요소로 지정하지 않는다. `creative_direction`이나 참고 이미지에서 요청한 꽃 연출은 사용할 수 있다. 원래 패키지나 라벨의 인쇄 그림도 제품의 일부로 보존한다.
- 새 광고 문구를 이미지에 생성하지 않는다. 기존 라벨·로고의 글자는 보존한다. 단순히 “모든 텍스트 제거”라고 지시하지 않는다.
- 누끼는 원래 각도·형태·라벨·펌프 등 미세부품과 투명 alpha를 요청한다. 테두리 번짐·구멍·반투명 부분·잘림은 사람이 확인한다.

김다혜는 제품 사실 확인을 거친 영어 연출 지시와 카피를 제공한다. pi의 빌더는 이를 검증·조립하고 이미지 API에 맞춰 전달한다. 새 LLM 호출·번역·카피 생성을 대신하지 않으며 입력 문장이 실제로 영어인지 자동 판정하지 않는다.

## 결과를 고르는 기준

같은 실제 제품으로 프리셋을 비교할 때 제품 형태·색·라벨, 누끼 경계, 제품 개수, 문구 여백, 최종4:5를 각각 확인한다. 프롬프트를 포함했다는 사실은 결과 품질 합격이 아니며 `needs_manual_check=true`를 유지한다. 생성 API 성공, 자동 검사, 사용자 이미지 승인도 서로 다른 상태다.

B10 이후2026-10-22에는 사용자 승인으로 MidProject에 있던 키를 사용해 생성·편집·누끼3회를 실행했다. 모두 기술 실행은 성공했으나 누끼 테두리와 제품 재구성 문제가 있어 시각 품질은 `NOT_APPROVED`다. 해당 키가 현재 팀 지급 키인지는 확인하지 않았고 실제 청구액도 미확인이다. B11의 프롬프트 변경은 오프라인 검증 대상으로, 이3회 결과를 새 프롬프트 품질 검증으로 재사용하지 않는다.

이 문서의 B11은 오프라인 프롬프트 도입 기록이다. 후속 B12에서는 Langfuse 프리셋3개와 실행 기록 연결을 구현하고 실제 편집3회 및 원격 readback을 확인했다. [관리 안내](langfusePromptManagement.md), [B12 검증](../fivecircles/test/cosmetics-langfuse-b12.md)을 참고한다. promptfoo와 유료 자동 평가는 도입하지 않았다.

2026-10-23 B13은 밝은 컬러의 Langfuse 버전2에서 배경과 제품의 조명을 나누고 원본 무광 재질 보존을 보강했다. [v2 템플릿](../prompts/cosmetics/vivid-pop-v2.json)은 원격 버전을 재현할 수 있는 저장본이다. 기존 로컬 `cosmetics-v1` 빌더는 최초 도입 버전을 유지하며, 운영 프롬프트는 Langfuse에서 조회해 고정한다. 버전2는 이번 라벤더 병/코럴 펌프 원본을 위한 지시다. 다른 제품에서는 부위 표현을 그 원본에 맞게 검토하며, 모든 제품을 일괄 무광으로 바꾸지 않는다. [실제1회 결과](../fivecircles/test/cosmetics-matte-b13.md).

## 참고 자료

2026-10-23 확인한 아래 구조를 참고해 화장품용 문장을 직접 작성했다. 원문 전체나 저장소를 런타임에 복사하지 않는다.

- [OpenAI 이미지 프롬프트 가이드](https://developers.openai.com/api/docs/guides/image-prompting): 참조 이미지 편집·제품 보존·투명 누끼와 결과 확인.
- [Agency Agents Image Prompt Engineer](https://github.com/msitarzewski/agency-agents/blob/main/design/design-image-prompt-engineer.md): 제품·배경·조명·구도·브랜드별 지시 구성.
- [AI Product Photography Prompts](https://github.com/JeremyGDM/awesome-ai-product-photography-prompts): 상품컷·광고 여백·별도 문구 합성.
- [B11 계약](../fivecircles/architecture/contract/cosmetics-prompts.md): 필드·버전·호환성·검증 범위.
