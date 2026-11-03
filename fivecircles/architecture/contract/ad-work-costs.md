# 광고 제작 work_id · 누적 토큰·비용 계약

2026-11-03 · 상태 REVIEW_PENDING · 대상 beauty-ai-models.
기존 FinOps 미게시43개 파일을 해시로 격리한 codex/ad-work-costs-20261103에서 구현한다.
PR base는 사용자 확정 feature/image-model. 작업 branch를 push하고 PR 설명·검증 후 merge한다. plan/models와 R2 WIP는 변경하지 않는다.
기존 예산/중복 호출/생성당 비교를 보존한다. 실제 유료 모델 호출 없이 모의 전체 흐름으로 검증하고 W&B에는 구분된 검증 자료를 게시한다.

## 목표·완료

한 광고의 기획·이미지 생성·검수·반복 수정·합성 기록을 project_id/work_id에 묶어 완료까지 누적한다.
수정은 같은 work_id의 새 attempt이며 성공 재사용은 새 과금 attempt가 아니다. 실패 비용도 포함한다.
제품/생성 성공은 광고 채택이 아니다. 완료는 명시한 최종 파일 hash·검수자·품질 기준·합격 근거로만 기록한다.
모델 API 비용 범위이며 로컬 합성의 CPU/스토리지 가격은 측정하지 않는다. 로컬 단계의 API 사용량0은 명시적 local receipt로만 기록한다.
이번 완료는 SDK/mock/API/재시작/화면/W&B/PRmerge 검증이다. 실제 팀 서빙 배포·외부 텍스트 서비스 적용·품질 효과는 별도다.

## 소유와 모델

새 work_ledger가 work/expected attempts/image-job 연결/영수증을 소유한다. 기존 ProviderAttemptStore와 계정 DB에 work 상태를 합치지 않는다.
새 파일 work_contracts.py는 검증, work_ledger.py는 SQLite, work_metrics.py는 순수 집계, work_tracking.py는 호출 관측을 담당한다. 가격 계산은 work_metrics.py의 순수함수다.
원시 프롬프트·사진·키·원시 오류는 저장하지 않는다. 기존 CallMeta/ExternalImageRequest/Result와 과거 request/report hash는 변경하지 않는다.
식별자는 UUID(work/project/attempt/job), 제품/검수자/정책/모델/source 별칭은 기존 safe identifier로 검증한다. hash는64자리 lowercase hex.
작업별 최대1000 attempts, project export 최대1000 works/20000 receipts, 입력JSON 최대1MiB. 초과는 안전 오류이며 부분 export로 성공시키지 않는다.
모든 JSON 입력은 중복 key·NaN·비밀 key/value·잘못된 타입을 거절한다. 오류 코드 WORK_*만 노출한다.

Work: work_id, project_id, product_ref, task_pair_id(nullable), arm(nullable/baseline/candidate), required_stages,
status(OPEN/COMPLETED/CANCELLED), created_at, closed_at(nullable), completion(nullable).
required_stages 기본 prompt_build/image_generation/review/composition. revision은 필요할 때 기록하며 강제 유료 수정을 만들지 않는다.
completion: artifact_sha256, reviewer_ref, quality_policy_version, quality_pass(strict true), expected_attempt_ids(UUID 고유목록).
Stage: prompt_build/image_generation/review/revision/composition. Text operation analyze_product/draft_campaign→prompt_build, review_copy→review, rewrite_copy→revision.
Attempt identity: attempt_id, work_id, project_id, stage, operation, provider, configured_model, prompt_version,
request_hash, parent_attempt_id(nullable), started_at. 상태 PENDING→SUCCEEDED/FAILED/UNKNOWN. started_at은 store가 기록한다.
Terminal Receipt: schema_version=beautyai-work-receipt-v1, attempt_id, provider_request_id(nullable), model,
usage(nullable), elapsed_seconds(nullable nonnegative finite), status, error_code(nullable safe), artifact_sha256(nullable),
estimated_cost_usd(nullable string9), rate_version(nullable), actual_cost_usd(nullable), billing_evidence_ref(nullable), source_ref, source_sha256.
usage keys: input_tokens/output_tokens/total_tokens/input_text_tokens/input_image_tokens/cached_input_tokens/reasoning_tokens (모두 nullable strict int0..2^63-1).
입력 usage는 허용 key만 사용한다. total과 input/output을 중복 합산하지 않는다. 보고 주 토큰 합계는 observed input+output이며 cache/reasoning은 부분집합이다.
terminal receipt에는 최초 사실을 불변 저장한다. 같은 attempt의 같은 내용은 멱등, 다른 내용은 WORK_RECEIPT_CONFLICT다. 향후 청구 정정 원장은 별도 기존 비용 revision 계약이다.
provider_request_id가 있으면 같은 project/provider/request ID를 다른 attempt/work에 재사용하지 못한다. NULL은 새로운 근거를 만들지 않는다.
Local 단계는 provider=local/model=local, usage input/output0을 명시하고 estimated API cost0/rate_version=local-api-zero-v1이다. actual cost는 별도 근거 없으면 null이다.

