# 작업 기록

## 2026-10-22T08:48:02+09:00 · BEAUTY-FC-001 · Design / Implementation

- 요청: 공개 FiveCircles 프레임워크를 beautyAi용으로 도입.
- 선택: 공통 문서 선택 복사 후 프로젝트 내부 적용; 원본 commit `2c4cc6132d95c2355e58d76aafceb9c9e8c3b2e8`.
- Conflict: 원본 템플릿에는 이전 프로젝트 테스트/배포/담당/이력과 깨진 진입 경로가 있음.
- Winner: 현재 사용자 지시와 BeautyAI 소스/팀 TODO.
- Resolution: Python 테스트·pi/김다혜·독립 일지 브랜치로 수정, 이력/호스트 설정은 제외.
- Follow-up TODO: 10/26 공동 계약 합의는 기존 원본 TODO에서 별도 관리.
- 범위/되돌리기: [도입 계약](../architecture/contract/framework-setup.md).

## 2026-10-22T08:49:40+09:00 · BEAUTY-FC-001 · Test / Local completion

- 결과: 설정 검사·누락/변조/JSON 오류 탐지·원본 보존·양쪽 45개 일치 확인 PASS.
- 독립 검토 반영: workflow의 없는 implementation-log 경로를 worklog로 수정하고 스킬 경로를 루트 기준으로 명시했다.
- [검증 상세](../test/setup-validation.md). 모델 테스트·실모델·Git 반영·배포는 미실행.

## 2026-10-22 · B1 · 구현/검증

- 구현: assets.py, image_preparation.py, 실제 임시 파일/DB tests와 CLI.
- 검증: 72 tests + CLI/시각 보고서 PASS. 실제 GPU/팀 인수는 미검증.
- 계약/범위: [image-runtime](../architecture/contract/image-runtime.md). [B1 기록](../test/relay-b1.md).

## 2026-10-22T12:24:52+09:00 · B2 · 구현/검증

- 구현: job_ledger.py·image_worker.py·inference_api.py. 서버 소유 토큰/project 매핑, atomic ID/hash, POSIX 실행 잠금, durable queued 복구와 결과 검증.
- 검토 수정: validation 오류의 임의 loc/입력 키를 제거하고 type만 반환. processor 없음은 queued 유지. health는 모델 readiness를 주장하지 않음.
- 검증: 전체 112 tests PASS(8.52s), health API 8 tests PASS(2.31s). 실제 loopback TCP/SQLite/파일 및 다중 프로세스 종료/복구 검증.
- 범위: 단일 호스트 동기 테스트 processor. 실제 GPU·외부 배포·광고 품질·팀 인수는 미검증. [B2 기록](../test/relay-b2.md).
- 문서: README의 5개 절 유지, 이미지 TODO 로컬 신뢰성 7개 추가 체크, 목표/현재 흐름 갱신. 다음 B3 profile·승인·adapter.

## 2026-10-22 · B3 · 구현/검증

- 구현: product_approval.py·local_processor.py·inference_runtime.py, 승인 API 및 단계별 CLI. 기존 wire/hash 유지.
- 검증: root 전체 148 tests PASS(12.14s), 담당 관련 35 tests PASS(3.95s), 별도 서버 프로세스와 TCP CLI 6단계/실제PNG PASS.
- 검토/수리: 명시적 확인/hash/project/파일/geometry·등록/실행 재검증 확인. 변조 자산 테스트의 기대코드를 승인 경계에 맞춤. [B3 검증](../test/relay-b3.md).
- 범위: 기존 투명 RGBA·로컬 template이며 실제 AI 모델을 호출하지 않았다. 사람 신원/품질 인증·현재 편집 revision·GPU·렌더러·팀 인수는 남아 있다.
- 문서: README5절 유지, 이미지 TODO4개 추가 체크, 목표/현재 흐름 및 다음 B4 점수표 갱신.

## 2026-10-22 · B4 · 구현/검증

