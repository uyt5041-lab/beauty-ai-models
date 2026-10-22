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
7. Push / Publication: a1b1602f52834191dca6730600c6ac475c106b00 일반push/원격SHA일치. 운영CD NOT_APPLICABLE.
8. Closeout Report: B4 HTML/MD/PNG/browser PASS. 실모델·팀renderer·실제품질 GAP 유지.
9. Relay: CONTINUE_WITH_NEXT_FORM. B5 고정 평가 입력·측정 도구, 점수6.
10. Final Ledger: Doc/Implementation/Validation/Repair/Push/Report 완료. 실제GPU/품질/인수 GAP.

## B5 · 평가 manifest와 측정 증거 도구

0. Scope Intake: native, plan/models. metrics와 manifest/CLI를 파일별 위임, root는 job timing/API·통합검증·Git.
1. Start Report: B4 flow 기준 I 평가·인수 연결 PARTIAL.
2. Relay Unit Selection: 2+2+1+2-1=6. 실모델/권리있는평가셋 없이도 준비할 수 있는 측정 도구.
3. Doc / Contract: image-runtime B5 COMPLETED. 자동 품질승인 금지, UNKNOWN과0구분.
4. Implementation: COMPLETED. 로컬 평가manifest/CLI·마스크metric·job timing/API/CLI.
5. Validation: 전체280 tests PASS4.06s, 실제예제manifest CLI1건 NOT_ASSESSED, 실제TCP timingCLI PASS70.61ms(server wallclock), wheel/isolatedimport PASS.
6. Repair: 동료 검토에서 발견한 FIFO manifest 입력 대기를 nonblocking open/fstat로 수정. 실제 FIFO 회귀와 전체 검사 PASS.
7. Push / Publication: c0c1240508c253872ee8cc480e2bf19d256ccaa2 일반push와원격SHA일치. 운영CD NOT_APPLICABLE(오프라인평가도구와로컬참조API).
8. Closeout Report: B5 HTML/PNG/browser PASS. 실제GPU·품질평가·팀인수 GAP.
9. Relay: 사용자 로컬 선구현·후속 GCP 이식 방향에 따라 B6 로컬 모델·고정 revision·이식 가능한 adapter를 검토. B5 publication 확인 뒤 다음 폼 작성.
10. Final Ledger: Doc/Implementation/Validation/Repair/Report/Push 완료. 실제모델준비는 다음배치.

## B6 · 로컬 BiRefNet Lite 실누끼

0. Scope Intake: native, plan/models. 사용자 로컬선구현/후GCP이식 방향. root model store/runtime/실행검증/Git, adapter와upstream검토분리.
1. Start Report: B5 flow C 실누끼GAP, Mac64GB/MPS준비가능. GCP인증대기대신로컬경계로진행.
2. Relay Unit Selection: 3+3+2+2-2=8. 고정모델/의존성/명시장치/실누끼를기존승인흐름에연결.
3. Doc / Contract: image-runtime B6 COMPLETED. source/hash검토후에만고정로컬코드실행.
4. Implementation: COMPLETED. 고정모델저장/해시검증, 선택의존성, 실제누끼adapter, runtime/CLI.
5. Validation: 전체339tests4.30s PASS, offline실제MPS/TCPCLI합성fixture PASS(첫job6897.11ms), 실제파일과평가hash연결, wheel/pipcheck/소스검토PASS.
6. Repair: 다운로드HTTPException을안전코드로변환하고정리회귀추가. adapter상호검토/실제실행PASS.
7. Push / Publication: a7f3957664eeb98ef76d2352c566c47f2a733ced 일반push/원격SHA일치. 로컬코드/환경/가중치검증PASS, GCP배포NOT_RUN.
8. Closeout Report: B6 flow/HTML/PNG/browser PASS. 실제제품사진품질/배경실모델/공동인수GAP유지.
9. Relay: 다음실배경모델은누끼배치검증/전달뒤선정.
10. Final Ledger: Doc/Implementation/Validation/Repair/Report/Push 완료. CONTINUE_WITH_NEXT_FORM B7.

