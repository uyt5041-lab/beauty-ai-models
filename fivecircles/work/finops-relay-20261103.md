# FinOps 절감 측정 릴레이 — 2026-11-03

작업 ID FINOPS-20261103. 계약: [finops-savings](../architecture/contract/finops-savings.md). TODO 원본: [imageTodo](../../docs/imageTodo.md).

## 0. Scope Intake
- 실행 native, 필요 시 파일 소유권을 나누어 위임. 기존 실험/W&B 유지, 공통 계정 예산과 절감 측정·표시 구현.
- 기준 branch feature/image-model /93faaa8. 사용자 코드 변경 없음에서 시작.
- 실제 이미지 호출·W&B 업로드·청구조회·GCP 배포 안 함. 운영 한도/기초액은 설정 없이 추정하지 않음.
- 완료: B0~B4 계약·단계별 테스트·전체 회귀·오프라인 보고 화면. 실제 비용 절감은 별도 FIN-5.

## 1. Target / current

```mermaid
flowchart LR
 A["서비스·실험·자유형"] --> V["입력 검증·성공 재사용"]
 V --> B["공통 계정 예약"] --> L["실험별 시도 기록"] --> P["한 번만 전송 허가"] --> API["이미지 API"]
 API --> R["원형 usage·결과·오류"] --> C["기존·중단 자료 수집"] --> S["비용·채택·비교"] --> H["한국어 표시"] --> W["명시 W&B 비교"]
```

현재 시작 상태: 실험별 기록/래퍼 전송/기존 W&B는 존재. 공통 계정 예약·일회 허가·raw usage·중단 수집·새 비교/표시는 GAP.

| 후보 | upstream | connection | safety | validation | penalty | score | 선택 |
|---|---:|---:|---:|---:|---:|---:|---|
| FIN-0 계약 | 4 | 3 | 2 | 2 | -1 | 10 | 먼저 |
| FIN-1 계정 저장 | 4 | 3 | 2 | 2 | -2 | 9 | 계약 후 |
| FIN-2 관측·전송 | 3 | 3 | 2 | 2 | -2 | 8 | B1 PASS 후 |
| FIN-3 비용·수집 | 2 | 3 | 1 | 2 | -1 | 7 | B2 PASS 후 |
| FIN-4 표시 | 1 | 2 | 1 | 2 | -1 | 5 | B3 PASS 후 |

## 2. Doc / Contract
- policy fivecircles-egm-v3-risk-scoped; HIGH(예산 권한·식별·비용 데이터 계약), required_scope FULL.
- 이전 리뷰의 finding/현재 코드 근거만 재사용. 새 계약은 FRESH 5단계. 상태 DESIGN_REVIEWED. 5단계/9 fresh reviewer, 필요한 수정 후 해당 reviewer 재검토 완료.
- 5개 차원: 기존 코드 재사용, 현재 권한/비밀·노출 통제, 단위 중복 방지, 원장/수집/계산/표시 분리, 예전 데이터 유지.

## 3. 단계 상태

| batch | Doc | Implementation | Validation | Repair | local delivery | next |
|---|---|---|---|---|---|---|
| B0 | PASS | N/A | EGM 5단계 PASS | 필수 지적 해소 | LOCAL receipt | B1 |
| B1 | REUSED B0 | PASS | 101 PASS | 수집 오류 수리 | LOCAL files | B2 |
| B2 | REUSED B0 | PASS | 402 PASS | guard fixture·migration 수리 | LOCAL files | B3 |
| B3 | REUSED B0 | PASS | 260 PASS + 실제26건 수집 | 정밀도·history roundtrip 보강 | LOCAL dataset | B4 |
| B4 | REUSED B0 | PASS | 1837 전체 PASS + browser | 게시 실패 exit/단가표시 보강 | LOCAL reports | FIN-5 outside scope |

## 4. 검증/기록/게시 정책
- 각 batch 관련 tests PASS와 실패 수리 기록 후 다음 batch. 범위 밖 live 인수는 NOT_RUN.
- Python .venv/bin/python, SQLite 파일/다중 프로세스와 HTTP MockTransport 사용. 마지막 전체 pytest -q -W error.
- 원격 게시/배포는 현재 요청에 명시되지 않아 SKIPPED_NOT_REQUESTED. 로컬 산출물이 delivery 대상이며 CD는 NOT_APPLICABLE(배포 변경/실행 없음).
- closeout에 source diff/hash, 테스트 명령·결과, target/current PNG/HTML과 브라우저 확인을 연결한다.
- 자동 승인이나 기록만으로 운영 배포·절감효과·팀인수를 완료로 표시하지 않는다.

