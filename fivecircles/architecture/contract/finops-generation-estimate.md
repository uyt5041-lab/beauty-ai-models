# 생성당 추정비용 표시와 완료까지의 측정 계약

2026-11-03 · 상태 DESIGN_REVIEWED · beautyai-generation-comparison-v1.
기존 finops-savings의 계정 예산·채택 광고 원가 계약은 유지한다. 이번 변경은 저장 자료의 생성당 추정 비교와 화면/W&B 표시다.
현재 feature/image-model의 미커밋 FinOps 작업 위에 추가한다. 유료 이미지 호출·push·PR·배포는 하지 않는다.
W&B는 구현과 가짜 SDK 검증 후 기존 로그인 계정의 PRIVATE 프로젝트에 저장 보고서를 명시 동기화한다. 실제 원격 게시 결과는 readback 수령증 없으면 미완료다.

## 목표와 비교 단위

생성 결과 1건의 추정비용 비교를 먼저 표시한다. 실제 청구나 채택 광고 원가로 이름을 바꾸지 않는다.
누락 비용을 0으로 넣지 않고 비교에서 제외하며 비교/제외 건수와 이유를 함께 표시한다.
생 프롬프트에서 수정·검수를 거쳐 최종 광고를 완료하는 비용은 아래 후속 계약의 목표다. 지금 단일 생성 비교로 그 효과를 주장하지 않는다.
기존 26건 중 baseline=preset10(첫 프리셋), candidate=goldfish10(수정 프리셋)으로 case ID를 연결하고 freeform6은 미배정으로 제외한다. 배열 순서를 매칭 근거로 쓰지 않는다.
같은 모델·quality·size·제품 사진은 저장 근거에서 확인했으나 프롬프트/카피 일부와 품질은 달라 관측 비교다.
이전10건0.412130, 수정10건0.407115 USD. 생성당0.041213→0.0407115, 감소율1.216849052%, 합계 차이0.005015.

## 입력·제외·계산

generation manifest(schema_version=beautyai-generation-comparison-v1): comparison_id, estimate_rate_version, pairs.
계산 결과 schema_version은 beautyai-generation-estimate-v1이다.
pairs는 [{pair_id, baseline_cost_id, candidate_cost_id}]이며 비어 있지 않아야 한다. 모든 식별자는 cost_accounting.validate_identifier를 사용한다.
pair_id 고유, cost ID는 양군 전체에서 한 번만 사용한다. 없는 ID·중복·잘못된 schema는 ValueError(COST_GENERATION_*).
정규화 dataset의 최신 cost_items를 읽는다. pairs는 비교할 조건을 운영자가 명시한 연결이며 자동 품질 인증이 아니다.
포함 조건: 양쪽 모두 kind=image, state=SUCCEEDED, estimated_cost_usd 존재, rate_version이 manifest와 같음.
한쪽이 조건을 못 맞추면 그 쌍의 두 기록을 모두 제외한다. 나머지 미배정 dataset 기록도 제외 수에 포함한다.
제외 record의 자체 위반은 COST_MISSING / RATE_MISMATCH(비용이 있을 때만) / NOT_SUCCEEDED / NOT_IMAGE로 기록한다.
정상인 상대만 PAIRED_RECORD_EXCLUDED를 받는다. 양쪽 위반이면 각자 자체 사유만 받는다. 미배정은 UNPAIRED_RECORD와 자체 위반 사유를 함께 받는다.
결과 reason_codes는 제외 사유 합집합이며 유효 쌍의 계산을 막지 않는다. 계산불가 사유 NO_COMPARABLE_PAIRS/BASELINE_ZERO_COST는 추가한다.
비교 건수=포함 pair 수×2, 제외 건수=dataset 최신 전체 기록 수−비교 건수. 포함/제외 합계가 전체와 같아야 한다.
coverage_pct=비교 기록 수/dataset 최신 전체 기록 수×100. 전체 기록0이면 null. pair 포함률과 혼동하지 않는다.
비교 쌍0이면 모든 원가/감소율 null. 기준군 평균0이면 감소율만 null/BASELINE_ZERO_COST. 음수 감소율은 비용 증가다.
각 arm 합계/포함 생성 수=생성당 평균. (기준 평균−수정 평균)/기준 평균×100=생성당 추정비용 감소율.
합계 차이는 포함된 동일 수의 결과에서 기준 합계−수정 합계다. 실제 청구 감소액이나 최적화의 인과 효과가 아니다.
금액/파생값은 기존 Decimal·usd_string의9자리 문자열을 재사용하며 계산은 고정 고정밀 context를 사용한다.

## 출력·화면·W&B