- 구현: ImagePrompt DTO/직렬화·schema, ImageEditStore의 CAS/project cache/현재 revision attach, 내부 API·CLI 연결.
- root 검증: 전체201 tests PASS(20.04s), 실제 TCP CLI prepare/approve/select/render/attach/copy-only select/get-edit에서 동일 job 재사용 PASS.
- 검토 반영: strict integer·64bit 범위·공백 prompt를 거절. 문구 내용·검수DB는 수정하지 않는 이미지 상태 저장 범위.
- 문서: README5절, 원본 TODO 로컬 항목3개 추가(누적22개), B4 흐름/다음 B5 후보 갱신. [검증](../test/relay-b4.md).


## 2026-10-22 · B5 · 구현/검증

- 구현: evaluation.py·evaluation_metrics.py, JobTiming/API/CLI. 실제 파일 지표와 권리·사람 선언·모델 미호출 상태를 구분한다.
- root 검증: 전체 280 tests PASS(4.06s, -W error), 예제 manifest CLI와 실제 TCP timing PASS. wheel build SHA-256은 B5 검증 기록에 남겼다.
- 독립 검토 수리: FIFO manifest가 대기하지 않도록 nonblocking open·fstat·실제 FIFO 회귀를 추가했다.
- 문서: README 5절, 원본 TODO 하위 3개 추가(누적 25개), 흐름/브라우저 비교 갱신. 실제 품질·GPU·팀 인수는 GAP.
- 다음: 사용자 로컬 우선 지시에 따라 모델 revision·CPU/MPS 호환성·adapter·샘플을 검증하고 이후 GCP로 이전한다. [검증](../test/relay-b5.md).


## 2026-10-22 · B6 · 구현/검증

- 구현: model_store.py·birefnet_processor.py, 선택 models 의존성, 명시적 runtime/CLI profile. 검토한 4개 파일 hash와 source revision을 고정했다.
- root 검증: 전체 339 tests PASS(4.30s), 실제 TCP+MPS forward로 합성 병 512×512 RGBA/mask 저장. RGB 유지·alpha 0..255·수동 경고·approved=false.
- 검토/수리: 모델 저장의 HTTPException을 안전한 오류로 정제하고 회귀. 실제 실행의 timm FutureWarning은 기록했으며 실패는 없었다.
- 문서: README 5절·설치/이전 안내·원본 TODO 2개 체크(누적 27개)·흐름/브라우저 비교. 실제 출력 파일은 공개 Git 제외.
- 다음: B6 게시 후 B7 실제 로컬 배경 모델 계약·검증. 실사진 품질·CUDA·팀 인수는 별도. [검증](../test/relay-b6.md).


## 2026-10-22 · B7 · 구현/검증

- 구현: sdxl_model_store.py·sdxl_processor.py, background 의존성·runtime/CLI·꽃 예제 prompt. 고정 20개 파일 hash·77 token 한도·여백 복원·제품 재합성을 적용했다.
- root 검증: 전체 433 tests PASS(9.39s), pip check·최종 wheel·격리 lazy import. 세 번째 실제 MPS 512 fixture 실행에서 제품 770 opaque/mismatch 0·Safe Zone exact.
- 수리: 전부0 원출력 거절. 실제 실패 2회의 원인인 제거된 VAE tiling API와 대체 backend 테스트를 수리하고 관련 44 tests PASS.
- 기록: background ledger 14700.575ms/CLI 15.343s는 로딩·대기·파일 포함. MPS 메모리는 작업 후 snapshot이며 peak 아님.
- 문서: README 5절·설치 안내·원본 TODO 2개 체크(누적 29개)·흐름/브라우저 비교. 꽃 표현 부족·제품 잔상으로 광고 품질 미평가.
- 다음: 로컬 반복·크기·단계 trace·응답 유실 조회/조정 후보가 남아 있다. 실사진·CUDA·팀 인수는 별도. [검증](../test/relay-b7.md).


