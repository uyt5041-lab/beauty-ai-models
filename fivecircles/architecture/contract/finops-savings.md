# 비용 절감 측정·표시와 공통 계정 예산 계약

계약: beautyai-finops-v1 · 2026-11-03 · 설계 상태 REVIEWED (5단계 EGM; 구현 검증은 별도).
사용자 승인 범위: W&B 유지, 계정 예산만 공통 검사, 리뷰 지적 반영, 계약/TODO/단계별 구현·테스트.
실제 이미지 API·청구 계정 조회·W&B 원격 업로드·GCP 배포는 이 작업에서 실행하지 않는다.
기준 코드: feature/image-model / 93faaa848bf5f9caf2e4d361017b662a5a69a679.

## 목표와 완료 단계

같은 제품·품질 기준에서 채택 광고 한 장의 원가와 감소율을 표시한다. 이미지 범위를 기본으로 하며 텍스트 생성 로직은 변경하지 않는다.
관측 누락·채택 미확정이면 절감액은 null이다. 현재 제공 자료26건은 usage20건만 있고 실제 비용·채택 근거가 없으므로 절감 성공으로 표시하지 않는다.
구현 완료는 오프라인 계약/통합/화면 검증이다. 실제 비용 절감 입증·팀 인수는 별도 미완료 상태로 둔다.
이미지40%·FinOps40%·서비스20% 및 기존 원가/낭비 임계치는 임시 평가 정책이며 이 기능의 달러 계산식이 아니다.

## 기존 구조와 선택한 대안

실험별 ProviderAttemptStore와 singleton plan/claim/max_attempts를 그대로 유지한다. 공유 DB로 합치지 않는다.
별도 AccountBudgetStore만 여러 실험/서비스가 공유한다. W&B는 보고/비교 시스템이며 호출 허가나 비용 권위가 아니다.
기존 저장 자료의 리포트는 공유 예산 설정 없이 실행 가능하다. live 실행은 공통 예산 설정이 없으면 차단한다.
대안 판단: 실험별 예산만 유지하면 계정 합산이 안 된다. 실험 상태까지 한 DB로 합치면 singleton/claim 경계를 깨뜨린다.
별도 공유 예산 SQLite는 실험 상태를 유지하며 단일 호스트 합산만 추가하므로 채택한다.
중앙 예산 서비스는 다중 호스트가 필요할 때 재검토한다. 현재는 단일 호스트 SQLite만 지원하며 네트워크 파일시스템 사용은 지원하지 않는다.
외부 프로그램에서 같은 키를 직접 쓰는 비용은 관측할 수 없다. 보장 범위는 공통 경계를 통과한 호출과 운영자가 명시한 기초 사용액이다.
제공 예산30달러, 운영한도10달러를 코드 기본값으로 추정하지 않는다. 운영 정책 파일에 한도를 명시해야 한다.

## 공통 계정 예산 계약