compare_generations(dataset, manifest)->dict: schema_version, comparison_id, estimate_rate_version,
input_record_count, planned_pair_count, compared_pair_count, compared_record_count, excluded_pair_count, excluded_record_count,
coverage_pct, baseline/candidate {generation_count,total_estimated_cost_usd,estimated_cost_per_generation_usd},
estimated_cost_reduction_pct, estimated_cost_difference_usd, per_generation_difference_usd,
excluded_records [{cost_id,reason_codes}], reason_codes, assurance=OBSERVATIONAL_NOT_QUALITY_CONTROLLED.
생성 비교는 기존 채택 광고 비교와 독립이다. 연구 category도 이 진단 비교에는 허용하며 생산비로 재분류하지 않는다.
build_report/write_report에 keyword-only generation_comparison=None, CLI에 --generation-comparison PATH 추가.
전달 없으면 과거 report.json의 필드·ID·바이트/기존 W&B 비교를 유지한다. 전달 시 report에 generation_comparison 필드와 identity 입력을 추가한다.
report.inputs에 선택 입력이 있을 때만 generation_comparison_sha256(64자리 소문자 hex)를 추가한다.
W&B는 기존 top-level 필드+기존 inputs2필드 또는 generation_comparison 추가+inputs3필드의 두 형태만 허용한다. 선택 결과는 null이 아닌 유효 객체여야 한다.
선택 manifest는 pair_id 순으로 정규화해 해시한다. 같은 입력은 같은 보고서 ID이고 다른 매칭/단가 선택은 다른 ID다.
한국어 화면: 생성당 추정비용 감소율, 양군 합계·평균, 비교 기록·쌍 수, 제외 수·사유·coverage, 관측 비교/품질 미검증 표시.
제외 자료가 있어도 유효한 쌍이 남으면 수치를 표시한다. 비용이 있는 기록만 남긴다는 이유로 한쪽 분모만 바꾸지 않는다.
새 W&B Table은 보고서당1행. 순서대로 comparison_id, estimate_rate_version,
baseline_generation_count, candidate_generation_count, baseline_total_estimated_cost_usd, candidate_total_estimated_cost_usd,
baseline_estimated_cost_per_generation_usd, candidate_estimated_cost_per_generation_usd, estimated_cost_reduction_pct,
estimated_cost_difference_usd, per_generation_difference_usd, input_record_count, planned_pair_count, compared_pair_count,
compared_record_count, excluded_pair_count, excluded_record_count, coverage_pct, assurance, reason_codes.
앞의 arm 값은 결과 baseline/candidate 객체에서, 나머지는 결과 최상위에서 읽는다. counts는 int, 금액/비율은 표시용 float 또는 null,
식별자/assurance는 문자열, reason_codes는 정렬된 코드를 쉼표로 잇는다. 정확한 십진 문자열은 report.json에 보존한다.
summary는 각 열 이름에 generation_을 붙인 동일 값이다. 예: generation_estimated_cost_reduction_pct, generation_compared_record_count, generation_excluded_record_count.
별도 generation_cost_estimate Table을 기록하고 기존 comparison Table을 유지한다. 원격 파일·새 표·summary를 검증해야 READBACK_VERIFIED다.
W&B SDK가 summary의 null 값을 생략할 수 있다. summary에서 예상값이 null인 누락만 null과 동등하게 읽는다.
알려진 값 누락/변경과 null→0은 실패다. config/artifact/정확한 report.json/두 Table의 검증은 완화하지 않는다.
독립 표의 artifact 이름/키를 보존하며 가짜 SDK도 Table별 실제 key를 사용한다. 기존 comparison 표와 새 generation_cost_estimate 표를 각각 누락·변조 시험한다.
원격 게시가 실패하면 INCOMPLETE, CLI nonzero. 재동기화는 저장 자료만 사용하며 이미지 API0회다.

## 후속: 생 프롬프트→최종 완료 측정

측정 단위는 같은 제품·동일한 제작 목표를 가진 work_id 한 건이다. 최종 광고 하나를 목표로 하며 수정·실패·검수 호출도 같은 work_id에 속한다.
비교는 baseline(생 프롬프트 흐름)과 candidate(개선 흐름)을 task_pair_id로 연결한다. 같은 입력·완료/품질 기준·비용 범위를 먼저 고정한다.
단계는 prompt_build/image_generation/review/revision/composition이다. 각 유료 요청마다 고유 attempt_id와 provider_request_id를 남긴다.
연결 필드: work_id, task_pair_id, arm, product_id, stage, attempt_id, parent_attempt_id, model, prompt_version/hash,
usage(input/output 및 image/text/cache 세부), rate_version, estimated_cost_usd, actual_cost_usd(nullable), status, elapsed_seconds, source_hash.
사람이 쓴 생 프롬프트의 로컬 토큰화 예상값은 local_text_tokens_estimate로 별도 표시한다. API 청구 usage로 합산하지 않는다.
매 호출의 API usage를 누적한다. total_tokens와 input+output을 중복 합산하지 않고 cache/image/text 세부는 부분집합으로 보관한다.
사용 모델별/단계별 input/output을 먼저 합산한다. 서로 다른 모델의 토큰 수는 동일 비용 단위가 아니므로 주 비교는 전체 추정비용이다.
상태 OPEN/COMPLETED/CANCELLED. COMPLETED에는 최종 artifact_sha256과 사용자 채택/합격 기준 근거, expected attempt 목록이 필요하다.
미확인 attempt/usage는0으로 두지 않는다. 전체 완료 비용은 누락이 있으면 null, 알려진 부분합·관측률을 따로 표시한다.
완료 광고당 원가=같은 work_id의 실패/수정/검수 포함 비용 합계. 비교는 양쪽이 완료된 task_pair만 사용하고 제외된 work 수도 표시한다.
생성 이미지 usage만으로 과거 텍스트·검수 비용을 복구했다고 주장하지 않는다. 기존26건의 전체 제작 토큰은 미확인이다.
이미지(pi)는 work/attempt 연결과 이미지 usage, 텍스트 담당은 프롬프트/카피/검수 usage 제공, 서빙은 동일 work_id 전달이 필요하다.
위 담당 연동은 협의 요청사항이며 팀 합의·실연결 완료가 아니다. 이번에는 계약·TODO만 추가하고 실행 계측은 후속이다.

