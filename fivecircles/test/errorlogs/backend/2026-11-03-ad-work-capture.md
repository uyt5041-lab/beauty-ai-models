# AD-WORK-COSTS 검증 중 수정 · 2026-11-03
- 설치SDK LegacyAPIResponse accessor를 확인해 http_response.json()으로 수정했다. 실제SDK+MockTransport malformed output에서 usage 보존을 시험했다.
- 영수증 복구 후 같은 텍스트 요청의 중복전송을 atomic strict 등록으로 막았다. external receipt API 기본 계약은 유지한다.
- local receipt 사전검증으로 불량입력이 PENDING을 남기지 않게 했다(4종 시험). 동시 inbox 소비의 FileNotFound는 안전히 처리했다(결정적2-thread 시험).
- 신규 이미지 fixture의 ImagePrompt 필수값 누락1FAIL을 수정했다. 기록 파일의 backend 디렉터리가 없어 첫 저장도 실패해 디렉터리를 만들고 재실행했다. 실패한 commit은 없었다.
- 최종 관련검증315PASS. [단계별 기록](../../../work/ad-work-costs-20261103.md). 실제 유료 모델 호출0.

W3: W&B validator가 명시 금액/비교 자격 변조를 허용하던 두 지적을 수정했다. 공통 비교 helper+receipt subtotal/단가 근거 검증을 재사용하고 변조 회귀를 추가했다.
W3 UI:390px에서 새비교표가846px로 넘침. 해당section의 최소폭/3열줄바꿈만 수정해390px로 재검증했다. 구형 보고서 바이트는 유지했다.