서버 소유 JSON 설정: schema_version=beautyai-account-budget-v1, database_path, account_ref, period_id,
starts_at, ends_at(UTC offset 포함), hard_limit_usd, opening_spend_usd, opening_evidence_ref,
warning_ratio=0.8, stop_ratio=0.9. 금액/비율은 십진 문자열, 나머지는 비어 있지 않은 문자열이다.
account_ref는 비밀이 아닌 별칭이다. 키·이메일·임의 사용자 요청에서 계정을 받지 않는다. database_path는 설정 파일 기준으로 해석한다.
기초 사용액은 필수이며 0도 명시적 운영자 선언이다. 공급자 잔액 인증이 아니므로 상태 DECLARED_OPENING_BALANCE를 표시한다.
기간 정책은 DB에 불변 저장한다. 같은 account_ref/period_id의 다른 설정, 기간 중복은 거절한다. 기간 밖의 새 예약은 차단한다.
예산 계산액은 max(0, 현재 기간 기초액 + 현재 기간에 기록한 정산 증감액 합계) + 모든 기간의 미정산 예약 합계다.
정산 증감액은 신규 확정액 - 직전 확정액(첫 정산은0)이며, 예약 생성 기간이 아닌 정산을 기록한 활성 정책 기간에 귀속한다.
예: 새 기간 기초0, 이월 SUBMITTED5 → 계산액5. 이번 기간 최초 정산5 → 예약0+정산증가5=5. 정정6 → 증감+1로6. 정정4 → 증감-2로4.
과거 확정분은 기초액에 이미 반영됐다고 선언하며, 정정은 차액만 반영한다. 음수 정산 증감이 기초액을 초과해도 허용 예산을 한도 이상 늘리지 않는다.
이전 기간 미확인 예약은 새 기간으로 사라지지 않는다. 운영자가 새 기초액에 이 미정산 예약을 다시 넣지 않도록 문서화한다.
새 예약 후 위 합계 <= hard_limit_usd × stop_ratio일 때만 허용한다. 경고는 합계 >= hard_limit_usd × warning_ratio.
예약액은 기존 reservation_usd(명시적 양수, 최대6자리 소수)를 사용한다. 단가와 무관한 승인 여유액이며 실제 청구 상한이 아니다.
실제 비용이 예약보다 크면 정산 사실을 보존하고 EXCEEDED를 표시하며 새 예약을 차단한다. 이미 발생한 비용을 되돌리거나 낮춰 쓰지 않는다.
계정 경계는 새로운 테이블 budgets, reservations, settlements가 소유한다. 실험 원장의 schema/claim은 수정하지 않는다.
reservation 식별자는 account_ref/project_id/job_id의 canonical hash. request_hash·period·예약액을 함께 고정한다.
같은 키의 재예약은 전송 허가로 쓰지 않는다. 성공 결과 재사용은 기존 로컬 receipt를 확인한 후 예산 예약 전 반환한다.

API(서버 내부 Python): AccountBudgetPolicy는 검증된 불변 설정. AccountBudgetStore(path, policy)는 해당 정책을 DB에 configure하고 바인딩한다.
load_account_budget(config_path)->AccountBudgetStore가 설정 파일을 읽는다. build_app/processor/execute_plan의 account_budget 인자는 이 바인딩 객체다.
reserve(project_id, job_id, request_hash, amount_usd)->reservation_id;
bind_attempt(reservation_id, attempt_id)->BudgetPermit; BudgetPermit.claim(request_hash, attempt_id)->None;
settle(reservation_id, settlement_id, actual_cost_usd, evidence_sha256, supersedes_id=None)->None; snapshot(account_ref, period_id)->dict.
configure/reserve/bind/claim/settle는 BEGIN IMMEDIATE 트랜잭션. 네트워크 동안 DB 잠금을 유지하지 않는다.
예약 state는 HELD(미전송 포함)/SUBMITTED(전송 직전 claim됨)/SETTLED. HELD와 SUBMITTED는 모두 예산을 점유한다.
공통 reserve → 로컬 attempts.begin → bind → adapter에서 permit.claim → HTTP POST → 로컬 finish 순서다.
두 DB는 원자적이지 않다. 어느 단계에서 crash가 나도 앞선 공유 예약은 유지하고 자동 해제·재전송하지 않는다.
bind와 claim은 현재 정책 기간 내 미정산 HELD에만 허용한다. claim은 한 번만 원자적으로 HELD→SUBMITTED 전환한다.
SETTLED는 전송에 대해 종결 상태다. settle/claim 경쟁에서 settle가 먼저 완료되면 POST0. HELD 정산은 미전송 근거의 실제0만 허용한다.
SUBMITTED 정산은 확인된 실제 금액을 허용하며 자동 실행하지 않는다. 정정은 최신 settlement의 금액과 차이를 현재 활성 기간에 기록한다.
claim은 한 번만 가능하다. permit은 같은 account/reservation/request/attempt에 묶이며 direct live adapter도 permit 없이는 POST할 수 없다.
성공/실패/unknown만으로 공유 예약을 해제하지 않는다. 확인된 청구/미전송 근거로 trusted settle가 수행되어야 한다.
settlement는 append-only: settlement_id 재입력은 완전히 같을 때만 멱등. 정정은 현재 최신 ID를 supersedes_id로 지정한다.
최신 확정액과 정정 이력은 보존하며 기간별 증감액과 미정산 예약을 중복 합산하지 않는다. 잘못된 supersedes/다른 근거 재사용은 거절한다.
확정 비용은 USD 소수점 이하 최대9자리까지 보존하되 예산 집계는 마이크로달러 올림으로 보수 계산한다. 비용 표시값을 올림값으로 덮지 않는다.
오류는 ACCOUNT_BUDGET_REQUIRED/CONFIG_INVALID/POLICY_CONFLICT/EXCEEDED/RESERVATION_CONFLICT/STORAGE_ERROR 등의 안전 코드만 노출한다.

