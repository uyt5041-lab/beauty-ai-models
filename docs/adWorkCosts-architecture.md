# 광고 작업 비용 구조와 데이터 흐름

2026-11-03. W1 원장·집계와 W2 SDK·이미지·내부 API 연결을 기준으로 한다. W3 보고 연결은 아래 계약을 따른다. 사용법은 [adWorkCosts.md](adWorkCosts.md), 정확한 입력·출력은 [work 비용 계약](../fivecircles/architecture/contract/ad-work-costs.md)이 원본이다.

## 모듈 의존성과 저장 책임

```text
inference_runtime
├─ AdWorkLedger(root/ad_works.sqlite3)
├─ ExternalImageProcessor ─ work_tracking ─ work_ledger
│  └─ ProviderAttemptStore(root/provider_attempts.sqlite3)
└─ inference_api ─ work_api ─ work_ledger

track_ad_work(ContextVar scope)
├─ OpenAITextGateway ─ TextWorkCapture ─ work_ledger
└─ BackendImageAdapter / freeform_image ─ ExternalImageProcessor

work_ledger ─ work_contracts
cost_reporting ─ work_metrics ─ work_contracts
cost_wandb ─ report.json 검증·게시·readback
```

`work_ledger`는 project/work, expected attempt, image-job binding, 최초 terminal receipt와 명시적 완료를 소유한다. SQLite의 짧은 `BEGIN IMMEDIATE` 안에서만 상태를 바꾸며 provider 호출 중에는 write lock을 잡지 않는다. `ProviderAttemptStore`는 기존 이미지 제출·예약·재사용 근거, 계정 DB는 기존 예산을 계속 소유한다. work 원장은 두 DB와 별도 경로를 사용한다.

`work_contracts`는 엄격한 schema·안전 식별자·관계·개수 제한을 검증한다. `work_metrics`는 정규화된 snapshot을 받아 순수 계산만 한다. 파일 SQLite를 읽는 `read_work_dataset`은 schema 생성이나 migration을 수행하지 않는다. 보고서와 W&B는 이 snapshot의 파생 결과이며 work의 상태·영수증·완료를 수정하는 경로가 없다.

## 호출과 수집 경로

1. `track_ad_work`가 project/work/원장 경로를 scope에 고정한다. 같은 작업의 수정은 새 attempt로 누적하며, 성공 재사용은 기존 attempt의 같은 receipt를 다시 관측한다.
2. 추적 텍스트는 `with_options(max_retries=0)`의 `responses.with_raw_response.parse`를 호출한다. raw usage/model/request ID를 먼저 읽고 `raw.parse()`와 schema 검증을 수행한다. 응답 후 파싱 실패에도 관측된 usage를 남긴다. 이 기능이 없는 client는 SDK 호출 전에 거절한다. 비추적 텍스트 경로는 기존 동작을 유지한다.
3. 텍스트 receipt는 원장 인접 `.cache/work-receipts`에 정제된 복구 JSON을 먼저 저장하고 원장에 기록한다. 다음 scope 진입·호출 전 재수집은 모델 호출을 하지 않는다. 두 저장 경로 모두 실패하면 안전 receipt를 통한 수동 재입력 가능 여부를 확인해야 하며, 복구 성공을 가정하지 않는다.
4. 이미지는 명시 work ID·활성 scope·영속 job binding의 project/work/원장 경로가 일치해야 자산·예산·SDK 단계로 진행한다. `ExternalImageProcessor`의 `finally`에서 기존 provider record를 수집한다. 수집 실패는 `WORK_CAPTURE_PENDING`이고, 기존 provider 영수증을 보존하여 다음 수집에서 재사용한다. 닫힌 작업에서는 검증된 기존 성공 결과만 재사용한다.
5. 로컬 합성은 `record_local_stage`의 명시적 receipt로 API token/cost 0을 기록한다. CPU·스토리지 가격은 측정하지 않으며 실제 청구액은 근거가 없으면 `null`이다.

`BackendImageAdapter.generate(..., work_id=...)`는 안정적인 내부 job ID를 work에 연결하고, Freeform 실행의 `--work-id/--work-ledger`는 함께 전달한다. work ID를 기존 이미지 요청 DTO나 준비 파일에 추가하지 않으므로 기존 request hash 경계를 유지한다. 외부 텍스트 담당자는 동일 attempt/receipt API로 근거를 전달하며 이 원장은 외부 텍스트 생성기를 구현하지 않는다.

## 내부 API와 소유권