## 2026-10-22 · B8 · 구현/검증

- 구현: HTTPImageGateway.submit_and_wait·CLI. 요청 snapshot과 단일 monotonic deadline, PUT 1회·동일 ID GET, 자동 재제출/취소 없음.
- root 검증: 전체 521 tests PASS(15.01s, -W error), 신규 84 unit + 4 실제 TCP 응답 유실/기한 시험, wheel·격리 lazy import PASS. DB commit 뒤 유실 주입에서도 PUT/processor 각 1회였다.
- 수리: failed.error_code의 임의 원문 노출 공백을 제한한 ASCII 코드·길이·실패 상태 검사로 막고 legacy 경로도 회귀했다. 정상 서버 코드는 유지한다.
- 문서: README 5절·원본 TODO 2개 체크(누적 31개)·흐름/브라우저 비교. 실제 GPU 재호출·운영 proxy·광고 품질을 이번 검사로 완료하지 않는다.
- 다음: 로컬 cold/warm·크기·단계 trace와 메모리 관찰 계약. 실사진·CUDA·렌더러·팀 인수는 별도. [검증](../test/relay-b8.md).


## 2026-10-22 · B9 · 구현/검증

- 구현: execution_trace·ledger 별도 관찰/terminal 원자 저장·worker/모델 계측·인증 execution API/CLI. wire/hash/timing 유지, 미기록 NOT_RECORDED/null.
- root 검증: 전체624 tests PASS(36.32s), pip check·wheel·격리 lazy import. 실제MPS6 jobs와 source hash6개 일치·get-execution CLI를 확인했다. 동일512 두 job의3개 출력 hash 일치.
- 측정: worker 누끼9.102s, 배경512 cold47.170s/warm28.488s,768 57.418s,1024 104.426s,세로50.855s. 일부 단계 중첩·작업 후 메모리 snapshot을 구분하고 peak/순수GPU시간으로 표시하지 않는다.
- 시험 fixture 수리: worker 시작 recovery의 시각 호출을 추가해 iterator 고갈을 해소했다. 제품 결함과 구분한다.
- 문서: README5절·TODO 현재 출발점 갱신·기존4개 체크(누적35개)·설치/측정표·flow/브라우저 비교. 꽃 표현 편차·제품 잔상·최종 품질은 미해결.
- 다음: 사용자 요청의 B10 SSOT 대조·실행 TODO 정리. OOM/저장 실패 뒤 같은 worker 후속 정상 job·부분 결과 자산 게시/보존/정리는 이후 후보. 실사진·CUDA·렌더러·팀 인수는 별도. [검증](../test/relay-b9.md).

- B9 시각 검증 정정: 최초 브라우저 화면은 헤더 오기 수정 전 자료다. 헤더·PNG 수정 후 최종 HTML 재검증은 Chrome CUA의 `file:` 정책으로 BLOCKED. 현재 PNG 직접 검사·문서 구조 정적 검증은 완료했고 이전 screenshot을 최종 PASS로 쓰지 않는다.


## 2026-10-22 · SSOT 대조 문서 전달

- 확정 인스타그램4:5·각1개·동일제품2~5·참고배경0~5와 현재 단일source/카피기본3/참고계약·4:5·최종렌더 미연결 차이를 정리했다. [대조표](../../docs/ssotAlignment.md).
- SSOT-01~12의 담당/미정·의존·완료기준은 imageTodo의 실행 항목에 연결했다. 기존 완료35개는 실제 완료일2026-10-22와 B1~B9 근거를 붙였고 신규 기능은 체크하지 않았다.
- 입력 운영 상한은 유지했다. 사용자 검토·제품 레이어 승인·자동 검수를 분리하고 최종 한글 합성은 MVP 필수, 부분수정UI·다중시안·다른비율/플랫폼은 후속으로 구분했다. 기존 revision/hash/cache는 유지한다.
- 이 작업은 문서 대조이며 SSOT 새기능 구현/팀합의/인수 완료가 아니다. B9 최종 HTML 미리보기는 자동승인검토의 file: 접근 거절로 BLOCKED이며 우회하지 않았다. 검증된 소스/문서 게시와 이 제한은 별개로 기록한다.


