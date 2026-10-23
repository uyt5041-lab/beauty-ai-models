# B11 화장품 프롬프트 검증

2026-10-23 · plan/models. [구현 전 계약](../architecture/contract/cosmetics-prompts.md), [사용 가이드](../../docs/cosmeticsPromptGuide.md).

## 범위

클린 스튜디오·고급 미니멀·밝은 컬러 광고의3프리셋과3배치, 제품색/라벨/기존패키지그림 보존 지시, 기본 꽃 장식 요구 제거(꽃 금지 규칙 없음), revision2 고정 누끼 지시를 적용했다. 다혜의 영어 연출을 결정적으로 조립하며 LLM 생성기를 추가하지 않는다. 기존revision1 본문/해시/전송 지시는 유지한다.

## 확인 기록

- 초기 관련회귀: `tests/test_cosmetics_prompt_demo.py tests/test_external_image_contracts.py tests/test_openai_images.py` **126 passed**.
- 독립 신규검증: `tests/test_cosmetics_prompts.py` **58 passed**. v1고정본문/hash·revision2 JSON/multipart·파일순서·입력경계·4:5·해시변경·수동검토 유지·불투명누끼거절과 명시적인 꽃 연출 보존을 포함한다.
- CLI: 세 프리셋과 고정 누끼의 제공자 전달문장/요청템플릿을 파일로 내보냈다. `PROMPT_PREVIEW_ONLY`, 실제호출0·생성이미지0·품질NOT_ASSESSED.
- 전체회귀: `.venv/bin/python -m pytest -q -W error` **866 passed in 13.90s**. 로컬beautyAi 신규60 tests **60 passed in 0.38s**. 설정 검사·문서 링크·README5절·`git diff --check` PASS. 변경파일의 로컬/저장소 byte parity 확인.
- 독립 코드 검토: v1호환·제품색/라벨 보존·오프라인 경계를 확인했다. 사용자 정정 뒤 기본 꽃 요구/금지 모두 제거했고 명시적 꽃 연출은 전달되는 테스트를 통과했다.
- 꽃 금지 제거 과정의 오래된 테스트 기대값2개 실패는 수정 후 통과했다. [원인과 재검증](errorlogs/2026-10-23-cosmetics-prompts-b11.md).

## 한계

실제 화장품 이미지의 개선 여부는 이번에 평가하지 않았다. 팀 지급키·잔액·실청구·서비스UI/렌더러 인수는 미확인이다. 프롬프트 제약은 모델의 픽셀보존이나 품질합격을 보증하지 않는다. Langfuse/promptfoo를 설치하거나 유료평가를 실행하지 않았다.

현재요청: 확인된영어브리프 → 프리셋조립 → ImagePrompt/해시 → revision2 제공자규칙 → 기존 OpenAI어댑터/자산검사 → 수동검토. 같은 경로를 mock으로 검증했다. 실제품 품질/최종서비스 연결은 PARTIAL이다.
