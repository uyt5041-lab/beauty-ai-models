# 비용·절감 비교 사용법

W&B는 실험 결과를 보는 곳이다. **계정 예산 검사는 호출 전에 별도 SQLite에서 한다.** 실험별 작업 상태·호출 횟수·성공 결과는 기존 원장에 남는다. 여러 실험이 같은 계정 예산 DB를 사용하면 예약액을 합산한다.

이 기능은 공통 실행기를 거친 호출과 운영자가 선언한 기초 사용액을 다룬다. 다른 프로그램이 같은 API 키를 직접 사용한 비용까지 자동으로 알지는 못한다. 단일 서버의 로컬 SQLite를 지원한다. 여러 서버나 네트워크 드라이브를 하나의 예산 DB로 쓰지 않는다.

## 1. 저장 자료로 먼저 비교한다

저장소 루트에서 실행한다. 아래 예시는 **계산 검증용 가상 자료**다. 실제 비용 절감 실적이 아니다.

```bash
.venv/bin/python -m beauty_ai.cost_reporting \
  --dataset examples/evaluation/finops-dataset.json \
  --comparison examples/evaluation/finops-comparison.json \
  --output outputs/finops-demo
```

`outputs/finops-demo/index.html`을 연다. `report.json`에는 정확한 십진 문자열과 계산을 막은 사유가 있다. 비교 입력이 같으면 보고서 ID도 같다.

예시의 기준 비용은 2달러, 채택 수는 4장이다. 개선 비용은 1.5달러, 채택 수는 5장이다.

| 표시 | 계산 | 뜻 |
|---|---|---|
| 기준 장당 원가 | 2 ÷ 4 = $0.50 | 채택한 한 장을 얻는 데 든 비용 |
| 개선 장당 원가 | 1.5 ÷ 5 = $0.30 | 실패 비용도 포함한 장당 원가 |
| 장당 원가 감소율 | (0.50 − 0.30) ÷ 0.50 = 40% | 같은 기준에서 비교한 감소율 |
| 동일 수량 환산 차이 | (0.50 − 0.30) × 5 = $1.00 | 개선 수량 5장으로 맞춘 계산값 |
| 실제 두 묶음의 지출 차이 | 2 − 1.5 = $0.50 | 위 환산 차이와 다른 값 |

환산 차이를 실제 청구 절감액으로 쓰지 않는다. 기준 원가가 0이거나, 채택·비용·품질 근거가 부족하면 감소율은 `null`이다. 음수 감소율은 비용 증가다.

## 2. 기존 결과를 수집한다

수집기는 이미지 API를 호출하지 않는다. 명시한 폴더의 `*/generation-result.json`만 읽는다. 과거 프리셋·자유형 결과와 새 자유형 실행 결과를 지원한다.

```bash
.venv/bin/python -m beauty_ai.cost_collection \
  --generation-root presets=/path/to/saved-presets \
  --generation-root freeform=/path/to/saved-freeform \
  --attempt-db trial=/path/to/provider_attempts.sqlite3 \
  --output outputs/finops-import/dataset.json
```

실패하거나 중단된 호출도 비용이 들 수 있다. DB 수집은 `submitting`, `unknown`, `failed` 상태까지 읽는다. DB는 읽기 전용으로 열고 테이블을 변경하지 않는다.

수집 결과에는 원본 hash 목록과 채택·비교 입력용 빈 예시 파일이 함께 생긴다. 같은 attempt의 같은 관측은 한 번만 센다. 서로 다른 내용이면 충돌로 멈춘다. 연결할 attempt ID가 없는 파일은 같은 호출이라고 추정하지 않는다.

자동 수집한 비용은 기본적으로 연구비다. 생산비로 사용하려면 담당자가 제품·묶음·배분·채택 근거를 입력한다. 생성 성공을 자동으로 채택으로 바꾸지 않는다.

비교 자료가 아직 없으면 수집 현황만 만든다.

```bash
.venv/bin/python -m beauty_ai.cost_reporting \
  --dataset outputs/finops-import/dataset.json \
  --output outputs/finops-import/report
```