## 원장 API와 상태

AdWorkLedger(path)에서 파일SQLite BEGIN IMMEDIATE로 짧게 처리, network 중 lock 없음. project/work 경계를 모든 API에 적용한다.
create_work(project_id,work_id,product_ref,task_pair_id=None,arm=None,required_stages=None)->Work. 같은ID 다른spec은 충돌.
start_attempt(project_id,work_id,attempt_id,stage,operation,provider,configured_model,prompt_version,request_hash,parent_attempt_id=None)->None.
record_receipt(project_id,work_id,receipt)->None. 선등록 attempt에만 terminal receipt 수신; 동일 replay는 비용 증가0.
bind_image_job(project_id,work_id,job_id)->None. 같은 project/job은 한 work에만 고정. 이 연결은 restart 후에도 유지한다.
get_image_binding(project_id,job_id)->work_id|None. work_tracking.sync_image_receipt(ledger,project_id,job_id,provider_record,request)는 관측을 store API로 전달한다.
이미지 job을 바인딩하면 completion 전 확인할 expected job이 된다. receipt를 처음 관측할 때 provider attempt ID로 work attempt를 만든다.
취소 전 바인딩한 이미지 job은 sync_image_attempt(project_id,job_id,identity,receipt_or_none)로 기존 provider attempt를 뒤늦게 등록/동기화할 수 있다.
이 재수집 예외는 CANCELLED에만 허용하며 새 provider 호출을 허용하지 않는다. 일반 start_attempt는 계속 CLOSED 오류다. sync는 기존 job binding을 검증하고 job당 한 attempt만 허용한다.
image get/provider_record 없는 바인딩은 미확인이며 자동0원/취소하지 않는다. sync는 ProviderAttemptStore.get만 읽고 모델 API를 호출하지 않는다.
complete(project_id,work_id,completion)->None: 새 작업을 막고 모든 expected jobs에 영수증, 모든 attempt terminal, required stages 성공 기록,
최종 hash가 같은 work의 성공 image/composition 영수증에 존재, expected_attempt_ids가 원장 전체와 정확히 같을 때만 허용한다.
usage/단가 미확인 자체는 광고 완료를 막지 않는다. 대신 전체 비용/토큰은 null+부분합/누락 수다. 성공한 검수 호출은 법적 승인으로 간주하지 않는다.
cancel(project_id,work_id)->None: OPEN만 CANCELLED. pending 호출의 뒤늦은 영수증은 수신해 비용을 보존하되 새 attempt/job은 금지한다.
COMPLETED는 동일 receipt/동일 completion 재입력만 허용하고 다른 상태/새 호출은 거절한다. 두 상태는 자동 재오픈하지 않는다.
get_work/export_dataset(project_id)->safe dict. export는 프로젝트 범위만, DB schema/migration 없이 읽는 read_work_dataset(path,project_id)도 제공한다.

## 자동 관측과 통합