## 5. Relay
- 현재 STOP_WITH_REASON: 요청한 FIN-0~4 로컬 구현·검증 완료. 다음 FIN-5는 실제 청구/효과/운영 인수이며 이번 범위 밖.
- final flow verification: 로컬 범위 PASS. paid/image/W&B remote/GCP NOT_RUN.

### B0 수령 기록
- 기존 관련 회귀 179 passed (0.94s). 계약5단계 완료, 상세 receipt: outputs/finops-relay-20261103/review/receipt.json.
- 수리: 제품 비중 비교·기간이월·claim/settle 경쟁·raw usage 오류 관측·재사용 사건·단가 변환·파일 경계.
- 로컬 계약 산출물 확인. 원격 게시 SKIPPED_NOT_REQUESTED, CD NOT_APPLICABLE. 다음 FIN-1.

### B1 수령 기록
- account_budget.py와 test_account_budget.py. 독립 정적 검토 추가 budget bypass 없음. root pytest account+provider 101 passed/7.50s, b1-tests.txt.
- 수리: pytest 예약 매개변수 request→request_hash; 근거 hash 충돌·loader 범위·Decimal context 보호를 추가 검증.
- LOCAL 수령 완료. 원격 SKIPPED_NOT_REQUESTED/CD N/A. FIN-2 CONTINUE_WITH_NEXT_FORM: 전송 경계/관측과 runtime/freeform 연결, B0 계약 재사용.

### B2 수령 기록
- runtime/experiment/freeform →공통 계정/실험 원장→permit→전송, raw usage/오류/재사용 관측 연결.
- root 관련15파일 402 passed/11.83s. b2-tests.txt. mock/preview만 사용, 실제 유료 HTTP0.
- 수리: 새 guard에 맞춘 기존 fixture6+2, 최초 WAL 전환 경쟁, B1 테이블 기대값에 reuse_events 반영.
- 기존 test_openai_images/test_cosmetics_comparison fixture 적응과 새 test_account_budget_entrypoints는 동일계약 테스트 보강이며 기능 범위를 늘리지 않음.
- LOCAL 수령; 원격 SKIPPED_NOT_REQUESTED/CD N/A. FIN-3 CONTINUE_WITH_NEXT_FORM: 순수 비용/채택/절감 계산과 읽기전용 수집.

### B3 수령 기록
- 엄격 비용/배분/정정과 채택/취소, 같은 제품비중 비교, source수집/중단snapshot.
- root 관련260 tests PASS/0.62s. synthetic40% 및 환산1.0/실제지출차이0.5 구별. b3-tests.txt.
- 실제3그룹26개 source를 CLI로수집, 추정20개부분합0.819245000 USD, actual/acceptance미확인 유지. b3-source-check.json.
- 정정 전체이력을 보존하는 normalize_dataset+latest_records로 roundtrip 보강, 추정 변환 Decimal context 고정.
- LOCAL 수령; 원격 SKIPPED_NOT_REQUESTED/CD N/A. FIN-4 CONTINUE_WITH_NEXT_FORM: 한국어보고/W&B 비교+최종화면검증.

### B3 후속 구현 검토
독립 audit에서 빈 제품 묶음의 비용 누락과 project_id 중복 지문 누락을 발견·수리했다. 계약의 완전성/출처 조건을 명확히 하고 회귀2개 추가, cost3파일196 PASS. B4는 수리된 비교 결과를 사용한다.

### B4 최종 수령 기록
- cost_reporting/cost_wandb, synthetic2파일, docs/finopsSavings.md 및 W&B 안내 연결.
- 전체1837 passed/28.56s(-W error), FiveCircles검사/diffcheck PASS. full-tests.txt.
- 실제26건 보고서·계산예시40% 브라우저 확인, 390px 폭 넘침없음/금액·비율·가상자료표시 확인. flow/browser-check.json 및comparison-mobile.png.
- 원격 업로드 실패는 CLI nonzero; fakeW&B 동일보고2동기화init1, readback불일치/권한실패미완료. 실제remote NOT_RUN.
- 수정된 계약의 완전성/프로젝트지문 문구는 독립 구현 검토의 두 수리를 명확화한 것이며 해당 reviewer 재검토 PASS.
- 로컬 산출물 수령. 커밋/푸시/PR/GCP 없음. FINAL STOP_WITH_REASON: 사용자 승인 범위 완료; 실제 효과 입증은 별도.