2026-11-03에 확인한 기존 자료는 26건이다. 20건의 당시 단가 기준 추정 부분합은 **$0.819245**다. 6건은 비용을 모르며, 26건 모두 확인된 실비·채택 근거가 없다. 따라서 전체 제작비나 절감 성과로 표시하지 않는다. 사진·생성물·원본 실험 자료는 Git에 넣지 않는다.

## 3. 비용과 채택을 입력한다

입력 계약은 [finops-savings](../fivecircles/architecture/contract/finops-savings.md)에 있다. 금액은 숫자형 float 대신 `"0.062000000"`처럼 USD 문자열로 쓴다. 소수점 아래 최대 9자리다.

| 입력 | 작성 기준 |
|---|---|
| `cost_items` | 이미지·텍스트·검수·컴퓨트 비용을 각각 기록한다. 실패·수정 비용도 포함한다. |
| `category` | 생산비는 `production`, 계획한 연구비는 `research`로 쓴다. |
| `allocations` | 생산비를 묶음별로 배분한다. 정수 비중 합계는 10000이다. 연구비는 빈 객체다. |
| `batches` | 제품 ID와 예상 비용 ID를 적는다. 배분된 비용 목록과 같아야 한다. |
| `acceptances` | 검수한 광고 ID·파일 hash·검수자 별칭·품질 기준·합격 여부를 적는다. |
| `comparison` | 같은 제품의 기준/개선 묶음, 비용 범위, 품질 기준, 추정 단가 버전을 고른다. |

비용과 채택의 변경은 이전 행을 지우지 않고 다음 `revision`을 추가한다. `supersedes_revision`은 직전 번호다. 계산에는 최신 상태만 쓴다. 채택 취소는 `action=revoke`다. 같은 결과 파일의 복사본은 채택 수를 늘리지 않는다.

실비를 적으려면 `billing_evidence_ref`가 필요하다. 일부만 확인했으면 `PARTIAL`로 둔다. 전체 실비 비교에는 모든 비용의 `RECONCILED` 상태·금액·근거가 필요하다. 추정 비교는 같은 단가 버전을 사용한다. 과거 단가 근거가 없는 저장 추정액을 확정 단가로 승격하지 않는다.

누락 항목은 `state=MISSING`, 비용은 `null`로 적는다. 누락 ID를 목록에서 빼거나 비용을 0으로 채우지 않는다. 제품별 채택 비중이 다르면 `PRODUCT_MIX_MISMATCH`로 감소율을 막는다. 값싼 제품이 많아진 효과를 최적화 효과로 계산하지 않기 위해서다.

## 4. 계정 공통 예산을 설정한다

설정 파일은 서버 담당자가 관리한다. 요청 사용자가 계정·한도·DB 경로를 고르게 하지 않는다. 키는 이 파일에 넣지 않는다. 아래 금액 두 곳은 **설명용 placeholder**다. 운영자가 한도와 기초 사용액을 확인한 뒤 교체해야 한다.

```json
{
  "schema_version": "beautyai-account-budget-v1",
  "database_path": "./account-budget.sqlite3",
  "account_ref": "example-account",
  "period_id": "2026-11",
  "starts_at": "2026-11-01T00:00:00+09:00",
  "ends_at": "2026-12-01T00:00:00+09:00",
  "hard_limit_usd": "REPLACE_WITH_APPROVED_USD",
  "opening_spend_usd": "REPLACE_WITH_CONFIRMED_OPENING_USD",
  "opening_evidence_ref": "operator-record-20261103",
  "warning_ratio": "0.8",
  "stop_ratio": "0.9"
}
```

기초 사용액이 미확인이면 0을 넣지 않는다. 예산 정책은 최초 등록 후 같은 기간에 덮어쓸 수 없다. 먼저 실제 값을 정한다. 과거 미정산 예약은 자동 이월되므로 새 기초액에 같은 예약을 다시 더하지 않는다.

계산액은 `max(0, 기초액 + 이번 기간 정산 증감액) + 모든 기간의 미정산 예약`이다. 경고 비율과 신규 예약 중단 비율은 설정값을 따른다. 예약액은 청구 상한이 아니다. 실제 비용이 예약보다 크면 사실을 저장하고 새 예약을 막는다.