## B7 · 로컬 SDXL 배경 인페인트

0. Scope Intake: native,plan/models. 사용자로컬선구현방향, base모델만/고정18runtime파일약6.94GB+라이선스기록. 별도refiner/전체repo다운로드없음.
1. Start Report: B6flow F실배경GAP, E승인/C실누끼/worker/자산/평가연결완료범위사용.
2. Relay Unit Selection: 2+3+2+2-2=7. 고정SDXL/실제영문prompt/보호마스크인페인트/최종원제품재합성.
3. Doc / Contract: image-runtime B7 COMPLETED. 실제인페인트지원/tokenizer/VAE/safety조건공식구현검토.
4. Implementation: COMPLETED. 모델준비와adapter파일분리, root runtime/CLI/실제실행통합.
5. Validation: 전체433tests9.39s, 실제MPS/TCPCLI512x512 PASS, opaque770/mismatch0/SafeZone정확/다운로드hash일치. 배경14700.575ms는load/queue/storage포함. wheel/격리import/flow브라우저 PASS.
6. Repair: 전부검정원출력저장차단. 실라이브러리에서제거된pipeline.enable_vae_tiling호출을vae.enable_tiling으로수정하고실API회귀2개추가. 최초2실패증거보존후세번째실행PASS. 꽃부족/제품잔상은광고품질미완료로기록.
7. Push / Publication: b71fadcaf22bebd67c9089c67d452e449d3596ea 일반push/원격SHA일치. 로컬141파일동일/433tests19.17s/모델파일검증PASS. GCP배포NOT_RUN.
8. Closeout Report: B7 HTML/PNG/browser(Task Space143) PASS, GAP/PARTIAL. 실사진광고품질/팀renderer/사용자인수GAP유지.
9. Relay: CONTINUE_WITH_NEXT_FORM. 동일job ID 제출응답조회·조정, 9점.
10. Final Ledger: Doc/Implementation/Validation/Repair/Report/Push 완료. 아래 B8 폼.

## B8 · 불확실 제출의 같은 작업 조회

0. Scope Intake: native,plan/models. 기존 TODO4-A 응답유실/SubmissionUncertain 조정. gateway와통합검증을분리하고root가CLI/문서/Git을연결.
1. Start Report: B7최종flow/receipt를사용. 상류B 제출응답유실경계PARTIAL.
2. Relay Unit Selection: 3+3+2+2-1=9. 자동재제출없이같은ID/hash확인으로중복생성방지.
3. Doc / Contract: image-runtime B8 COMPLETED. 기존submit/get/wait계약유지, 별도bounded orchestration함수와CLI연결.
4. Implementation: COMPLETED. gateway submit_and_wait·CLI prepare/render 연결.
5. Validation: 전체521tests15.01s. 신규84unit·실제TCP4. commit뒤응답유실주입→same-ID복구, PUT1회/processor1회, 파일hash/프로젝트권한/기한후worker계속실행PASS. wheel/격리importPASS.
6. Repair: 실패error_code원문노출가능경계를ASCII안정코드80자/실패상태검사로제한. 정상코드/legacy경로회귀·재검토PASS.
7. Push / Publication: 1e05f945a202ac9e53fccb4d6c7a2b721f5fcb10 일반push/원격SHA일치, 로컬145파일/521tests68.92s/설정PASS. 운영배포NOT_APPLICABLE.
8. Closeout Report: B8 HTML/PNG/browser(Task Space144) PASS, GAP/PARTIAL. 실제GCP/품질/서비스인수GAP유지.
9. Relay: CONTINUE_WITH_NEXT_FORM. 로컬측정·반복검증6점.
10. Final Ledger: Doc/Implementation/Validation/Repair/Report/Push 완료. 아래 B9 폼.