## 호출 경로와 관측 계약

지원 경로: inference_runtime.build_app 및 main, experiment_execution.execute_plan/experiment_runner execute,
BackendImageAdapter.generate에 주입한 ExternalImageProcessor, 새 freeform CLI. 역사 preset helper는 동일 build_app으로 연결한다.
새 account_budget 인자는 build_app/processor/execute_plan에 전달한다. runtime env는 BEAUTY_ACCOUNT_BUDGET_CONFIG,
실험/자유형 CLI는 --account-budget-config. live에서 누락이면 키 읽기·HTTP 전에 차단한다. mock은 기존 호환을 유지한다.
공유 설정을 사용한 mock도 실제 SQLite 예약/claim을 거치게 하여 경쟁·crash를 시험한다. MockTransport 밖의 임의 transport는 계속 금지한다.
자유형 CLI는 사진 파일과 prompt 파일로 기존 revision1 ExternalImageRequest를 만든다. --execute 없으면 미리보기만 만들며 POST0.
실행은 명시 job/project, 기존 env-file의 키, local attempt DB, 공유 설정을 요구한다. API SDK 직접 호출은 사용하지 않는다.
기존 revision1/2, 한국어·프리셋·managed prompt·워터마크 지시는 변경하지 않는다. 자유형 생성이 단가/예산 규칙을 우회하지 않는다.

OpenAIImagesAdapter가 ProviderImageResponse에 raw_usage를 추가한다. JSON usage만 최대64KiB/깊이8로 검증해 보존한다.
허용값은 null/비음수 유한 숫자/객체/배열. bool과 문자열 값은 거절한다. 객체 key는 ASCII [A-Za-z][A-Za-z0-9_]{0,63}.
key는 대소문자 무관 (api_?key|authorization|password|secret|credential|access_?token) 검색과 일치하면 거절한다.
usage root는 객체/깊이0이며 중첩 자식 깊이가8을 초과하면 거절한다. UTF-8 json.dumps(ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False) 바이트가65536을 초과하면 거절한다.
잘못된 usage는 PROVIDER_USAGE_INVALID, raw/normalized usage는 null. 누락 usage는 오류 없이 null. 전체 HTTP body/헤더/키는 저장하지 않는다.
기존 정규화 ImageProviderUsage 5개 필드와 ExternalImageResult 형식·과거 request hash는 유지한다.
ProviderAttemptStore에 nullable raw_usage_json, metadata_json, elapsed_seconds, account_reservation_id 컬럼을 additive migration한다.
새 metadata는 operation/model/quality/width/height/prompt_version과 request_hash만 기록한다. 시간은 execute 시작부터 응답/오류까지의 monotonic 초다.
usage를 base64/이미지 디코드보다 먼저 검증한다. OpenAIImagesError에 검증된 usage/raw_usage만 optional 관측 필드로 추가한다.
이미지 디코드 실패/예외 재포장 때 이 관측 필드를 유지하고 processor는 response 또는 안전 error 필드를 attempts.finish로 전달한다.
유효 usage가 있으면 이미지 검증/저장 실패에도 보존한다. usage 자체가 유효하지 않으면 null+PROVIDER_USAGE_INVALID로 남긴다.
기존 DB의 누락 필드는 null. 과거 데이터에 존재하지 않는 토큰·계정·청구를 만들어 넣지 않는다.
성공 재사용은 같은 실험 DB의 provider_reuse_events(event_id UUID PK, job_id, project_id, attempt_id, request_hash, created_at)로 기록한다.
ProviderAttemptStore.record_reuse(job_id,project_id,request_hash)->event_id는 성공 receipt를 트랜잭션에서 재검사하고 이벤트를 넣는다.
성공 결과/asset 확인 후 반환 전에 호출하며 기록 실패는 기존 storage 오류로 반환하고 POST하지 않는다. 재사용 조회 사건이지 전달 완료나 절감액 근거가 아니다.
새 provider attempt/예약/비용은 증가하지 않는다. get_reuse_events(job_id,project_id)->list로 조회하며 중복 응답 요청은 각각 관측 이벤트다.