| 연결 경로 | 전달 값 |
|---|---|
| 서버 실행 | `BEAUTY_ACCOUNT_BUDGET_CONFIG=/path/to/account-budget.json` |
| 실험 CLI | 기존 execute 명령에 `--account-budget-config /path/to/account-budget.json` 추가 |
| Python 연결 | `load_account_budget(path)` 결과를 `build_app`·`execute_plan`·`ExternalImageProcessor`의 `account_budget`에 전달 |
| 자유형 CLI | `--account-budget-config` 필수 |

계정 DB와 `provider_attempts.sqlite3`는 다른 파일이어야 한다. 모든 실험은 같은 계정 DB 경로를 사용하되 각 실험의 원장은 유지한다. 현재 서버와 구버전 실행기를 함께 쓰면 구버전 경로까지 통제한다고 볼 수 없다.

예약은 `HELD → SUBMITTED → SETTLED`로 진행한다. `claim`은 한 번만 성공한다. 성공·실패·시간 초과만으로 예약을 풀지 않는다. 청구 또는 미전송 근거가 확인된 뒤 운영자가 `AccountBudgetStore.settle`로 정산한다. 정정은 최신 settlement ID를 `supersedes_id`로 지정한다. 실제 금액과 증거 hash가 필요하다.

계정 현황은 `store.snapshot(account_ref, period_id)`로 읽는다. 이 값의 기초액은 운영자 선언이며 공급자 잔액 인증이 아니다.

## 5. 자유형 요청도 같은 경계를 쓴다

미리보기는 키를 읽지 않고 이미지 API도 호출하지 않는다.

```bash
.venv/bin/python -m beauty_ai.freeform_image \
  --product product.jpg \
  --reference reference.png \
  --prompt-file prompt.txt \
  --output outputs/freeform-preview
```

제품 사진은 1~5장, 참고 사진은 0~5장이다. `--product`·`--reference`를 반복해서 추가한다. 저장된 `request.json`의 project/job ID를 유지한다. 실제 실행은 검토한 미리보기, 유효한 계정 설정, 실험 예산이 있을 때만 명시한다.

```bash
.venv/bin/python -m beauty_ai.freeform_image \
  --prepared outputs/freeform-preview/request.json \
  --project-id PROJECT_UUID --job-id JOB_UUID \
  --env-file /path/to/private.env \
  --attempt-db outputs/freeform-preview/runtime/provider_attempts.sqlite3 \
  --account-budget-config /path/to/account-budget.json \
  --budget-usd 1 --reservation-usd 1 --execute
```

위 금액 1달러는 실행 예시의 예약 허용액이다. 실제 이미지 가격이 아니다. 결과를 다시 읽으려면 같은 준비 파일·project/job·원장을 사용한다. 성공 결과 재사용은 새 예약이나 API 호출을 만들지 않는다. 재사용 사건만 따로 기록한다.

요청 ID, 정규화 토큰과 원형 usage, 모델/옵션, 실행시간, 실패 상태, 계정 예약 ID를 남긴다. 이미지 디코딩·저장 실패에도 확인된 usage를 보존한다. 원형 usage에는 임의 문자열·키·응답 전체를 저장하지 않는다.

## 6. W&B에서 비교한다

기존 이미지 실험 내보내기는 그대로 유지한다. 새 비용 보고서는 별도의 비교 run으로 올린다. 보고서 생성만으로는 업로드되지 않는다.

```bash
.venv/bin/python -m beauty_ai.cost_reporting \
  --dataset examples/evaluation/finops-dataset.json \
  --comparison examples/evaluation/finops-comparison.json \
  --output outputs/finops-demo \
  --wandb --wandb-entity YOUR_ENTITY --wandb-project beautyai-image-experiments
```

프로젝트의 PRIVATE 상태를 확인한다. 같은 보고서는 같은 run ID를 쓴다. 원격 파일·표·요약이 일치하면 재업로드하지 않는다. 확인이 실패하면 `INCOMPLETE`로 남긴다. `UPLOADED`와 `READBACK_VERIFIED`는 다르다. 재동기화할 때 이미지 API를 다시 호출하지 않는다.

실제 청구 조회, W&B 원격 업로드, 운영 서버 적용과 실제 절감 효과 검증은 이번 로컬 구현 시험에서 하지 않았다.