ExternalImageProcessor에 optional work_ledger. 기존 process_job를 래핑해 finally에서 현재 provider receipt를 동기화한다. 없으면 기존 흐름과 바이트를 유지한다.
현재 scope가 있으면 job을 그 work에 바인딩한다. 명시 work_id/영속 job binding와 활성scope가 동시에 있으면 project/work/원장실제경로가 모두 같아야 한다.
불일치는 WORK_SCOPE_CONFLICT로 asset/예산/SDK호출 전에 거절한다. scope가 있고processor원장이없을때만scope원장을사용한다. 둘다있으면경로가같아야한다. work와 project 불일치는 요청/예산/API 전에 거절한다.
동기화 실패는 WORK_CAPTURE_PENDING으로 반환한다. ProviderAttemptStore의 성공 영수증은 보존되어 다음 동기화에서 같은 결과를 재사용하며 POST를 반복하지 않는다.
성공 reuse에서 동일 provider attempt는 원장 멱등이다. 결과/오류/usage/elapsed를 실제 underlying receipt에서 읽는다.
BackendImageAdapter.generate(...,work_id=None)에서 work를 명시할 수 있다. scope 또는 work_id가 있을 때만 바인딩하며 기존 입력 hash를 바꾸지 않는다.
freeform CLI --work-id/--work-ledger는 함께 제공한다. execute 전에 work 소유/OPEN 확인, processor에 전달한다. 준비파일/과거 request hash는 그대로다.
runtime은 root/ad_works.sqlite3를 만들고 processor와 내부 work API에 같은 객체를 전달한다. 기존 서비스 시작의 계정 예산 guard를 유지한다.
새 work_tracking.track_ad_work(ledger,project_id,work_id) contextmanager는 ContextVar로 async/thread 간 작업 혼선을 막는다. context 종료 시 이전 값 복구.
OpenAITextGateway._parse는 scope가 있을 때 start_attempt→SDK raw response→usage관측→parse/검증→terminal receipt를 기록한다. tracked SDK는 responses.with_raw_response.parse를 사용하고 raw.http_response.json()의 usage/model/id와 x-request-id를 먼저 관측한 뒤 raw.parse()한다. 이 기능도 없으면 WORK_CLIENT_UNSAFE로 호출 전 거절한다. SDK가 raw response 자체를 주지 못한 오류는 usage=null이며 추정하지 않는다.
텍스트 상태는 검증된 응답 SUCCEEDED, refusal/incomplete/응답 후 schema 실패/명시 HTTP오류 FAILED, timeout/연결실패/취소/응답 전 SDK parse오류 UNKNOWN이다. 도메인 후검증 실패는 이미 성공한 모델 receipt를 바꾸지 않는다. 취소도 finally에서 안전 receipt를 남긴 후 원래 취소를 전파한다.
텍스트 응답 후 receipt는 먼저 원장 인접 .cache/work-receipts에 mode0600 atomic replace+fsync로 sanitized recovery JSON을 쓴 뒤 record_receipt한다. 성공 시 inbox 파일을 지운다. 원장 실패는 WORK_CAPTURE_PENDING(안전 receipt 속성 포함)으로 반환하며 다음 scope 진입/호출 전 inbox 재수집을 시도한다. inbox까지 실패하면 오류에 포함한 receipt를 POST receipts로 재입력할 수 있다. 두 저장 모두 실패한 사실은 안전하게 복구 불가할 수 있음을 표시하며 usage를 지어내지 않는다. 같은 work의 같은 operation/request_hash가 PENDING 또는 UNKNOWN이면 새 SDK 호출은 거절한다. 재수집은 network 호출0회다.
관측 중 실제 SDK는 with_options(max_retries=0)로 사용한다. 이를 지원하지 않는 추적 client는 SDK호출 전 WORK_CLIENT_UNSAFE. 비추적 경로의 기존 SDK2 retries는 유지한다.
기획/검수/수정 생성 내용과 ReviewService 정책은 바꾸지 않는다. 도메인 후검증 실패는 모델 호출 성공/광고 승인과 구분한다.
외부 팀 텍스트는 동일 start_attempt+record_receipt 계약으로 입력한다. 이 경로는 비용 근거 수신이며 텍스트 생성 로직을 재구현하지 않는다.