## 비용 데이터와 비교 계약

독립 파일 schema_version=beautyai-cost-dataset-v1. cost_items, batches, acceptances를 가진다. original evidence 파일은 바꾸지 않는다.
공통 식별자는 제한된 비밀 아닌 문자열. 비용 금액은 USD 십진 문자열 최대9자리 소수, 0<=금액<=1,000,000,000. NaN/Inf/음수/float 직접 입력 거절.
CostItem: cost_id, revision(양수 int), supersedes_revision(nullable), source_ref, source_sha256, kind(image/text/review/compute),
category(production/research), estimated_cost_usd(nullable), rate_version(nullable), actual_cost_usd(nullable),
billing_status(UNRECONCILED/PARTIAL/RECONCILED), billing_evidence_ref(nullable), allocations({batch_id:정수 basis points}), state.
state는 SUBMITTING/SUCCEEDED/FAILED/UNKNOWN/MISSING/RECORDED. 마지막 값은 외부 비용 항목이며 나머지는 시도 관측 상태다.
어떤 state도 생산비 합계에서 제외하지 않는다. 성공이 아니어도 비용이 있을 수 있고 누락/MISSING은 비용0으로 처리하지 않는다.
production allocations 합계는10000. research는 allocations={}이고 비교 생산비에서 제외하지만 연구 부분합을 따로 표시한다.
한 cost_id의 첫 revision1, 이후 n은 n-1을 supersede. 동일 revision의 동일 내용은 중복제거, 다른 내용은 오류. 최신 revision만 계산한다.
actual 금액을 쓰려면 billing_evidence_ref 필수. PARTIAL은 확인된 부분합을 보존하지만 전체 실제 원가에는 사용하지 않는다.
estimated 금액이 있으면 rate_version 필수. 원시 usage→추정은 알려진 model/rate snapshot만 사용한다. 할인 포함 증거가 없으면 추정임을 표시한다.
분할은 정확한 Decimal로 계산하고 9자리 아래는 버린 뒤 잔여를 batch_id 정렬의 마지막 항목에 더한다. 배분 합계는 원액과 같다.
Batch: batch_id, product_id, expected_cost_ids(고유 목록). 등록된 production allocations와 기대 비용 목록은 정확히 같아야 한다.
Acceptance: ad_id, revision, supersedes_revision, batch_id, action(accept/revoke), artifact_sha256, reviewer_ref, quality_policy_version, quality_pass(bool).
같은 ad_id는 같은 batch에 속하고 최신 상태만 사용한다. revoked/quality_fail은 채택 수0. 같은 arm의 동일 artifact hash는 한 장만 센다.
복사/다운로드/내보내기 크기 변경은 같은 ad_id로 기록한다. 자동으로 모델 성공을 사용자 채택으로 바꾸지 않는다.

