# B11 프롬프트 기대값 수정

2026-10-23 · 발생/기록일. 저장소BeautyAI · plan/models · 기록: 현재 작업 에이전트.

- 사용자 정정: 꽃을 기본으로 넣으라는 요구만 제거하며 꽃 금지 조건을 만들지 않는다.
- 증상: 관련60개 검증 중 편집/참고이미지 생성의 multipart2개가 실패했다. MockTransport 안의 오래된 문장 기대값이 AssertionError를 내고 adapter의 보수적 예외 처리에서 PROVIDER_SUBMISSION_UNKNOWN으로 변환됐다.
- 원인: 제품코드에서는 꽃 금지 문장을 제거했으나 테스트가 과거 배경참조 문장을 기대했다. 실제 네트워크/제공자 실패가 아니다.
- 수정: 새 문장 `Do not copy their products, text or logos.`에 맞추고, 기본 프리셋에 꽃 강제/금지 없음과 명시적 꽃 연출 보존을 별도 검증했다.
- 재검증: `tests/test_cosmetics_prompts.py` 58 passed. 전체회귀는 [B11 기록](../cosmetics-prompts-b11.md)에 별도 기록한다.
- 재발방지: “기본 요구 제거”를 “전역 금지”로 확대하지 않는다. 제품범위와 연출 선택은 구분한다. 실제API호출0회.