## 구조·보안·대안

현재 로컬 저장자료 처리. 새 권한/계정 예산 경계 변화 없음. HTML escape·안전한 ID·명시 파일 입력·PRIVATE W&B 검사를 재사용한다.
대안: 누락이 하나라도 전체표시 차단은 관측 추정에 과함. 한쪽만 제외하면 제품/조건 비중이 달라짐. 명시 pair의 양쪽 제외를 채택한다.
새 순수 generation_costs 모듈이 계산을 소유한다. 보고서는 전달/표시, W&B는 명시 업로드만 맡는다. 제공자 SDK·모델 호출을 계산에 넣지 않는다.
기존 usd/helpers·latest_records·보안 validator·W&B private/readback을 재사용한다. 별도 서비스·DB·단가표는 추가하지 않는다.
리소스는 저장자료 파싱·인덱스 집계 O(N+P), 정규화/출력 정렬 O(N log N+P log P)다. 비밀/사진/프롬프트 원문은 새 비교 계약에 넣지 않는다.
노출 단계는 이번 도구 LOCAL_PROTOTYPE; 배포 환경은 미확인. 외부 공개 전 정책 변경/데이터 접근 권한 시험은 서빙 담당 후속이다.

## Detailed Implementation Plan

### G0 계약·리뷰
- 본 파일, finops-savings.md 링크, docs/imageTodo.md, fivecircles/work/finops-generation-20261103.md.
- HIGH/FULL FRESH: 지표 의미·선택 스키마 추가. 이전 예산/채택 계약은 불변 범위로만 재사용.
- 원문/해시/5단계 결과는 outputs/finops-generation-20261103/review/에 기록한다.
### G1 순수 계산
- beauty_ai/generation_costs.py, tests/test_generation_costs.py.
- 완전20+미배정6, 한쪽 비용누락의 쌍 전체 제외, 단가/상태/중복/없는ID/0/음수/정정/정밀도·입력불변 시험.
### G2 화면·W&B
- beauty_ai/cost_reporting.py, cost_wandb.py, tests/test_cost_reporting.py, test_cost_wandb.py.
- 기존 byte/hash 회귀와 선택입력 해시, 한국어 표시, fakeSDK의 새 표·summary·원격 불일치/재동기화 검증. 잘못된 선택 top/input/결과 형태를 인증 전 거절하고 기존 형태를 허용한다.
### G3 실자료·문서·최종 검증
- docs/finopsSavings.md, docs/imageTodo.md, fivecircles/work/finops-generation-20261103.md, fivecircles/work/update.md.
- 입력 원본 outputs/finops-relay-20261103/saved-evidence/dataset.json(SHA256 6c2a8926d925bdbec9f5d4f3ec2e8747769cca57cb27fd008359204436265ad2).
- 이 파일을 outputs/finops-generation-20261103/dataset.json에 그대로 복사한다. source_ref= generation:preset/{case}/generation-result.json을 baseline,
  generation:goldfish/{case}/generation-result.json을 candidate로 사용한다. case는 P01-1/P01-2부터 P05-1/P05-2까지10개다.
- outputs/finops-generation-20261103/generation-comparison.json에 위 실제 cost_id10쌍을 저장한다. 원본/과거 보고서는 보존한다.
- 명령: .venv/bin/python -m beauty_ai.cost_reporting --dataset outputs/finops-generation-20261103/dataset.json --generation-comparison outputs/finops-generation-20261103/generation-comparison.json --output outputs/finops-generation-20261103/report
- 산출물은 report/report.json, report/index.html, report/cost-wandb-sync.json(명시적 prepare/sync시), browser-check.json이다.
- 예상20건 비교/6건 제외/10쌍, 감소율1.216849052와 비용0.005015을 확인한다. 화면 브라우저 검증·전체 pytest -q -W error·설정/diffcheck.
- 순서: G1 PASS→G2 PASS→G3. 후속 work별 전체 여정 계측/실제 효과는 미완료로 남긴다.