자동 provenance는 canonical_bytes/digest로 고정한다. text request_hash는 operation/model/prompt_version/instructions/input/max_output_tokens/store/text_format(schema JSON) 전체 요청값의 canonical SHA256이며 원문은 메모리에서만 hash한다. source_ref는 work-receipt:{attempt_id}, source_sha256은 receipt에서 source_ref/source_sha256을 제외한 모든 필드의 canonical SHA256이다. local receipt에도 같은 규칙을 쓴다. 예외 원문은 포함하지 않고 safe error_code만 포함한다.
image identity는 stage=image_generation, operation=request.operation, provider=openai, configured_model=request.model, prompt_version=managed_prompt.version 또는 prompt.prompt_version 또는 unmanaged-v1, request_hash=기존 content_hash(request), parent=null을 쓴다. record metadata가 없으면 검증된 request로 채우며 결과 model은 metadata.model 또는 request.model이다. succeeded/failed/unknown은 SUCCEEDED/FAILED/UNKNOWN, submitting은 PENDING identity만 기록하고 receipt를 만들지 않는다. usage는 normalized usage 우선이며 raw_usage는 normalized에 없는 input/output/total/cache/reasoning만 채운다. result.image.sha256은 artifact, elapsed_seconds와 error_code는 underlying record 값, 비용은 명시 근거 없으면 null이다. created_at은 최초 identity 시간으로만 사용하고 updated_at/reuse events/raw metadata는 receipt/hash에서 제외한다. 동일 provider attempt 재관측은 동일 safe receipt/hash가 된다.

## 내부 HTTP API

기존 bearer→project 의존성 및 bounded body reader를 재사용한다. user project_id 입력은 받지 않는다. API 연결은 work_api.py 설치 함수로 분리한다.
PUT /internal/v1/ad-works/{work_id}: create spec(product_ref,task_pair_id,arm,required_stages). 200 멱등/201신규는 구현에서일관된200 허용.
GET /internal/v1/ad-works/{work_id}: work+metrics; GET /internal/v1/ad-works: project export.
POST /internal/v1/ad-works/{work_id}/attempts: identity 필드에서 project/work/started_at 제외한 start spec.
POST /internal/v1/ad-works/{work_id}/receipts: terminal Receipt. POST .../complete: completion. POST .../cancel: 빈객체.
기존 PUT /jobs/{job_id}에 선택 X-Ad-Work-Id UUID header를 지원한다. 인증/요청 검증 후 queue 전에 work/job을 바인딩한다. workstore 없으면503, 다른project/없으면404.
WORK_NOT_FOUND404, WORK_*CONFLICT/CLOSED/PENDING/MISSING_STAGE/COMPLETION_INVALID409, 검증400/413, storage/capture503. 인증오류401 기존정책유지.

## 가격·집계·표시

