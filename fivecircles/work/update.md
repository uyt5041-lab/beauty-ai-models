# 최근 변경

## 2026-10-26 · 이미지 통합 브랜치 이름 변경

- 사용자 요청으로 `codex/image-main`을 `feature/image-model`로 변경했다. 기존 구현 커밋과 이력을 유지하고 현재 안내 문서를 새 이름으로 맞췄다. 아래 기존 게시·테스트 기록의 브랜치명은 당시 사실로 보존한다.

## 2026-10-26 · 이미지 전체 통합 브랜치

- 사용자 요청에 따라 `plan/models`의 `cbf14b2`에서 `codex/image-main`을 분기했다. 최신 이미지 실행기·호출 상한·W&B 수집·v3 프롬프트·문서·테스트를 통합 게시 대상으로 정리했다. 별도 PR이나 협업일지 병합은 하지 않는다.
- [이미지 구조 문서](../../docs/imageArchitecture.md)에 폴더 트리, 실행·프롬프트·기록 그래프, 빠른 시작, 수정 위치, Graft 재사용과 현재/미완료 범위를 정리했다. 로컬 그래프 캐시와 생성 자산은 게시하지 않는다.
- 전체 `pytest -q -W error`: **1,321 PASS / 24.16초**. 새 유료 API 호출·팀 E2E·배포는 수행하지 않았다. 원격 게시 결과는 별도 검증 기록을 따른다.

## 2026-10-25 · 팀 연동 전 합의 항목과 공유 문안

- 팀 Notion 대조 결과를 원본 이미지 TODO 8.1절 LINK-01~05에 추가했다. OpenAI 지시문, 사진 장수·역할, 제품 유형 시점, 검토·최종 합성, 입력 처리·FinOps 계측의 완료 증거를 정의했다. 모두 미합의·공동 검증 전이다.
- [다혜·혁상 공유 문안](../../docs/integrationRequests-20261025.md)을 작성하고 [연동 점검 문서](../../docs/modelIntegrationReview.md)에 연결했다. 메시지·Notion 전송은 하지 않았다. 스키마·서비스 코드와 과거 완료 이력은 보존했다.
- 검증 PASS: 두 설치 폴더의 FiveCircles 검사, 문서 3개 동일성·로컬 링크 존재, LINK-01~05 미체크 각 1개, Git diff 공백 검사. 코드 변경이 없어 pytest·실 API·E2E는 실행하지 않았다.

## 2026-10-23 · B14 W&B 실험 비교와 FinOps40%

- Langfuse를 유지하고 기존 실제 실행20건(API7/로컬13)을 비공개 W&B에 게시했다. 입력/결과·프롬프트·시간·비용·비교표와 평가계획 원격 readback PASS. 새 이미지 모델 호출0회.
- 팀 Notion 기반 임시 평가계획·빈 실제품10개manifest·오프라인 채점 도구를 추가했다. 이미지40/FinOps40/서비스20이며 실제품·확정청구·팀잔액·서비스 인수는 미확인으로 유지한다.
- 전체1078 tests20.36s·FiveCircles·의존성·diff검사 PASS. [게시/비용/검증](../test/wandb-finops-b14.md), [사용법](../../docs/wandbExperiments.md), [임시 평가](../../docs/imageEvaluationPlan.md). Git 전달은 [반영 기록](integrate-log.md)에서 별도로 확인한다.
- 후속 정리: 원본 이미지 TODO에 완료 7개와 남은 작업 7개를 구분했다. [쉬운 한글 작업 예시](../../docs/wandbFinopsWorkExamples.md)는 설명용 가상 자료다. 실제 팀 점수나 비용으로 집계하지 않는다.

## 2026-10-22 · BEAUTY-FC-001

- FiveCircles 원본을 받아 BeautyAI 운영 문서/설정/프로젝트 스킬을 추가했다.
- 기존 모델 코드와 TODO를 연결하고 담당·마일스톤·미합의 상태를 구분했다.
- 검증과 완료 상태: [setup-validation.md](../test/setup-validation.md).
- Git 반영 상태: [integrate-log.md](integrate-log.md).

## 2026-10-22 · 릴레이 B1

