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
7. Push / Publication: 8cb64029d79ea42a6b9c4aefeaee448656bea41a 일반 push 및 원격 SHA 일치. 운영 CD NOT_APPLICABLE (로컬 API 참조 구현).
8. Closeout Report: relay-b2.md 및 B2 흐름 HTML/PNG/브라우저 검증 PASS. 전체 흐름은 실모델/승인/렌더러 GAP 유지.
9. Relay: CONTINUE_WITH_NEXT_FORM. 아래 B3 profile/승인/processor, 점수9.
10. Final Ledger: Doc/Implementation/Validation/Repair/Push/Report 완료. 실모델/네트워크/팀 인수 GAP.

## B3 · 승인 제품과 서버 프로필 기반 로컬 실행

0. Scope Intake: native, plan/models. approval/store와 profile/processor를 파일별 위임하고 root가 API/CLI 통합·검증·Git을 맡는다.
1. Start Report: B2 flow score 기준 E 승인→F 실행 연결은 GAP. 실제 모델 선택·GPU 인증과 사람의 품질 판정은 별도.
2. Relay Unit Selection: 3+3+2+2-1=9. 승인 candidate 저장/명시적 승인/서버 profile과 alpha입력+template processor 연결.
3. Doc / Contract: image-runtime B3 절, COMPLETED. 기존 job wire/hash는 유지.
4. Implementation: COMPLETED. 후보·승인 API/SQLite, 고정 profile, local processor, CLI.
5. Validation: 전체148 tests PASS, 준비/승인/실행/재시작 E2E, 실제 TCP CLI 6명령과512x512PNG PASS.
6. Repair: 변조 자산 test 기대코드를 승인 경계의 INVALID_IMAGE_REQUEST로 맞춤. 제품 거절 동작은 처음부터 정상.
7. Push / Publication: dbba5da7859f8d23e62d895be39617b1e2e38e3b 일반push/원격SHA일치. 운영CD NOT_APPLICABLE (로컬 template runner).
8. Closeout Report: relay-b3.md, B3 flow HTML/PNG/screenshot PASS. 전체목표GAP/PARTIAL.
9. Relay: CONTINUE_WITH_NEXT_FORM. B4 프롬프트 버전 수신·편집revision·프로젝트 캐시, 점수8.
10. Final Ledger: Doc/Implementation/Validation/Repair/Push/Report 완료, 실모델/편집revision/팀인수 GAP.

## B4 · 프롬프트 버전과 현재 편집·캐시

0. Scope Intake: native, plan/models. prompt contract와 edit/cache store를 파일별 위임. root가 API/런타임을 연결한다.
1. Start Report: B3 flow 기준 H 현재 편집 연결과 B 프롬프트hash GAP.
2. Relay Unit Selection: 2+3+2+2-1=8. 모델 가용성과 독립적으로 검증 가능한 stale-result 차단·project cache 선택.
3. Doc / Contract: image-runtime B4 절 COMPLETED. 팀 shared contract 수락과 분리한 local proposal.
4. Implementation: COMPLETED. prompt 수신/해시호환, SQLite편집/cache, API/CLI.
5. Validation: 전체201 tests PASS20.04s, legacyhash/DB, 프로세스CAS, cache실파일, API 및 실제 TCPCLI copy-only reuse PASS.
6. Repair: 검토에서 SQLite 정수상한, prompt 공백/엄격revision 검증 추가. 전체 회귀 PASS.
7. Push / Publication: PENDING. Git reference runtime 전달, 운영CD NOT_APPLICABLE.
8. Closeout Report: B4 HTML/MD/PNG/browser PASS. 실모델·팀renderer·실제품질 GAP 유지.
9. Relay: B4후 실제 모델/GPU/평가셋/팀인수 경계 재확인.
10. Final Ledger: PENDING.