RateBook schema beautyai-work-rates-v1: rate_version, models{model:{mode(combined/split),input_usd(nullable),input_text_usd(nullable),input_image_usd(nullable),output_usd}}.
단위1M tokens, USD 문자열0..1e9 소수9. combined는 input전체×input_usd; split은 text/image 둘 다 관측됐고 합이 input일 때만 적용. output는 output_tokens×output_usd.
캐시 할인 미반영 추정임을 표시한다. 기본 단가를 추정하지 않는다. 미지원 model/누락 token/단가면 estimated null.
export_dataset(project_id,rate_book=None,data_kind=DECLARED_DATA)에는 rate_book을 입력 snapshot으로 포함한다. 명시 receipt의 추정+rate_version이 있으면 우선한다. 비밀/키는 rates 파일에 넣지 않는다.
Work dataset schema beautyai-work-dataset-v1: data_kind(DECLARED_DATA/SYNTHETIC_FIXTURE),works,attempts,receipts,image_bindings,rate_book(nullable). 각 project ID는 같아야 한다.
work_metrics.summarize_work_dataset(data)->dict는 전체 이력을 검증하고 모든 실패/수정/취소 비용을 포함해 latest 상태를 계산한다. 모델/단계별 input/output/cache/reasoning 부분합·누락 수 제공.
work별 model_api_attempt_count/local_stage_count/pending_count/failed_count, token observation coverage, input/output 전체합(null 허용)/known 부분합,
estimated_total_usd(null)/known_partial_usd/missing_cost_count, actual_total_usd(null)/known_partial_actual_usd, status/completion, stage/model breakdown을 반환한다.
전체 summary는 completed/open/cancelled 수와 모든작업 비용을 보존한다. 취소/미완료 비용을 전체생산비에서 제거하지 않는다.
완료쌍 비교는 work.task_pair_id로 baseline/candidate를 연결한다. product_ref/quality_policy/required stage 조건이 같고 양쪽 COMPLETED+비용완전할 때만 포함한다.
비용 RATE_MISMATCH는 양쪽 billable attempt의 실제 적용 rate_version 집합이 다르거나 미확인 버전이 있을 때다(local0제외).
명시영수증추정은그영수증버전, snapshot파생은RateBook버전을쓴다. 버전은가격정책의불변선언이며 외부선언의진위를자동인증하지않는다.
모델집합은달라도같은단가정책의USD비용은비교가능하다. 토큰은모델별로표시하며서로다른모델을동등단위로간주한감소율은제공하지않는다.
미완료/누락 work는 pair 통째로 제외하고 work/pair 수를 표시한다. 이 진단 비율은 전체생산비 절감과 구분한다. 전체생산비/완료 광고 원가도 별도로 표시한다.
비교 입력2+완료1 같은 선택편향을 전체 절감으로 표시하지 않는다. 같은 모델의 토큰만 비교하며 모델별 breakdown을 유지한다.

cost_reporting build/write_report에 optional work_dataset=None, CLI --work-dataset PATH 또는 --work-ledger PATH와 --work-project UUID/--work-rates PATH를 추가한다(둘 중하나).
미전달이면 기존 JSON/HTML/ID 바이트 유지. 전달이면 report.work_costs, inputs.work_dataset_sha256과 identity에 정규화 dataset을 추가한다. 기존 generation_comparison도 함께 사용 가능하다.
새 work_costs에는 schema_version beautyai-work-summary-v1, summary, works, comparison, scope=MODEL_API_COST_ONLY, authority=DECLARED_WORK_ACCEPTANCE가 있다.
화면은 작업상태·최종hash·누적비용·토큰관측률·실패/수정 수·단계/모델 세부·완료쌍 비교/제외와 전체 생산 원가를 한국어로 표시한다. 입력값 escape/CSP 유지.
W&B는 work_costs Table(작업당1행), work_stage_usage Table(작업/단계/model당1행) 및 work_ summary를 추가한다. 정확한 report.json·각 표·summary를 readback해야 완료.
report 없음/generation만/work만/둘다의 조건부 field/hash 형태만 허용한다. 임의 extra키·null 대신0·잘못된 count는 거절한다. summary expected-null 생략 codec은 유지한다.
정확한 wire는 아래 부록을 사용한다. work별/단계별 receipt details와 derived metrics는 같은 report.json artifact에 보존한다.

## 보안·대안·호환

단일 호스트 SQLite·명시 server credentials. 데이터 보존/일회전송/예산 경계와 오래된 hash/SDK DTO를 유지하는 선택형 연결이다.
대안: 모든원장을한DB로합치면 기존singleton/예산경계를깨므로 기각. 각CLI별기록은누락을만들어공통처리기/scope/외부영수증API를채택.
서버에 work API를 둘 때 기존 인증은 NOW_REQUIRED. 외부베타 사용자별권한/요율관리역할은 서빙 담당이 공개 전 통합시험해야 한다. 현재 전체배포상태는 UNKNOWN.
이벤트 쓰기는 O(1) 인덱스, export/집계는 bounded O(W+A)와 출력정렬. 네트워크가 없는 순수계산은 기존 validators/Decimal helpers를 재사용한다.
원장호출실패로 provider 결과를 지우거나 자동재생성하지 않는다. DB/crash 검증으로 늦은영수증을 재수집한다.

## Wire 부록

