# BeautyAI 릴레이 실행 기록

## B1 · 입력 자산과 제품 캔버스

0. Scope Intake: native, plan/models. 이미지 TODO 1-A/2-B/2-D. 날짜는 대기 조건이 아님. 자산·캔버스 파일별 병렬 작업, 조정/검증/Git은 root.
1. Start Report: 목표/현재 흐름과 점수는 `../architecture/spec/image-runtime-flow.md`. 최초 상태는 클라이언트만 구현, 자산/서버/GPU/인수 GAP.
2. Relay Unit Selection: 입력 경계 4+3+2+2-1=10, job ledger 3+3+2+2-1=9, 모델 실행 2+3+1+1-3=4. 첫 상류 입력 경계를 선택.
3. Doc / Contract: `../architecture/contract/image-runtime.md`, COMPLETED.
4. Implementation: 자산 저장/검증·캔버스 배치·제품 보존 검사, COMPLETED.
5. Validation: 72 tests PASS + CLI 파일/DB smoke PASS + 구조도 브라우저 렌더 PASS.
6. Repair: Pillow deprecated 테스트와 CLI 실행 진입점을 수정·재검증.
7. Push / Publication: 릴레이샷 요청 범위에서 plan/models 일반 push, PENDING. 운영 CD는 로컬 Python 라이브러리 범위라 NOT_APPLICABLE.
8. Closeout Report: [B1 검증](../test/relay-b1.md), 전체 흐름 GAP/PARTIAL.
9. Relay: B1 검증/반영 후 영속 job/API B2를 재평가한다. 아직 다음 배치를 실행하지 않음.
10. Final Ledger: PENDING. 실모델/팀 인수는 별도 GAP.