Comparison manifest: schema_version=beautyai-cost-comparison-v1, comparison_id, accounting_window_id, cost_scope(list kind),
quality_policy_version, estimate_rate_version, pairs([{product_id,baseline_batch_id,candidate_batch_id}]).
제품별 한 짝, batch는 중복/양군 공유 불가. 각 쌍의 제품 ID·품질정책과 scope가 일치해야 한다. 각 쌍 양쪽 모두 채택>=1 필요.
선택한 각 batch에는 비용 근거가 하나 이상 필요하다. 빈 expected_cost_ids는 무료 생산의 근거가 아니므로 NO_COST_ITEMS로 차단한다.
CostItem이 scope 밖이면 제외하지 않고 범위 오류로 차단한다. 누락 비용은 비용0이 아니다. 알려진 부분합은 coverage와 함께 표시한다.
권위는 DECLARED_SCOPE/DECLARED_QUALITY이며 실제 전 계정 완전성·사람 검수 진위를 자동 인증하지 않는다.
각 arm의 총생산비/고유채택수가 설명용 원가다. headline 감소율은 제품별 채택수 비중까지 같아야 한다.
모든 pair에서 baseline_count_i*candidate_total == candidate_count_i*baseline_total을 검사하고 다르면 PRODUCT_MIX_MISMATCH로 감소율/환산차이 null.
양군 모두 비용 완전성·품질·표본·분모·제품비중 조건을 통과한 basis별로만 감소율 계산한다.
actual은 전 항목 RECONCILED+근거+금액 필요. estimated는 전 항목 추정+같은 rate_version 필요. basis를 섞지 않는다.
B=기준군 총생산비/고유채택수, C=개선군 총생산비/고유채택수이며 같은 basis를 쓴다.
(B-C)/B*100은 unit_cost_reduction_pct. (B-C)*개선군채택수는 counterfactual_difference_usd이며 실제 청구 감소액이 아니다.
기준0/채택0/누락/부분청구/품질불일치면 해당 basis의 총액·원가·감소율 null, reason_codes 반환. 부분합·누락수는 보존한다.
음수 감소율은 비용 증가로 표시한다. 재사용 횟수를 환산 절감액에 더하지 않는다. 연구비를 포함한 순이익으로 표시하지 않는다.

## 수집·표시·W&B

collect_generation_results(root, group_id, category=research): 명시 root의 */generation-result.json만 읽는다.
기존 preset10+revision10+freeform6 형식을 지원하며 legacy:<group>/<case> ID로 중복을 막는다. source hash/미확인을 유지한다.
collect_attempt_snapshot(sqlite_path, source_id): URI mode=ro + read transaction에서 provider_attempts 전체를 읽되 SQL 컬럼은 명시한다.
submitting/unknown/failed도 포함. 원장을 여는 수집기로 테이블 migration/키 읽기/API 실행을 하지 않는다. 원본 DB+WAL 바이트 대신 추출 snapshot canonical hash를 기록한다.
canonical attempt ID가 같으면 같은 비용 후보로 취급한다. project_id를 포함한 관측 지문을 대조한 뒤 중복을 제거한다. 불일치하는 여러 snapshot은 충돌로 차단하고 임의로 최신이라 추정하지 않는다.
legacy와 terminal 데이터를 연결할 attempt ID가 없으면 두 입력을 같은 호출이라고 추정하지 않는다. source 목록을 사용자에게 표시한다.
historical_estimate의 known snapshot 반환값만 Decimal(str(value))를 소수9자리로 변환하며 rate_version=historical-b12-2026-10-23을 부여한다.
RECORDED_IN_SOURCE인데 단가 근거가 없으면 원본 source_ref/hash는 유지하되 정규화 estimated_cost_usd/rate_version=null로 둔다. 근거를 만들어 넣지 않는다.
새 비용 수집기는 기존 experiment_records/collect_trial의 과거 형식·해시를 변경하지 않는다. 기존 terminal 수집기와 별개로 중단 snapshot을 받는다.

