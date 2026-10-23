# 화장품 프롬프트 적용 계약 · B11

2026-10-23 · 구현 전 계약. 사용자 정정: 꽃은 이전 제품 주제였으며 화장품 광고에 꽃 장식을 추가하라는 뜻이 아니었다.

## 범위와 입출력

- pi는 확인된 영어 제품 설명과 연출 지시를 결정적인 템플릿에 조립한다. LLM 호출·번역·새 효능/성분 생성은 하지 않는다. 김다혜의 프롬프트 생성 책임은 유지한다.
- `CosmeticsPromptBrief`: `product_description`, `preset_id`(clean_studio/premium_minimal/vivid_pop), `layout_id`(product_right/product_left/product_center_bottom), `brand_colors`(HEX 최대3개), `creative_direction`(선택 영어 지시), `source_revision`.
- `build_cosmetics_prompt(brief) -> ImagePrompt`: 제품 보존 → 연출 → 배치/빈 문구 영역 → 금지 변경. 한글 광고 문구는 따로 렌더링하며 제품 사진 속 실제 라벨/로고는 보존한다. 기본 프리셋에 꽃을 넣으라는 요구는 없다. 꽃이나 식물 자체를 금지하지 않으며 명시한 연출 지시는 정상 전달한다.
- `build_cosmetics_request(brief, product_images=[...], reference_images=[...], operation="edit") -> ExternalImageRequest`: revision=2, 기본1152×1440. generate는 제품사진 없는 가상 콘셉트용이며 실제 제품 광고는 edit를 사용한다.
- `build_cosmetics_cutout_request(product_image) -> ExternalImageRequest`: revision=2의 고정 누끼 지시, prompt=null. 원본 각도/형태/라벨/미세부품과 깨끗한alpha를 요청한다. 원본 픽셀이나 품질 보증은 아니다.
- `python -m beauty_ai.cosmetics_prompt_demo`: 세 프리셋의 프롬프트/제공자 전달 문장/검토표를 내보내는 오프라인 예제. 키 조회·HTTP·이미지 생성 없음.

예: 확인된 보라색 세럼 용기 설명 + vivid_pop + product_right → 우측 단일 제품/좌측35% 문구 여백/선명한 색면/화장품 광고 지시와 버전이 붙은 ImagePrompt.

## 호환성과 전달

- 기존 ExternalImageRequest 기본 revision=1과 v1 제공자 프롬프트를 그대로 유지한다. 저장된 작업 hash/의미를 바꾸지 않는다. 명시한 revision=2만 새 규칙을 적용한다.
- revision2 generate/edit는4:5다. cutout은 원본 제품을 자르지 않고 기존 크기 계약을 따른다. 예제는4:5를 사용한다.
- 프리셋/레이아웃/색/연출/원본revision은 본문 또는 prompt_version에 들어가 요청 hash에 반영된다. 제공자 공통 규칙 변경은 pipeline revision, 템플릿 변경은 prompt version을 올린다.
- 첫 제품 사진이 대표 시점이며 뒤 사진은 같은 제품의 세부 확인 자료다. 배경 참고에서 다른 제품/로고/문구를 복사하지 않는다. 브랜드색은 배경 accent에만 사용하고 제품 자체 색은 보존한다.
- 기본 꽃 장식 요구를 제거했으며 금지 규칙은 두지 않는다. 제품에 이미 인쇄된 그림과 명시적 연출은 보존한다. 프롬프트 준수를 자동 보증하지 않고 수동 검토 상태를 유지한다.
- 키·live 차단·예산 원장·오류 처리·수동 검토 계약은 유지한다. Langfuse/promptfoo 설치와 유료 자동 평가 루프는 이번 범위 밖이다.

## 검증

v1 문자열/hash/저장 호환성, revision2 전송 JSON/multipart와 투명PNG, 실제 파일 순서, 입력/4:5 검증, 각 지시 변경의 hash 변화, 네트워크 없는CLI, 전체 회귀·설정 검사·로컬beautyAi 동기화를 확인한다. 실제품 품질/팀 인수는 별도 미완료다.

## 출처

- [OpenAI Image prompting](https://developers.openai.com/api/docs/guides/image-prompting): 참조 편집·제품 보존·투명 누끼·출력 점검.
- [Agency Agents Image Prompt Engineer](https://github.com/msitarzewski/agency-agents/blob/main/design/design-image-prompt-engineer.md): 제품/배경/조명/구도/브랜드 지시 구조.
- [AI Product Photography Prompts](https://github.com/JeremyGDM/awesome-ai-product-photography-prompts): 상품컷·광고 여백·별도 문구 합성.

2026-10-23 확인한 지침의 구조를 참고해 화장품 템플릿을 직접 작성한다. 외부 저장소 전체를 런타임에 설치하거나 새 모델을 도입하지 않는다.