모든 route는 기존 bearer 의존성이 결정한 project를 사용한다. body의 project ID로 소유권을 선택할 수 없다.

| 경로 | 책임 |
| --- | --- |
| `PUT /internal/v1/ad-works/{work_id}` | work 생성·같은 spec의 멱등 재입력 |
| `GET /internal/v1/ad-works[/{work_id}]` | 인증 project export 또는 work와 metrics |
| `POST /internal/v1/ad-works/{work_id}/attempts`, `/receipts` | 선등록 attempt와 불변 terminal receipt |
| `POST /internal/v1/ad-works/{work_id}/complete`, `/cancel` | 완료 근거 검증 또는 취소 |
| `PUT /internal/v1/jobs/{job_id}` + `X-Ad-Work-Id` | 인증·요청 검증 후 queue 이전에 image job 연결 |

`work_api`는 bounded body reader를 재사용한다. 완료에는 모든 expected job의 receipt, 모든 attempt의 terminal 상태, required stage의 성공, 원장 전체와 같은 expected attempt ID 목록, 같은 work의 성공 이미지/합성에 존재하는 최종 artifact hash가 필요하다. 모델 호출 성공은 광고 채택이 아니며, 품질 합격은 지정 검수자·정책의 선언으로 보존한다.

## 집계·비교·보고 경계

- input/output은 각각 누락이 없을 때만 전체합을 낸다. 부분 관측은 known 부분합에 보존하고 cache/reasoning/total을 주 토큰 합계에 다시 더하지 않는다. 미관측 image binding과 pending attempt는 중복 없이 누락으로 센다.
- token 관측률의 분모는 billable attempt와 아직 관측되지 않은 image binding이다. local은 제외하며 분모 0은 `null`이다. 실패·수정·취소·미완료 작업의 API 비용도 전체 생산 원가에 포함한다.
- 영수증의 추정액+rate version이 우선한다. 없으면 공급된 RateBook snapshot만 사용한다. 기본 가격을 추정하지 않고, 미지원 모델·필수 token·단가 누락은 `null`과 부분합·누락 수로 남긴다. USD는 Decimal 계산 후 소수 9자리 문자열로 보존한다.
- 비교는 같은 task pair의 양쪽 완료 work, 동일 제품·품질 정책·required stages, 완전한 비용과 같은 실제 적용 rate-version 집합을 요구한다. 같은 arm의 중복 최종 artifact와 불완전한 쌍은 통째로 제외하고 제외 수·이유를 남긴다. 다른 모델의 USD 비교는 같은 가격 정책에서 허용하며 모델 간 token 감소율은 제공하지 않는다.
- 전체 원가는 모든 작업 비용을 고유한 완료 artifact 수로 나눈다. 비용 누락이나 완료 artifact 0개이면 `null`이다. 완료쌍의 진단적 절감률과 구분한다.

W3의 `cost_reporting`은 선택적 work dataset과 정규화 dataset hash를 report identity에 함께 넣는다. `cost_wandb`는 작업당 `work_costs` 1행, 작업/단계/model당 `work_stage_usage` 1행과 `work_` summary를 게시한다. 보고 JSON의 USD 문자열은 유지하고 W&B 표시에서만 숫자로 바꾼다. W&B count/token의 절댓값이 2^53을 넘으면 network/init 전에 거절한다. 정확한 `report.json` artifact·각 표·summary의 readback이 보고 완료 조건이며 게시 ACK만으로 충족되지 않는다. 모의 자료는 `SYNTHETIC_FIXTURE`로 구분한다.

## 현재 보안과 다음 통합 조건

`NOW_REQUIRED`: 내부 work API의 기존 bearer→project 인증, project/work 경계, 제한된 JSON 입력, 안전한 오류 코드, 비밀·원시 prompt·사진·원시 오류 제외, 기존 예산 guard와 재호출 방지다. 텍스트 복구 파일은 mode 0600·atomic replace·fsync를 사용한다. 이미지의 provider receipt와 과거 request/report hash는 기존 저장 책임을 유지한다.

외부 베타 공개 전에는 서빙 담당자가 사용자별 권한·요율 관리 역할과 실제 인증 설정을 통합시험해야 한다. 이 단일 호스트 SQLite 구현·SDK/mock/API 검증은 기존 팀 서비스 배포, 외부 텍스트 서비스 적용, 실제 청구 대사, 광고 품질 효과를 입증하지 않는다. 팀 서비스의 전체 배포 상태는 `UNKNOWN`이다.