## 구조와 검증

```text
beauty_ai/
  account_budget.py       계정 공통 예산·예약·일회 허가·정산
  provider_attempts.py    실험별 시도·관측·재사용 사건
  api_image_processor.py  기존 처리기에 공통 예산 연결
  openai_images.py        전송 직전 허가 확인·usage 보존
  freeform_image.py       자유형 미리보기·명시 실행
  cost_accounting.py     비용·채택 이력과 정확한 금액 배분
  cost_collection.py     저장 파일·중단 DB의 읽기 전용 수집
  cost_savings.py        같은 조건에서 장당 원가 비교
  cost_reporting.py      한국어 HTML·JSON 보고서
  cost_wandb.py          명시 업로드·원격 재확인
```

계산 코드는 모델을 호출하지 않는다. 수집·표시·W&B 동기화 실패가 이미지 재생성으로 이어지지 않는다. 아래 시험은 모의 전송·임시 SQLite·가짜 W&B를 사용한다.

```bash
.venv/bin/python -m pytest -q -W error tests/test_account_budget.py tests/test_account_budget_integration.py tests/test_provider_observations.py tests/test_freeform_image.py tests/test_cost_accounting.py tests/test_cost_collection.py tests/test_cost_savings.py tests/test_cost_reporting.py tests/test_cost_wandb.py
```

