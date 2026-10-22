# BeautyAI 릴레이 실행 기록

## B1 · 입력 자산과 제품 캔버스

0. Scope Intake: native, plan/models. 이미지 TODO 1-A/2-B/2-D. 날짜는 대기 조건이 아님. 자산·캔버스 파일별 병렬 작업, 조정/검증/Git은 root.
1. Start Report: 목표/현재 흐름과 점수는 `../architecture/spec/image-runtime-flow.md`. 최초 상태는 클라이언트만 구현, 자산/서버/GPU/인수 GAP.
2. Relay Unit Selection: 입력 경계 4+3+2+2-1=10, job ledger 3+3+2+2-1=9, 모델 실행 2+3+1+1-3=4. 첫 상류 입력 경계를 선택.
3. Doc / Contract: `../architecture/contract/image-runtime.md`, COMPLETED.
4. Implementation: 자산 저장/검증·캔버스 배치·제품 보존 검사, COMPLETED.
5. Validation: 72 tests PASS + CLI 파일/DB smoke PASS + 구조도 브라우저 렌더 PASS.
6. Repair: Pillow deprecated 테스트와 CLI 실행 진입점을 수정·재검증.
7. Push / Publication: `1bc4fea80ffb29094c676689d8a4b147a7ec5666` 일반 push와 원격 SHA 일치 확인. 운영 CD는 로컬 Python 라이브러리 범위라 NOT_APPLICABLE.
8. Closeout Report: [B1 검증](../test/relay-b1.md), 전체 흐름 GAP/PARTIAL.
9. Relay: CONTINUE_WITH_NEXT_FORM. B2 영속 job/API, 점수 10. 아래 폼으로 다음 배치를 시작한다.
10. Final Ledger: Doc/Implementation/Validation/Repair/Push/Report 완료. 실모델/팀 인수는 별도 GAP.

## B2 · 영속 작업 API와 worker

0. Scope Intake: native, plan/models. B1 파일/DB 검증을 사용한다. 계약은 image-runtime의 B2 절.
1. Start Report: B1 closeout HTML/MD를 입력으로 재사용. A→B와 B가 아직 GAP.
2. Relay Unit Selection: 4+3+2+2-1=10. 최상위 미연결 job ledger/API 선택.
3. Doc / Contract: COMPLETED. wire endpoint 유지, project별 토큰, durable claim, POSIX 실행잠금, 결과 자산 검증.
4. Implementation: ledger/worker/API 파일 분리, COMPLETED.
5. Validation: 전체 112 tests PASS. SQLite 동시 프로세스·복구·worker 경쟁·HTTP 인증/범위·실제 loopback smoke PASS.
6. Repair: validation loc의 임의 사용자 필드명 노출 제거 및 회귀 PASS. worker health에 fatal 상태 노출.
7. Push / Publication: PENDING. 로컬 서버/라이브러리 코드 publication은 Git. 운영 GPU 배포는 이 배치의 완료 기준에 포함하지 않음.
8. Closeout Report: relay-b2.md 및 B2 흐름 HTML/PNG/브라우저 검증 PASS. 전체 흐름은 실모델/승인/렌더러 GAP 유지.
9. Relay: B2 검증/반영 뒤 B3 profile/승인/processor 평가.
10. Final Ledger: PENDING.
