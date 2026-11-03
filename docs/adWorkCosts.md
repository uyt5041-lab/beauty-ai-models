# 광고 한 건의 누적 토큰과 비용

한 광고를 기획하고 이미지를 만든 뒤 검수·수정·합성을 반복하는 동안 같은 `work_id`를 유지한다. 각 모델 호출은 별도의 `attempt_id`로 남긴다. 실패한 호출의 확인된 사용량도 누적한다.

이 기능은 모델 API 비용을 집계한다. 로컬 합성의 CPU·저장공간 가격을 계산하거나, 공급자 청구 내역과 사람 검수의 진위를 인증하지 않는다. 합성 fixture로 전체 흐름을 시험할 수 있으며 실제 유료 호출이나 운영 배포를 뜻하지 않는다.

구현의 모듈·저장 구조는 [구조 설명](adWorkCosts-architecture.md), 정확한 입력 필드는 [계약](../fivecircles/architecture/contract/ad-work-costs.md)을 따른다.

## 작업을 만들고 연결하기

작업 원장은 기존 이미지 시도 원장·계정 예산 DB와 다른 파일을 사용한다. 서버 실행 시 `ad_works.sqlite3`를 만들고 내부 작업 API와 이미지 처리기에 같은 원장 객체를 전달한다. 인증 토큰에서 확인한 프로젝트만 읽고 쓴다.

```python
from uuid import UUID, uuid4
from beauty_ai.work_ledger import AdWorkLedger

project_id = UUID("00000000-0000-4000-8000-000000000001")
work_id = uuid4()
ledger = AdWorkLedger("outputs/my-campaign/ad_works.sqlite3")
ledger.create_work(project_id, work_id, product_ref="product-cream-01")
```

`project_id`는 서버가 이미 확인한 프로젝트 값이어야 한다. 제품·검수자·정책은 공개 가능한 별칭을 쓴다. 프롬프트·사진·API 키·원시 오류를 작업 영수증에 넣지 않는다.

기본 필수 단계는 기획·이미지 생성·검수·합성이다. 수정은 필요할 때만 기록한다. 비교 실험을 할 때는 작업 생성 시 같은 `task_pair_id`에 `arm="baseline"`과 `arm="candidate"`를 각각 배정한다.

### Python 호출 범위

```python
from beauty_ai.work_tracking import track_ad_work

async def draft_for_work(gateway, facts, options):
    with track_ad_work(ledger, project_id, work_id):
        return await gateway.draft_campaign(facts, options)
```

기존 `OpenAITextGateway`의 기획·검수·수정 호출을 범위 안에서 관측한다. 텍스트 생성과 검수 정책의 소유권은 기존 텍스트 담당에 있다. 새로운 텍스트 생성기를 만드는 기능이 아니다.

추적 중인 텍스트는 실제 SDK에서 재시도를 0으로 설정한다. 구조화 출력 검증 전에 원시 HTTP 응답에서 허용된 사용량과 요청 ID만 추린다. 거절·불완전 응답·구조 검증 실패도 관측한 사용량을 남긴다. 응답이 없던 연결 실패·시간 초과·취소는 `UNKNOWN`이며 사용량을 추정하지 않는다.

같은 작업의 같은 operation·request hash는 다시 호출하지 않는다. 성공·실패가 이미 기록됐으면 `WORK_ALREADY_RECORDED`, 미확정 호출이면 충돌 오류다. 받은 결과를 사용하고, 의도적인 새 수정은 변경된 입력이나 명시적인 프롬프트 버전으로 구분한다. 추적 범위가 없으면 기존 호출 동작을 유지한다.

영수증 저장 실패는 `WORK_CAPTURE_PENDING`이다. 안전한 영수증을 원장 옆 `.cache/work-receipts`에 먼저 보관하고 다음 범위 진입 때 재수집한다. 재수집은 모델을 호출하지 않는다. 두 저장 모두 실패했다면 오류에 포함된 안전한 `.receipt`로 인증된 영수증 API에 재입력할 수 있으나, 저장 실패를 성공 기록으로 처리하지 않는다. 취소는 영수증 처리를 시도한 뒤 원래 취소를 전파한다.