- 자산 저장/검증과 제품 캔버스/보존 검사를 추가. 원본 이미지 TODO 8개를 근거 범위로 체크.
- 72 tests 및 CLI 파일 저장, Mermaid 보고서 브라우저 검증 PASS. [상세](../test/relay-b1.md).
- 다음은 B2 영속 job/API. GCP 조회는 사용자 재인증 필요.

## 2026-10-22 · 릴레이 B2

- 인증 internal API·WAL ledger·단일 호스트 worker 구현. 원본 TODO의 로컬 신뢰성 7개만 추가 체크.
- 전체 112 tests, health API 8 tests 및 loopback·프로세스 복구·시각 비교 PASS. [상세](../test/relay-b2.md).
- 다음은 B3 profile·승인·adapter. 실제 GPU·운영 네트워크·팀 인수는 미검증.

## 2026-10-22 · 릴레이 B3

- 제품 후보/명시적 승인 저장·서버 고정 profile·로컬 template·실행 CLI를 연결했다. 원본 TODO 로컬 범위 4개만 추가 체크.
- 전체 148 tests·실제 TCP CLI·512×512 결과 파일 검증 PASS. [상세](../test/relay-b3.md).
- 다음 B4는 프롬프트 버전 수신·편집 revision·프로젝트 캐시. 실제 GPU/AI 모델·최종 광고 품질·팀 인수는 미검증.

## 2026-10-22 · 릴레이 B4

- 프롬프트 수신·legacy hash, 편집 revision CAS·프로젝트 성공 결과 캐시·늦은 attach 차단을 연결했다.
- 전체201 tests 및 실제 TCP CLI의 문구 수정 후 동일 job 재사용 PASS. TODO 로컬 검증3개 추가 체크. [상세](../test/relay-b4.md).
- 다음 B5는 고정 평가 manifest·증거 수집 도구. 실모델/평가셋 품질/사용자 UI/렌더러/팀 인수는 미검증.


## 2026-10-22 · 릴레이 B5

- 로컬 평가 manifest·mask/픽셀 지표·job timing API/CLI를 추가했다. 원본 TODO의 도구 하위 3개만 체크했다(누적 25개).
- 전체 280 tests PASS(4.06s), 예제 manifest CLI·실제 TCP timing·wheel build PASS. FIFO manifest 대기를 발견·수리하고 재검증했다. [상세](../test/relay-b5.md).
- 실제 모델·평가셋·사람 품질·GPU 시간·VRAM·비용은 미검증. 다음 B6는 로컬 Mac 실제 모델 검증 후 GCP 이전이다.


## 2026-10-22 · 릴레이 B6

- 고정 BiRefNetLite 모델 저장·선택 의존성·명시 장치 adapter·runtime/CLI를 연결했다. 기존 TODO 2-A의 2개만 추가 체크했다(누적 27개).
- 전체 339 tests PASS(4.30s), 실제 MPS/TCP 합성 병 fixture의 RGBA·마스크·미승인 후보·수동 경고 PASS. 첫 job 6897.11ms는 로딩·대기·파일을 포함한다. [검증](../test/relay-b6.md).
- 다음 B7 후보는 로컬 실제 배경 모델. 실사진 품질·CUDA/GCP·렌더러·팀 인수는 미완료다.


## 2026-10-22 · 릴레이 B7

- 고정 SDXL fp16 파일·inpaint adapter·두 tokenizer·제품/여백 복원·CLI를 연결했다. TODO 2-C의 2개만 추가 체크했다(누적 29개).
- 전체 433 tests PASS(9.39s), 실제 MPS 512 fixture의 opaque 770/mismatch 0·Safe Zone exact·3개 레이어 PASS. 제거된 VAE API로 실패한 2회를 수리·보존했다. [검증](../test/relay-b7.md).
- 꽃 표현 부족·제품 잔상이 있어 품질/안전은 NOT_ASSESSED. 다음 로컬 후보는 반복/크기·단계 trace·제출 조회 조정이며 실제 품질·CUDA·팀 인수는 별도다.


## 2026-10-22 · 릴레이 B8