## B9 · 작업별 실행 관찰과 로컬 반복 측정

0. Scope Intake: native,plan/models. 기존TODO3-B/4-B의모델단계별측정. 관찰저장/모델계측/API·CLI·실제측정으로소유권분리.
1. Start Report: B8최종flow/receipt사용. ledger총경과만있어세부성능/재현범위미확인.
2. Relay Unit Selection: 1+2+2+2-1=6. 기존동일job/hash에관찰을연결하며측정없는값을만들지않음.
3. Doc / Contract: image-runtime B9 COMPLETED. 별도execution조회,기존wire/hash/timing보존. 단계값은monotonic wall-clock이며순수GPU시간아님.
4. Implementation: COMPLETED. ContextVar관찰,원자적execution저장,프로젝트조회API/CLI,두실모델계측.
5. Validation: 전체624tests36.32s PASS,실제MPS누끼1+배경5모두PASS.512동일요청/seed2회파일hash일치,768/1024/512x768레이어파일·불투명픽셀·SafeZone검사PASS. 단계/환경/메모리snapshot/API/CLI연결,최종wheel/격리lazyimport/pipcheckPASS.
6. Repair: 신규test시각fixture가startup recovery호출을누락해고갈→fixture시각보완. 제품코드결함아님. 상호검토추가finding없음. 실제모델upstream알림과품질잔상은검증한계로기록.
7. Push / Publication: PENDING. 소스·문서는코드/실모델/정적검증근거로승인된일반push대상. HTML미리보기file:접근거절을Git게시금지로확대하지않음. 원격조회시점은B8 1e05f945a202ac9e53fccb4d6c7a2b721f5fcb10. 최종receipt별도. GCP실행NOT_RUN.
8. Closeout Report: 코드624tests/실제MPS6jobs/package/정적문서·PNG검사PASS. 최종HTMLbrowser BLOCKED. 수정전TaskSpace145스크린샷은최종증거아님. 품질/사람인수GAP유지.
9. Relay: STOP_WITH_REASON. 현재배치최종브라우저검증미완료로후속코드배치를시작하지않음. 새로직접전달된SSOT대조·TODO문서요청은별도로작성하며릴레이마감/구현재개와구분. OOM/저장실패후다음작업복구와부분자산정리는후속후보로보존.
10. Final Ledger: Doc/Implementation/automated+actual-model Validation COMPLETED; final Browser BLOCKED; Push PENDING; Report GAP/PARTIAL; Relay STOP_WITH_REASON. 기존로컬실행·계약·자산·비밀값보호유지.

## 2026-10-22 · 별도 직접 요청: SSOT 대조·실행 TODO

- 사용자 전달 요청은 현재 구현과 확정 MVP의 차이·목표·담당·의존성·완료 기준을 문서화하는 것이다. 원문에 적힌 구현 항목을 별도 실행 승인으로 취급하지 않는다.
- 근거: `확정된 기준점 (SSOT)` 문서ID `401b13f77dbd80f39bbccd2e3a489566`, 현재 코드/원격 B8 및 로컬 B9 관찰. 원문 파일·계정정보를 공개 저장소에 복사하지 않는다.
- 범위: `docs/ssotAlignment.md`, `docs/imageTodo.md`, `docs/modelIntegrationReview.md`, 요구사항·결정·사용 안내의 연결. 기존 모델·작업 서버·승인·hash/cache 구조를 유지한다.
- 상태: 문서 작성 COMPLETED. SSOT-01~12의 차이·변경·담당·의존·완료조건을 연결했고 기존 완료35개에 실제날짜·근거를 붙였다. 문서 정적 검증 후 승인된 소스 게시와 로컬 beautyAi 동기화를 수행한다. 브라우저 차단을 우회하거나 최종 HTML 검증 PASS/다음 코드 배치 시작으로 표시하지 않는다.