normalize_work_dataset(data)->dict와 normalize_rate_book(data|None)->dict|None는 work_contracts.py 공개 함수다. 객체는 strict extra-forbid이고 JSON 원문 대신 dict로 공급할 수 있다.
Dataset 정확키: schema_version=beautyai-work-dataset-v1,data_kind,works,attempts,receipts,image_bindings,rate_book. source/work receipt와 identity의 필드는 위 목록을 사용하며 nullable도 key는 보존한다.
image_bindings 행: project_id/work_id/job_id,attempt_id(nullable). Receipt 없는 PENDING attempt와 아직 attempt가 관측되지 않은 binding은 누락으로 센다. 같은 pending을 두 번 세지 않는다.
WorkMetrics 정확 최상위: schema_version=beautyai-work-summary-v1,scope=MODEL_API_COST_ONLY,authority=DECLARED_WORK_ACCEPTANCE,summary,works,comparison.
work rows는 아래 WorkTable필드 외 stages,attempts,required_stages,quality_policy_version(nullable),rate_versions(list)를 가진다.
stages는 아래 StageTable필드의 배열. attempts는 identity와 nullable receipt를 함께 가진 안전한 상세 이력이다. 작업별시간은 SDK elapsed 합계로 구분하고 wall-clock 완료시간으로 표시하지 않는다.
WorkTable 열 순서: work_id,product_ref,task_pair_id,arm,status,attempt_count,model_api_attempt_count,local_stage_count,pending_count,failed_count,revision_attempt_count,
unobserved_image_job_count,known_usage_count,missing_usage_count,input_tokens,output_tokens,known_input_tokens,known_output_tokens,
estimated_total_usd,known_partial_estimated_usd,missing_cost_count,actual_total_usd,known_partial_actual_usd,final_artifact_sha256.
StageTable 열 순서: work_id,stage,provider,model,attempt_count,known_usage_count,missing_usage_count,input_tokens,output_tokens,known_input_tokens,known_output_tokens,
cached_input_tokens,reasoning_tokens,estimated_total_usd,known_partial_estimated_usd,missing_cost_count.
토큰/count는 strict int 또는 토큰미확인 null, USD는 정확한문자열9 또는null이다. report.json의 work_costs.summary도 USD 문자열을 유지한다.
표시용 USD float 변환은 W&B summary/Table에만 적용한다. HTML은 문자열을 포맷하고 원본 JSON 정확값을 검증한다.
W&B 새 work 필드의 count/token은 절댓값2^53 이하에서만 숫자로 게시한다. 이를 넘는 개별 값이나 집계가 있으면 WORK_WANDB_COUNT_RANGE로 network/init 전 거절하며 원본 JSON/HTML은 정확한 정수를 유지한다. 기존 표 숫자 계약은 변경하지 않는다.
work known_usage_count는 terminal 영수증에 input/output 둘 다 있는 billable attempt 수다(실패도 관측이면 포함). local은 API 관측 수/분모에서 제외한다.
missing_usage_count=nonlocal attempt 수+아직attempt가관측되지않은 image binding 수−known_usage_count. 같은pending attempt와binding을중복계수하지않는다.
UI token관측률=known_usage_count/(known_usage_count+missing_usage_count)×100, 분모0이면null이다. 부분 usage는 known_input/output 부분합에는 포함하되 known_usage_count에는 포함하지 않는다.
input_tokens 전체합은 어느 expected billable input도 누락되지 않았을 때만, output_tokens도 독립적으로 같은 규칙. known_input/output은 관측된 값만 합산한다.
local stage row의 known/missing_usage_count는0/0이며 명시 토큰0만 보존한다. cache/reasoning 값 미관측은 null이고 관측시 input/output에 다시 더하지 않는다.
가상 E2E export는 data_kind=SYNTHETIC_FIXTURE를 명시한다. work-only 보고서는 그 분류를 따르고 실제 데이터와 혼합되면 DECLARED_DATA로 표시한다.
summary 정확키: work_count,completed_work_count,open_work_count,cancelled_work_count,completed_ad_count,estimated_total_usd,known_partial_estimated_usd,
actual_total_usd,known_partial_actual_usd,input_tokens,output_tokens,known_input_tokens,known_output_tokens,missing_usage_count,missing_cost_count,cost_per_completed_ad_usd.
고유 final artifact로 completed_ad_count를 센다. 전체작업의 실패/취소/진행중 API비용을 포함해 그 수로 나눈 값이 cost_per_completed_ad_usd이며 하나라도비용누락/분모0이면null이다.
comparison 정확키: scope=MATCHED_COMPLETED_WORKS,planned_pair_count,compared_pair_count,excluded_pair_count,excluded_work_count,
baseline,candidate,estimated_cost_reduction_pct,estimated_cost_difference_usd,excluded_pairs,reason_codes.
각 arm은 completed_work_count,total_estimated_cost_usd,estimated_cost_per_completed_ad_usd. 제외행은 task_pair_id(nullable),work_ids,reason_codes.
사유는 UNPAIRED_WORK/NOT_COMPLETED/PRODUCT_MISMATCH/QUALITY_MISMATCH/STAGES_MISMATCH/COST_MISSING/RATE_MISMATCH/DUPLICATE_FINAL_ARTIFACT/NO_COMPARABLE_PAIRS/BASELINE_ZERO_COST.
같은 project/task_pair_id/arm은 work 하나만 허용. 같은 arm에서 중복 최종artifact가 있는 쌍은 비교에서 제외한다. 비교통과쌍0이면 arm 금액·감소율 null, 기준0이면 감소율만null. 음수는증가다.
report 선택필드 work_costs와 inputs.work_dataset_sha256은 같이 있어야 한다. identity에는 정규화work_dataset을 넣는다. generation선택 여부에 따라총4가지정확형태만허용한다.
W&B summary는 위 summary키에 work_를 붙이고, comparison값은 work_comparison_estimated_cost_reduction_pct/work_compared_pair_count/work_excluded_pair_count로 저장한다.
work_costs/work_stage_usage Table은 위 순서로 각각작업당1행/단계model당1행. 모든 기존표와새표를독립검증하며 누락/변조이면INCOMPLETE.