- 단일 deadline·PUT 1회·동일 ID GET의 제출 조정과 CLI를 연결했다. 원본 TODO 4-A의 2개만 추가 체크했다(누적 31개).
- 전체 521 tests PASS(15.01s), 신규 84 unit + 4 실제 TCP 시험으로 commit 뒤 응답 유실·단일 실행·미관찰/관찰 후 기한 종료를 확인했다. 오류 원문 노출을 코드 형식·상태 검사로 수리했다. [검증](../test/relay-b8.md).
- 다음 후보는 로컬 반복/크기·단계별 trace다. 운영 proxy·실사진 품질·CUDA·렌더러·팀 인수는 별도다.


## 2026-10-22 · 릴레이 B9

- job별 단계/모델/환경/동기화/작업 후 메모리 관찰을 terminal과 함께 저장하고 execution API/CLI를 추가했다. 원본 TODO4개만 추가 체크했다(누적35개).
- 전체624 tests PASS(36.32s), 실제 MPS 누끼1+배경5·동일512 두 번 hash 일치·배경5건 opaque mismatch0/Safe Zone exact. 최고 관찰 driver19,876,773,888 bytes는 작업 후 snapshot이며 peak 아님. [검증](../test/relay-b9.md).
- 크기별 꽃 표현 편차·제품 잔상으로 품질 미평가. 다음 B10은 사용자 요청의 SSOT 대조·실행 TODO 정리다. 실패 뒤 같은 worker의 후속 정상 job·부분 결과 자산 처리는 이후 후보로 유지한다. 실사진·CUDA·렌더러·팀 인수는 별도다.

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

## 2026-10-22 · B10 OpenAI 오프라인 래퍼

- 사용자 OpenAI 선택·API 누끼·실제 호출 금지를 TODO/README/계약에 반영했다. 기존 로컬 실험·과거 스키마 초안은 보존했다.
- OpenAI adapter, project 자산, durable attempt/예약액, job worker, receipt API, mock CLI를 연결했다. 실제 호출0회, 기본OFF·예산0.
- 806 tests51.05s·mock CLI3건·pip check·wheel PASS. 출력 경고 분류 수리 포함. [검증](../test/relay-b10.md), [흐름](../architecture/spec/image-runtime-flow.md).
- 실제 계정/품질/비용·최종 UI/렌더러·팀 인수는 미완료. Git 게시와 로컬 동기화는 [receipt](integrate-log.md)로 별도 확인한다.

- B10 전달 확인: source `fb7305bfb9acc123a71092ab2bc715b402f64efa` 일반push/원격일치, local192파일일치. 다음 실API인수는 사용자 호출금지로 NOT_RUN, 릴레이 STOP_WITH_REASON. 문서 receipt는 별도커밋으로 같은브랜치에 남긴다.

## 2026-10-23 · B11 화장품 프롬프트

- 프리셋3개·배치3개·누끼지시를 revision2에 연결. 기존revision1/hash 유지. 꽃의 기본 추가 요구만 제거하고 명시적 연출은 허용한다.
- 전체866 tests·로컬60 tests와 설정 검사 PASS. [검증](../test/cosmetics-prompts-b11.md), [사용 가이드](../../docs/cosmeticsPromptGuide.md). 실제API0회, 실제품 품질과 서비스 연결은 미완료.

## 2026-10-23 · B12 Langfuse와 프리셋 비교

- 관리 템플릿3개, 고정버전/최종본문/해시, SDKgeneration 관찰을 연결했다. 실제편집3회 성공, 원격저장 readback3건 일치.
- 전체934tests PASS. 밝은컬러는 광고주목도, 클린은 제품재질 표현에 우선추천. 실제품라벨/팀인수 미검증. [실행·비용·시각 비교](../test/cosmetics-langfuse-b12.md).

## 2026-10-23 · B13 밝은 컬러 무광 보강

- 사용자 선택한 밝은 컬러에 원본무광 보존지시를 보강하고 Langfuse v2로 기록했다. 동일원본으로 실제1장 추가, remote readback PASS. [실행·시각 비교·참고출처](../test/cosmetics-matte-b13.md).

## 2026-10-23 · 목표 변경에 따른 TODO/날짜 재배분