cost_reporting CLI는 dataset/comparison→report.json와 index.html을 생성한다. comparison 생략 시 수집 현황만 표시하고 감소율·환산차이는 null인 overview를 만든다. 수집 CLI는 명시 소스에서 dataset을 만들어 편집 가능한 채택/비교 예시를 함께 낸다.
화면: 비용 범위, 기준/개선의 부분합·원가, 추정/청구 구분, 감소율, 채택 수·누락 수·연구비·사유. HTML 사용자값은 escape한다.
comparison report ID는 canonical 입력 dataset+manifest+계약 버전 hash. import 시간으로 바꾸지 않는다. 과거 W&B export v1은 변경하지 않는다.
새 W&B 비교 업로드는 --wandb로 명시하며 기존 ensure_private_project를 재사용한다. model/API 모듈을 import하지 않는다.
W&B run_id는 compare-<report hash prefix>, report.json 파일 hash·summary·비교 table을 저장. 재실행 시 원격 동일 파일을 먼저 확인하며 같으면 재업로드하지 않는다.
원격 확인 실패를 완료로 쓰지 않는다. receipt는 PREPARED/UPLOADED/READBACK_VERIFIED/INCOMPLETE. 저장자료 재동기화에 이미지 API0회.
원본 사진 업로드·계정 청구 API·팀 전체 cost gate는 별도 기존 경로/담당 범위다.

## Detailed Implementation Plan

### B0 계약·검토
- 본 계약, docs/imageTodo.md, fivecircles/architecture/contract/wandb-experiments.md, fivecircles/requirements/decisions.md,
  fivecircles/work/finops-relay-20261103.md, fivecircles/work/update.md. docs/wandbExperiments.md에 새 사용법 링크.
- 로컬 evidence는 outputs/finops-relay-20261103/review/ 아래 번호별 md/receipt.json,
  flow/target.mmd,target.png,current.mmd,current.png,index.html. 테스트 출력은 같은 evidence root의 단계별 txt.
- HIGH/FULL. 이전 리뷰의 문제 목록을 재사용하지만 새 계약은 별도5단계 검토. 미해결 필수 지적은 구현 전 수리 확인.

### B1 계정 예산 저장소
- 신규 beauty_ai/account_budget.py, tests/test_account_budget.py. 다른 파일 변경 없음.
- configure/reserve/bind/claim/settle/snapshot과 JSON policy loader. 실험 DB를 열거나 변경하지 않는다.
- SQLite 파일 기반 다중 프로세스 경쟁, restart/정책충돌/만료/기간이월/정정/초과/중복claim/DB실패를 테스트.
- 완료: 예산0·설정오류·중복일 때 허가0, 서로 다른 실험 상태는 공유하지 않음, 실제 요금 상한 주장 없음.

### B2 전송 경계·관측
- 수정 beauty_ai/openai_images.py, provider_attempts.py, api_image_processor.py, inference_runtime.py,
  experiment_execution.py, experiment_runner.py, scripts/compare_cosmetics_presets.py; 신규 beauty_ai/freeform_image.py.
- tests/test_account_budget_integration.py, test_provider_observations.py, test_freeform_image.py 추가. 기존 관련 테스트 회귀.
- 위 함수/환경/CLI 경로 연결, raw usage·metadata·elapsed·reuse. 기존 result/request/public API 스키마는 유지.
- FakeTransport로 freeform/기존 preset/Korean 경로·직접adapter permit필수·설정누락 POST0·두 DB crash hold·응답 검증 실패 usage 보존.
- 과거DB additive migration과 기존 raw없음 조회 호환. 유료 호출 없이 완료한다.