단계별 결과는 [릴레이 기록](../fivecircles/work/finops-relay-20261103.md), 남은 실제 검증은 [이미지 TODO](imageTodo.md#2026-11-03--finops-절감-측정-릴레이)를 본다.

## 생성당 추정 비교를 표시한다

생성 결과별 추정비용이 있고 비교할 두 실험이 정해졌으면, 채택 기록 없이도 **생성 1건당 추정비용 감소율**을 표시한다. [추가 계약](../fivecircles/architecture/contract/finops-generation-estimate.md)의 명시 비교쌍을 사용한다.

```bash
.venv/bin/python -m beauty_ai.cost_reporting \
  --dataset outputs/finops-generation-20261103/dataset.json \
  --generation-comparison outputs/finops-generation-20261103/generation-comparison.json \
  --output outputs/finops-generation-20261103/report
```

위 경로는 이번 로컬 검증의 저장 자료다. 다른 실험은 자신의 dataset과 manifest 경로를 지정한다. manifest에는 `schema_version=beautyai-generation-comparison-v1`, `comparison_id`, `estimate_rate_version`, `pairs`를 적는다. 각 pair는 `pair_id`, `baseline_cost_id`, `candidate_cost_id`를 가진다. 같은 case ID로 연결하며 파일 배열 순서를 사용하지 않는다.

한쪽 비용이 없거나 단가·성공 상태가 조건에 맞지 않으면 쌍 전체를 제외한다. 비용과 생성 수를 함께 제외한다. 비교에 배정하지 않은 기록도 제외 수에 표시한다. 같은 입력을 다시 동기화해도 이미지를 재생성하지 않는다.

기존 저장 자료의 첫 실험10건과 수정 실험10건은 `$0.412130 → $0.407115`다. 생성당 평균은 `$0.041213 → $0.0407115`, 추정 감소율은 약 `1.22%`다. 비교20건, 제외6건, 유효10쌍이다. 6건은 비용 미확인 자유형 기록이며 비교군에 배정하지 않았다. 이 수치는 동등 품질의 최종 광고 절감 효과를 입증한 값이 아니다.

W&B에는 별도 `generation_cost_estimate` 표와 `generation_` 접두사의 요약 값으로 표시한다. 비교/제외 건수·쌍 수·평균·감소율을 함께 저장한다. `--wandb`와 기존 목적지 옵션을 명시해야 실제 업로드한다.

## 생 프롬프트부터 완료까지 측정하는 방법

이 부분은 후속 구현 계약이다. 현재 생성20건 비교에는 과거 텍스트 생성·검수의 토큰이 없다.

1. 제작 시작 때 `work_id`를 만든다. 같은 광고를 고치는 동안 이 ID를 유지한다.
2. 기획·프롬프트 생성·이미지 생성·검수·수정의 각 API 호출에 `attempt_id`를 붙인다. 원래 요청을 다시 전송한 경우도 별도 시도로 기록한다.
3. 응답의 input/output usage와 모델·단가 버전·추정비용을 기록한다. 이미지 입력과 캐시 세부는 전체 input의 부분값이므로 다시 더하지 않는다.
4. 사용자 승인과 최종 파일 hash가 생기면 제작 한 건을 완료한다. 그때까지의 실패·수정·검수 비용도 모두 합산한다.
5. 같은 제품·요구·품질 기준에서 기본 흐름과 개선 흐름을 비교한다. 한 건당 누적 토큰은 모델·단계별로, 비용은 전체 합계로 본다.

사람이 쓴 생 프롬프트를 로컬에서 센 토큰은 예상 길이다. API에 전달되어 응답 usage에 잡힌 값이 청구 사용량이다. 여러 번 전달한 사진·대화 이력도 각 요청에서 보고된 사용량대로 합산한다. `total_tokens`와 input/output 합계를 다시 더하지 않는다.

완료쌍의 흐름 비교에서는 제외된 작업 수를 함께 표시한다. 취소·미완료 작업의 이미 쓴 돈도 원장에 보존하고 전체 생산비에서 지우지 않는다. 기존 채택 광고당 원가 계산에는 그 생산 실패 비용까지 귀속해야 한다.

이미지는 이미지 usage와 작업 연결을 제공하고, 텍스트는 기획·카피·검수 usage를 제공해야 한다. 서빙은 같은 work_id를 전달해야 한다. 이미지 TODO의 FIN-L1~3은 이 연동·집계·실험을 완료하기 위한 후속 항목이다. 팀원과의 합의나 실제 연결을 완료로 처리하지 않았다.

2026-11-03 후속 검증: 생성당 추정 비교 보고서를 [W&B run](https://forge.coreweave.com/wandb/uyt5041-alphaflower/beautyai-image-experiments/runs/compare-e63b12dafbb37a9bd386)에 저장하고 원본·두 표·summary를 재조회했다. 비교20건/제외6건, 감소율1.216849052%다. 실제 청구 절감이나 전체제작 비용의 검증은 아니다.

## 작업별 누적 비용 보고서 연결

2026-11-03 추가 구현은 제작 시작부터 완료까지 같은 `project_id/work_id`에 기획·이미지·검수·수정·합성 영수증을 연결한다. 위의 생성20건 비교와 과거 미관측 텍스트 사용량은 그대로이며, 새 기능을 적용했다는 이유로 과거 비용을 채우지 않는다.

사용 방법은 [광고 한 건의 누적 토큰과 비용](adWorkCosts.md)을 따른다. 저장 dataset에는 `--work-dataset`, 기존 DB의 읽기 전용 snapshot에는 `--work-ledger --work-project`와 선택적인 `--work-rates`를 사용한다. 기존 `--dataset` 없이 작업 자료만으로도 보고서를 만들 수 있다. 모델을 호출하지 않으며 W&B 게시에는 기존의 명시적인 `--wandb` 목적지 옵션이 필요하다.

HTML/JSON은 작업 상태·최종 hash·누적 토큰·비용 부분합·실패/수정 수와 단계/모델별 사용량을 보여준다. 같은 조건의 완료쌍 비교와 전체 생산비는 따로 표시한다. 취소·미완료·실패 비용을 전체 생산비에서 빼지 않는다. 가격이나 usage가 빠지면 전체 금액은 null이며 확인된 부분합을 함께 남긴다.

작업 연결이 없는 기존 보고서는 기존 ID·JSON·HTML 바이트를 유지한다. 작업 연결을 전달하면 정규화한 dataset을 보고서 identity에 포함하고 `work_dataset_sha256`과 `work_costs`를 함께 추가한다. 생성 비교와 동시에 사용할 수 있으나 서로 다른 입력의 금액을 중복 합산하지 않는다.

가상 전과정 시험·로컬 PASS·W&B readback은 실제 팀 배포나 외부 텍스트 서비스 적용, 품질 동등성 또는 청구 절감 효과를 입증하지 않는다. 새 작업 API와 추적 코드를 실제 서빙에서 사용했는지는 별도 인수 근거로 확인한다.