## Detailed Implementation Plan

W0 계약/EGM: 본 파일·docs/imageTodo.md·fivecircles/work/ad-work-costs-20261103.md; HIGH/FULL. 기존예산/생성추정 불변범위만재사용.
W1: work_contracts.py,work_ledger.py,work_metrics.py와 tests/test_work_contracts.py,test_work_ledger.py,test_work_metrics.py.
W1 완료: create/idempotency/project격리/SQLite경쟁/상태완료/취소늦은응답/정밀도/누락/실패/완료pair/입력불변 tests PASS. 출력wire는 위 부록과 일치해야 한다.
W2: work_tracking.py,work_api.py; 수정 text_gateway.py,api_image_processor.py,backend_adapter.py,inference_api.py,inference_runtime.py,freeform_image.py.
W2 tests: test_work_tracking.py,test_work_api.py,test_work_integration.py 및 기존관련회귀. 실제 OpenAI SDK+MockTransport malformed structured-output/usage, raw 관측, timeout/취소, inbox/DB 실패와 복구·재호출0회, metadata 없는 이미지와 reuse hash 고정을 포함한다. 모의SDK 텍스트→이미지→검수→수정→재생성→합성→완료/재시작/API/project분리 검증.
W3: 수정cost_reporting.py,cost_wandb.py 및기존tests; docs/adWorkCosts.md,docs/finopsSavings.md. 원격W&B검증은가상전과정검증자료로명확히구분해명시게시.
W3 완료: oldbyte/hash 호환·새표/summary/정밀도/타입/두번sync·누락표/권한failure/HTMLescape·브라우저 tests PASS.
W4: synthetic examples/evaluation/ad-work-demo.json,examples/evaluation/ad-work-rates.json, examples/run_ad_work.py; tests/test_ad_work_demo.py.
전체pytest -q -W error, check_fivecircles.py,diff/secret/generated검사→명시파일커밋→작업branchpush→PRbase feature/image-model→CI/리뷰→merge→원격head/mergedstate/클린검증.
머지는사용자명시승인에따라수행하며불필요한재승인없음. 일반push만사용하고기존dirtysharedworktree는보존한다.