- 범위: 이미지 API 래퍼·실제품 실행기·Langfuse/W&B·FinOps40% 목표를 원본 이미지 TODO에 반영한다.
- 날짜: 10/20~11/13 평일 19일에 계획을 배분한다. M1 10/30·M2 11/06, 텍스트 고정 연계 11/10, 결과물 11/12 19:00·일지 11/13 23:50을 구분한다.
- 날짜별 체크 19개는 모두 미체크로 시작한다. 기존 완료 47개와 실제 검증일·근거는 보존한다.
- 진행 중 코드·단계 원장·테스트는 재개/수정하지 않는다. 이 변경은 문서 계획이며 코드 완료·유료 호출·팀 합의·Git 게시를 뜻하지 않는다.
- 문서 검증: 평일 19일/미체크 19개·기존 완료 근거 47개 보존·요일/마일스톤/실행기 목표 검사 PASS. FiveCircles 링크·설정 검사와 문서 diff 검사 PASS. 코드 테스트·유료 호출은 실행하지 않았다.

## 2026-10-23 · 실험 실행기 네 단계 완료

- 제품 2~5장·참고 0~5장, 명시 프리셋/버전, 네트워크 없는 미리보기와 고정 실행 계획을 연결했다.
- 영속 원장에서 계획당 실행권을 한 번만 부여하고 호출 시도 상한·실패/미확인 중단을 적용했다. 기존 일반 서비스 원장은 유지한다.
- 단계별 55/176/65 tests, 마지막 전체 1,258 tests PASS. 공유 JPEG 두 장의 CLI 준비도 성공했다. 유료 호출은 0회다.
- [사용법](../../docs/experimentRunner.md) · [단계 판정](experiment-runner-stages.md). 실제 사진 적합성·광고 품질·확정 비용·팀 인수는 별도다.

## 2026-10-23 · 새 실험 W&B 연결 완료, iCloud 검증 중단

- 새 실행기 자동 W&B 등록과 결과만 재등록하는 명령을 구현했다. 전체 1,321 tests, FiveCircles, diff 검사 PASS. 기존 W&B 비교 기록 20건 원격 재조회 성공.
- 새 LIVE 업로드는 이번에 수행하지 않았다. 모의 연결 검증과 실제 원격 재조회를 구분한다.
- iCloud 사진 3장의 형식·크기 검증에서 AssertionError. 사용자 지시에 따라 수리/재시도 없이 중단했다. 새 미리보기는 생성하지 않았다.
- [단계 판정](experiment-wandb-bridge-stages.md) · [사용법](../../docs/experimentRunner.md). 이미지 API 0회, Git 게시 없음.

## 2026-10-23 · 실제 호출 전 입력 검증 재개 후 중단

- iCloud 원본 3개의 실제 형식 MPO를 확인했다. PNG 사본 준비 중 검증 코드의 verify() 호출 순서 오류가 발생했다.
- 사용자 단계 중단 규칙에 따라 종료. 사진 손상이 확인된 것은 아니다. 실제 이미지 API/W&B 신규 업로드 0회.
- [단계 기록](experiment-wandb-bridge-stages.md). 생성·평가는 진행하지 않았다.


## 2026-10-23 · 검증 코드 수정 후 실제 1회 생성·평가 완료

- 사용자 지시가 수정됐다. 검증 코드 오류는 고치고 계속하며, 미리보기 확인 후 실제 1회만 호출한다. 앞선 FAILED_STOPPED는 당시 상태로 보존한다.
- MPO 원본 3개를 보존하고 frame 0을 PNG로 옮겼다. 새로 연 파일의 verify() 후 다시 열어 픽셀을 비교했다. 형식·크기·픽셀 일치·원본 해시 검사가 모두 통과했다.
- 제품 설명 길이를 계약 한도에 맞추고, 정면·측면·뒷면을 제품 입력으로 지정했다. Langfuse vivid-pop v1을 고정했다. 미리보기 HTML·파일·최종 프롬프트를 로컬에서 확인했다. 브라우저 로컬 파일 접근은 차단되어 브라우저 표시 검증은 하지 않았다.
- 실제 1회 성공: 27.393523초, 1152×1440. 사용량 5,298토큰. 현행 공식 요금 추정 $0.050008, 실제 청구액 null. 예약 $1은 사용액이 아니다.
- W&B 결과와 AI 평가 업로드·재조회 완료. 평가 파일 8개 해시와 summary를 대조했다. 원본 실행 보고서는 유지했고 재생성하지 않았다.
- AI 예비 81/100. 형태 3/5로 임시 하한 4/5 미달이므로 재작업 필요다. 사람 검수와 광고 사용 승인은 남았다. FinOps 비중 40%, 실제 청구·잔액·승인 광고 근거가 없어 점수 미산정.
- 상세: `docs/imageExperiment-20261023-avene.md`. 결과: `outputs/experiment-trials/20261023-avene-vivid-pop-02/`.
- 신규 제품 코드 변경 없이 입력 준비·실행·평가를 수행했다. 사전 전체 테스트 1,321 PASS 근거를 유지한다. 평가 HTML 로컬 링크 검사와 원격 artifact 대조 PASS. Git 커밋·푸시·PR 없음.