### 이미지 작업 연결

내부 이미지 요청에는 작업 ID를 헤더로 전달한다. 본문과 과거 request hash에는 작업 ID를 추가하지 않는다.

```bash
curl -X PUT "$INFERENCE_URL/internal/v1/jobs/$JOB_ID" \
  -H "Authorization: Bearer $INTERNAL_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Ad-Work-Id: $WORK_ID" \
  --data-binary @image-request.json
```

인증·요청 검증 뒤 queue에 넣기 전에 작업을 바인딩한다. 다른 프로젝트의 작업은 연결하지 못한다. 원장이 없는 서버는 해당 헤더 요청을 503으로 거절한다. 범위·명시 작업·기존 바인딩이 서로 다르면 asset·예산·모델 호출 전에 `WORK_SCOPE_CONFLICT`다.

Python 어댑터는 `BackendImageAdapter.generate(..., project_id=project_id, work_id=work_id)`를 지원한다. 자유형 CLI에서 실행을 추적할 때는 `--work-id WORK_UUID --work-ledger PATH`를 함께 지정한다. 준비 파일·예산·계정 허가와 명시적 `--execute` 요건은 기존 [자유형 실행 방법](finopsSavings.md#5-자유형-요청도-같은-경계를-쓴다)을 따른다.

이미지의 성공 결과를 같은 job으로 다시 읽으면 원래 provider attempt를 재사용한다. 새 비용 attempt나 POST를 만들지 않는다. 작업 연결과 영수증은 재시작 후에도 유지한다. 영수증 동기화 실패를 이유로 이미지를 재생성하지 않는다.

## 외부 텍스트 팀의 영수증 받기

외부 텍스트 서비스는 같은 프로젝트에 작업을 만든 후 아래 두 요청을 보낸다. 서버는 bearer credential에서 프로젝트를 확인하므로 요청 JSON에 `project_id`를 넣지 않는다.

| 순서 | 요청 | 내용 |
| --- | --- | --- |
| 1 | `POST /internal/v1/ad-works/{work_id}/attempts` | attempt ID, 단계, operation, provider, configured model, prompt version, request hash, nullable parent |
| 2 | `POST /internal/v1/ad-works/{work_id}/receipts` | 같은 attempt의 terminal 상태·관측 usage·모델·요청 ID·안전한 출처와 hash |

먼저 등록하지 않은 attempt의 영수증은 거절한다. nullable 필드도 계약의 key를 유지한다. 같은 영수증의 replay는 멱등이며, 같은 attempt의 다른 내용은 충돌이다. 공급자 요청 ID를 다른 work/attempt에 재사용할 수 없다.

usage는 strict 정수이고 누락은 null이다. `input_tokens`, `output_tokens`, `total_tokens`, `input_text_tokens`, `input_image_tokens`, `cached_input_tokens`, `reasoning_tokens`만 허용한다. `total_tokens`와 input/output을 다시 합산하지 않는다. 캐시·추론 토큰은 전체의 부분값이다.

예상 비용을 명시한다면 `estimated_cost_usd`와 `rate_version`이 필요하다. 실제 비용을 명시한다면 `actual_cost_usd`와 `billing_evidence_ref`가 필요하다. 정규화된 토큰이 있다는 사실만으로 실제 청구 금액을 채우지 않는다.

## 로컬 합성과 명시적 완료

최종 파일을 실제로 저장한 뒤 hash를 계산하고 로컬 영수증을 남긴다.

```python
from hashlib import sha256
from pathlib import Path
from beauty_ai.work_tracking import record_local_stage

artifact_sha256 = sha256(Path("outputs/my-campaign/final.png").read_bytes()).hexdigest()
record_local_stage(ledger, project_id, work_id, artifact_sha256=artifact_sha256)
```

이 기록은 `provider=model=local`, 입력/출력 0, API 추정비용 0과 `local-api-zero-v1`을 명시한다. CPU나 저장공간의 실제 원가가 0이라는 뜻은 아니다.

완료에는 최종 파일 hash·검수자 별칭·품질 정책·엄격한 true·원장 전체의 attempt ID 목록이 필요하다.

```python
data = ledger.export_dataset(project_id)
expected = [item["attempt_id"] for item in data["attempts"] if item["work_id"] == str(work_id)]
ledger.complete(project_id, work_id, {
    "artifact_sha256": artifact_sha256,
    "reviewer_ref": "reviewer-pi",
    "quality_policy_version": "campaign-quality-v1",
    "quality_pass": True,
    "expected_attempt_ids": expected,
})
```

모든 attempt와 바인딩한 이미지 job에 terminal 영수증이 있어야 한다. 필수 단계의 성공 기록과 같은 작업의 성공 이미지/합성 영수증에 최종 hash가 있어야 한다. 단가·usage 미확인 자체는 완료를 막지 않으며 비용의 미확인 상태를 그대로 표시한다.

`COMPLETED`는 같은 completion/receipt replay만 허용한다. `CANCELLED`는 새 호출을 막되 기존 호출의 늦은 영수증을 받는다. 취소 전에 연결한 이미지 job의 기존 attempt를 뒤늦게 수집할 수도 있다. 두 상태를 자동으로 재개하지 않는다.

## 보고서 만들기

저장한 work dataset만으로 보고서를 만들 수 있다. 이 명령은 모델·청구 API를 호출하지 않는다.

```bash
.venv/bin/python -m beauty_ai.cost_reporting \
  --work-dataset outputs/my-campaign/work-dataset.json \
  --output outputs/my-campaign/report
```

실행 중이거나 저장된 작업 DB를 읽기 전용 snapshot으로 볼 수도 있다.

```bash
.venv/bin/python -m beauty_ai.cost_reporting \
  --work-ledger outputs/my-campaign/ad_works.sqlite3 \
  --work-project PROJECT_UUID \
  --work-rates /path/to/work-rates.json \
  --output outputs/my-campaign/report
```

`--work-dataset`과 `--work-ledger`는 함께 쓰지 않는다. `--work-project`와 선택적인 `--work-rates`는 ledger 입력에서만 쓴다. Python `build_report`/`write_report`는 기존 dataset 인자를 유지하고 keyword `work_dataset`을 추가로 받는다. 기존 FinOps dataset이나 생성 비교를 함께 주려면 `--dataset`, `--comparison`, `--generation-comparison`을 기존 방식으로 지정한다. 서로 다른 원장의 비용을 자동으로 합산하지 않는다.

단가 파일은 `beautyai-work-rates-v1`이며 USD/백만 토큰의 정확한 문자열을 사용한다. 아래는 계산 방법을 보여주는 **가상 단가**다. 공급자의 현재 가격이 아니다.

```json
{
  "schema_version": "beautyai-work-rates-v1",
  "rate_version": "synthetic-rates-v1",
  "models": {
    "synthetic-text-model": {
      "mode": "combined",
      "input_usd": "2.000000000",
      "input_text_usd": null,
      "input_image_usd": null,
      "output_usd": "4.000000000"
    }
  }
}
```

`split`은 input text/image 세부와 각각의 단가가 모두 있고 합이 input과 같을 때만 계산한다. 미지원 모델·누락 토큰·누락 단가는 비용 null이다. 캐시 할인은 계산하지 않는다. 명시 영수증의 비용·단가 버전이 있으면 snapshot 계산보다 우선한다.

화면은 전체 생산비와 확인된 부분합, 진행·취소·완료, 최종 hash, 토큰 관측률, 실패·수정 수, 단계/모델별 내역을 보여준다. 관측률은 input/output이 모두 확인된 API 시도 수를 대상으로 하며 로컬 단계는 분모에서 뺀다. 분모가 없으면 null이다.

완료쌍 비교는 제품·품질 정책·필수 단계·적용 단가 정책이 같은 양쪽 완료 작업만 포함한다. 빠진 상대·미완료·비용 누락·중복 최종 파일 등은 쌍 전체를 제외하고 사유를 보여준다. 제외된 실패·취소·미완료 비용도 전체 생산비에는 남는다. 다른 모델의 토큰 감소율을 같은 단위의 효율 개선으로 표시하지 않는다.

작업당 최대 1,000 attempts, 프로젝트 snapshot 최대 1,000 works/20,000 receipts다. 외부 JSON 입력은 1MiB 이하이며 중복 key·비밀값·NaN·잘못된 타입을 거절한다. 내부 읽기 전용 DB snapshot은 행 수 경계를 적용하며 일부만 잘라 성공시키지 않는다.

## W&B 게시와 검증

보고서는 로컬 JSON/HTML을 먼저 쓴다. 실제 게시에는 아래 옵션을 명시한다.

```bash
.venv/bin/python -m beauty_ai.cost_reporting \
  --work-dataset outputs/my-campaign/work-dataset.json \
  --output outputs/my-campaign/report \
  --wandb --wandb-entity YOUR_ENTITY --wandb-project YOUR_PRIVATE_PROJECT
```

PRIVATE 프로젝트와 같은 보고서 ID의 run을 확인한다. `work_costs` 표는 작업당 한 행, `work_stage_usage` 표는 작업/단계/공급자/모델별 한 행이며 `work_` summary를 추가한다. 원본 `report.json`, 기존 표와 새 표, summary를 각각 재조회해 맞아야 `READBACK_VERIFIED`다. 업로드 수신만으로 완료하지 않는다. 재동기화는 모델을 호출하거나 같은 run을 다시 만들지 않는다.

JSON의 금액은 소수 9자리 문자열이며 W&B 표시에서만 숫자로 변환한다. 새 표·summary의 개별 토큰/count나 합계가 `2^53`을 넘으면 network/init 전에 `WORK_WANDB_COUNT_RANGE`로 거절한다. 로컬 JSON/HTML의 정확한 큰 정수는 유지한다. 미확인 값은 0으로 게시하지 않는다.

가상 전과정 검증 자료는 `data_kind=SYNTHETIC_FIXTURE`로 구분한다. 실제 선언 자료와 혼합하면 `DECLARED_DATA`다. 가상 게시·SDK 시험·PR merge는 실제 팀 서빙 배포, 외부 텍스트 서비스 적용, 광고 품질이나 비용 절감 효과 검증과 별도다.

## 실행 가능한 모의 예제

저장소 루트에서 실행한다. 출력 폴더는 새 경로를 쓴다.

```bash
python -m examples.run_ad_work --output outputs/ad-work-demo
```

모든 모델 요청은 MockTransport로 처리한다. 애플리케이션은 실제 키나 환경 파일을 읽지 않고 가상 인증값·고정 endpoint를 사용한다. SDK가 내부적으로 환경의 custom headers를 조회할 수 있어도 실제 외부 전송 경로는 없다. 구형 SDK 선택 인자는 지원 여부를 확인한다.

예제는 기준 작업·개선 작업·취소 작업·진행 작업을 만든다. 기획→생성→검수 실패→수정→재생성→PNG 합성→명시적 완료와 재시작·재사용을 확인한다. `report/index.html`에서 누적 비용을 보고 `execution.json`, `work-dataset.json`으로 근거를 확인한다. 예제 단가는 계산 검증용이며 실제 OpenAI 가격이 아니다.

2026-11-03 검증 자료는 4작업·12시도다. 텍스트 SDK mock7회, 이미지 POST mock3회, 재사용2회, 유료 모델 호출0회다. 실제 W&B [검증 run](https://forge.coreweave.com/wandb/uyt5041-alphaflower/beautyai-image-experiments/runs/compare-e236da2f35e68cac2d06)에서 JSON·두 work 표·summary를 재조회했다. 같은 자료를 다시 동기화하면 업로드를 반복하지 않는다. 가상 자료의 감소율은 실제 광고 품질이나 현금 절감 효과가 아니다.
