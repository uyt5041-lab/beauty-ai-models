# AD-WORK-COSTS 검증 중 수정 · 2026-11-03
- 설치SDK LegacyAPIResponse accessor를 확인해 http_response.json()으로 수정했다. 실제SDK+MockTransport malformed output에서 usage 보존을 시험했다.
- 영수증 복구 후 같은 텍스트 요청의 중복전송을 atomic strict 등록으로 막았다. external receipt API 기본 계약은 유지한다.
- local receipt 사전검증으로 불량입력이 PENDING을 남기지 않게 했다(4종 시험). 동시 inbox 소비의 FileNotFound는 안전히 처리했다(결정적2-thread 시험).
- 신규 이미지 fixture의 ImagePrompt 필수값 누락1FAIL을 수정했다. 기록 파일의 backend 디렉터리가 없어 첫 저장도 실패해 디렉터리를 만들고 재실행했다. 실패한 commit은 없었다.
- 최종 관련검증315PASS. [단계별 기록](../../../work/ad-work-costs-20261103.md). 실제 유료 모델 호출0.