### 2026-10-23 추가 비교 — 비율 보존 프롬프트 v3

- 같은 제품 입력 3장·모델·설정에서 비율 보존 문구만 추가하고 정확히 1회 생성했다. v3 25.674823초·추정 $0.050838, 이전 v1 27.393523초·추정 $0.050008. 두 AI 점수는 81/100, 형태 3/5로 같고 개선은 미확정이다.
- Langfuse 프롬프트 v3·[새 trace](https://jp.cloud.langfuse.com/project/cmt966agt0007ad0jn1c3s4up/traces/debacc2ea0d95ae326fde1ec3168aff4)와 평가 점수 16개(각 8개)의 trace·observation 연결·값을 원격 조회했다. [W&B 비교](https://forge.coreweave.com/wandb/uyt5041-alphaflower/beautyai-image-experiments/runs/cmp-86cd9d87740e2962079b)는 `READBACK_VERIFIED`: 파일 12개 해시·표 2행·관련 실행 3개의 현재 정책 링크를 확인했다.
- 사용자 의견에 따라 현재 판정은 **사용자선택**이다. 비율 차이만으로 자동 탈락·추가 생성하지 않는다. 그대로 사용·보정·재생성 선택을 제공한다. 과거 점수와 당시 판정 이력은 보존한다.
- 사람 검수·정식 출시·실제 청구 확인은 별도다. 근거는 `docs/imageExperiment-20261023-avene.md`와 실행 폴더의 `comparison/comparison-review.json`이다. 이번 문서 갱신은 기존 내용을 유지한 덧붙이기이며 푸시·PR은 하지 않았다.


## 2026-10-27 · 기본 워터마크 제외 정책

새 화장품 생성·편집·누끼 요청에 `watermark_policy=exclude-v1`을 고정했다. 사진 위의 워터마크 제거와 신규 워터마크 제외를 요청하되 실제 제품의 인쇄 로고·라벨은 보존한다. 과거 요청의 본문·해시는 유지한다.

Langfuse production은 clean-studio v2, premium-minimal v2, vivid-pop v4로 새로 게시하고 문장·버전·정책을 재조회했다. 밝은 컬러는 직전 production v2를 기준으로 워터마크 조건만 보강했다. v3 실험은 그대로 보존한다. 전체 회귀 1,326 passed / 23.40초, 이미지 API 호출 0회. [검증](../test/watermark-policy-20261027.md). 실물 제거 품질은 미검증이다.

2026-10-26 협업일지는 별도 일지 브랜치에서 작성하며 오늘 변경을 어제 성과에 포함하지 않는다.


## 2026-10-27 · K1 한국어 프롬프트
ko/en 선언을 지원하고 과거 en 기본값·해시는 유지했다. 한국어 실제 multipart 전송과 관련 회귀 268 PASS. 외부 호출0. [릴레이](relay-korean-adapter-resize.md).


## 2026-10-27 · K2 백엔드 Python 연결
한국어 prompt/concept/mood·카피·승인 메타데이터·2~5/0~5 사진 bytes를 받아 기존 영속 래퍼로 연결한다. 같은 project/job의 입력과 자산을 SQLite에 고정해 재시작·동시 중복 호출을 차단한다. 기본 결과/시간/사용량/비용 미확인 상태를 반환한다. 123 tests PASS, 외부 호출0. 서버 승인·팀 예산·HTTP 통합은 미완료. [사용법](../../docs/backendModelAdapter.md).