## 2026-10-22 · B9 소스·SSOT 문서 전달 확인

- 소스 `858c6f7709074b230bdb82192aa529583936fc5c` 일반 push·원격 SHA 일치·로컬152파일 동기화 확인. 로컬624 tests33.73s·설정 검사 PASS. 상세 Git·백업·검증·제한은 [게시 기록](integrate-log.md)에 둔다.
- 소스 게시 COMPLETED, 최종 HTML 브라우저 검증 BLOCKED, Flow diagram verification GAP/PARTIAL, 다음 코드 릴레이 STOP_WITH_REASON. 실제품 품질·CUDA·최종 렌더러·팀 인수는 미완료다.


## 2026-10-22 · 외부 이미지 API 래퍼 방향 정정

- 사용자 정정: LLM은 김다혜, 이미지도 외부 API 래퍼는 pi. 제공자·모델은 미정이며 ChatGPT Pro·최대 추론 리서치를 요청했다.
- 요구사항/설계/README/역할/원본 TODO를 정정했다. 기존 완료35개·SSOT12개는 보존하고 외부 API 작업 API-01~06을 미완료로 추가했다. 로컬 GPU 설치·튜닝·CUDA 이전은 자체호스팅 재선택 시만 적용한다.
- 코드·기존 모델·결과·키는 변경하지 않았다. 기존624tests·MPS6건은 로컬 엔진 근거이며 외부 API 실호출·제품 품질·팀 인수 근거가 아니다. 이번 검증은 문서 정적 검사다.
- Ego 리서치 창은 열었지만 ChatGPT 로그아웃 상태여서 로그인 제어를 넘겼다. 아직 Pro·최대 추론 설정 확인/프롬프트 전송/답변 수신은 하지 않았다. 준비한 연구 질문을 로그인 완료 뒤 같은 창에서 전달한다.


## 2026-10-22 · B10 OpenAI API 래퍼 구현 전

- Intent: 사용자 OpenAI 선택·API 누끼·실호출 금지에 따라 오프라인 검증 가능한 래퍼를 구현한다.
- 계약: architecture/contract/openai-images.md. legacy wire/hash와 새 api_image 단일 결과를 구분한다.
- Conflict: 과거 B9 HTML BLOCKED로 이전 relay는 정지했으나 현재 사용자가 새 구현을 직접 요청했다. Winner: 현재 사용자 지시. Resolution: B9 검증을 PASS로 바꾸거나 browser 차단을 우회하지 않고 B10의 코드·모의통합을 수행한다.
- 검증: 가짜 HTTP 응답·프로젝트 자산/SQLite·full regression. 실제 키·API/모델 호출·운영 배포 없음.
- 사전 상태: clean plan/models, 원격 schema 초안 64124ed를 ff-only로 수신. 기존 초안 스냅샷 보존.

## B10 구현·검증 closeout 전 기록 · 2026-10-22

- 구현: api_image 생성/편집/API 누끼, 기본 차단, 고정 provider 전송, 영속 시도·예약, 안전한 PNG 저장, 내부 worker/receipt와 mock CLI.
- 전체806 tests PASS51.05s, mock CLI actual_provider_calls=0, mock_provider_calls=3. 출력 경고 분류 문제 수리와 회귀 [근거](../test/errorlogs/2026-10-22-relay-b10.md).
- native 역할 분담: root는 attempt/processor/runtime·통합 검증, 계약/worker/API·예제 담당과 adapter 담당, 문서 담당을 제한된 파일로 병렬 위임했다. 실제 모델/키/제공자 호출 없음.
- 게시 대상 plan/models, PR/협업일지/배포 없음. 실제 API 인수와 서비스 UI/renderer는 이번 전달 완료로 표시하지 않는다.