### B3 비용·수집·비교
- 신규 beauty_ai/cost_accounting.py(엄격 schema/금액/정정·배분), cost_collection.py(원본→정규화), cost_savings.py(순수 비교).
- tests/test_cost_accounting.py, test_cost_collection.py, test_cost_savings.py. 기존 historical_estimate 함수 재사용, 복제 금지.
- 숫자 fixture: baseline 생산비2.0/채택4=0.5, candidate1.5/채택5=0.3 →40%, 환산차이1.0(실제지출차이0.5와 구별).
- 누락/부분청구/취소/정정/같은사진중복/양군샘플차이/기준0/음수감소율/분할잔여/과거20+미확인6/중단DB snapshot을 시험.
- 제품A/B 단가는 양군같고 baseline채택1/9, candidate9/1인 fixture는 합계가 낮아져도 PRODUCT_MIX_MISMATCH로 감소율 null.
- 기대값은 동일 fixture로 재현하며 이미지·W&B 네트워크·키 접근0.

### B4 표시·W&B·최종검증
- 신규 beauty_ai/cost_reporting.py, cost_wandb.py; tests/test_cost_reporting.py, test_cost_wandb.py.
- docs/finopsSavings.md 사용법과 examples/evaluation/finops-dataset.json, examples/evaluation/finops-comparison.json synthetic 예시; 실제 사진·생성물·DB·비밀은 Git 제외.
- 기존 W&B helper/private 경계 재사용. fakeSDK+fakeAPI로 2회동기화/중복·실패·hash불일치/읽기전용 비교 검증.
- 실제26건은 로컬 보고서만 만든다. 채택·실비 미확인은0으로 메우지 않는다.
- 전체 pytest -q -W error, check_fivecircles.py, git diff --check. Mermaid target/current PNG+HTML 및 브라우저 표기·이미지 로드 확인.
- 각 B의 테스트 통과·기록 후 다음 B로 이동한다. 실제 유료 효과실험/청구/원격업로드는 NOT_RUN으로 남긴다.

## 구조·보안·배포 경계

의존: 원장/전송은 account_budget으로, 수집/비교/보고는 cost_accounting으로 향한다. 순수 계산에서 모델/W&B/원장을 호출하지 않는다.
중복 단가 계산은 기존 historical_estimate를 재사용한다. 프롬프트 편집과 비용 경계는 독립이며 모델 제공자 자동 선택은 하지 않는다.
NOW_REQUIRED: secrets 비노출, 서버 계정 설정, 허가전 POST차단, raw usage 제한, W&B PRIVATE검증, HTML escape.
DEFERRED: 다중호스트 중앙권위, 외부 공개 전 서빙 담당의 정책변경 권한/테넌트 통합시험, 운영전 담당의 원장복원시험.
현재 배포 노출은 미확인이다. 기존 프로젝트에 실공개 경로가 있으면 위 통제를 생략할 근거가 아니다.
이관은 새 공유 원장에 명시 기초액을 설정하고 갱신된 실행기만 사용하는 cutover다. 과거 실험 DB 자동 합산/계정추정은 하지 않는다.
기존 미확인 지출을 기초액에 보수 반영한 운영자 근거가 필요하다. 필수 기초액·근거 참조·정책 유효성 검증을 통과해야 live를 활성화하며, 공급자 잔액 자동 인증은 요구하거나 주장하지 않는다.
롤백 시 새 live 경로를 먼저 비활성화한다. 공유 DB/새 관측 컬럼은 삭제하지 않는다. 구버전 live로 돌아가 guard를 우회하는 rollback은 지원하지 않는다.
Git/원격 배포는 별도 권한 범위. 본 작업은 로컬 구현·검증이며 외부 전송·merge 권한을 늘리지 않는다.

생성당 추정비용과 완료까지 토큰 측정은 [추가 계약](finops-generation-estimate.md)을 따른다. 기존 채택 광고 원가와 구분한다.
