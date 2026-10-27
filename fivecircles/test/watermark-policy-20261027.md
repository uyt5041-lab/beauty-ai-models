# 워터마크 제외 기본 프롬프트 · 2026-10-27

- 요청: 기본 프롬프트에 항상 워터마크를 없애는 조건 반영.
- 범위: 기본 화장품 빌더의 생성·편집·누끼, Langfuse pin, 요청 스키마와 문서.
- 정책: visible watermark overlay 제거 및 신규 watermark/stock overlay/signature/platform mark 제외. 실제 제품의 인쇄 브랜드 로고·라벨·패키지 그림 보존.
- 방식: `watermark_policy=exclude-v1`을 새 요청에 고정한다. managed prompt는 정책을 조립한 문장·SHA를 고정한다. 정책 필드가 없는 과거 요청은 직렬화에서 필드를 생략하여 해시·렌더링을 유지한다. 기존 버전 본문을 덮어쓰지 않는다.
- 관련 검증: 3프리셋의 로컬/managed 지시, 누끼/가상 생성 기본값, 과거 요청 해시 보존, 정책 누락 managed snapshot 거절. 모의 전송 검증은 고정된 실제 문장을 기준으로 확인한다.
- 전체 회귀: `.venv/bin/python -m pytest -q -W error --junitxml=outputs/watermark-policy-20261027/pytest.xml` 결과 **1,326 passed / 23.40초**. JUnit 확인: failures=0, errors=0, skipped=0. 유료 이미지 API 0회. 실물 워터마크 제거 품질은 NOT_RUN.
- 최초 관련 회귀에서 legacy canvas fixture 2건과 원격 문장 exact fixture 1건이 새 정책과 맞지 않았다. legacy fixture는 정책 없음으로 명시하고, 전송 fixture는 제출 전에 고정한 문장과 바이트 일치를 확인하도록 수정했다.
- 원격 프롬프트 게시·재조회 근거는 [프롬프트 버전 기록](../../prompts/cosmetics/watermark-20261027-readback.json)에 있다. 시간·비용·기존 실험 평가를 변경하지 않는다.
