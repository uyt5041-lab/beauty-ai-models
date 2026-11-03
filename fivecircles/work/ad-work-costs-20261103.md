# 광고 제작 누적 비용 릴레이 · 2026-11-03

작업 AD-WORK-COSTS-20261103. 계약: ../architecture/contract/ad-work-costs.md.
사용자 목표: work_id로 기획/생성/검수/수정/완료 토큰·비용을 연결하고 화면/W&B, feature/image-model로 PR merge.
작업 branch codex/ad-work-costs-20261103 → feature/image-model (사용자확정). 기본문서 branch plan/models는 변경하지 않는다.

## 기반 수령
- 공유 checkout의 R2 WIP를 보존하고 기존FinOps43파일을 영수증hash로 격리했다. freeform의검증된R2이전사본을사용.
- 기존기능2030 tests PASS/28.62s. prerequisites commit 893e730. 아직push/merge없음.
- Graph코드탐색을재사용하고 실제변경구간은원문확인. 새work계약은HIGH/FULL EGM이며계정예산원장통합은하지않음.

| 단계 | 목적 | 상태 |
|---|---|---|
| W0 | 계약·검토·기반 | PASS |
| W1 | work원장·검증·순수누적 | PASS |
| W2 | 텍스트/이미지/API/재시작 연결 | PASS |
| W3 | 보고서·W&B | PASS |
| W4 | 전체흐름·실제W&B·PR·CI·merge | VALIDATED_AWAITING_MERGE |

공통권한은기존bearer→project이며새stage/complete/receipt는프로젝트경계를검증한다. APItoken/사진/원시프롬프트를새영수증에저장하지않는다.
실제모델유료호출은하지않고mockE2E를사용한다. W&B검증자료는가상자료임을표시한다. 배포/GCP인수는이작업merge와구분한다.

## W0 완료 · EGM
HIGH/FULL, 5단계 독립 검토와 중요한 지적의 같은 검토자 재확인 PASS. 상세 원본은 로컬 outputs/work-costs-20261103/review, native agent 응답이다.
1 Zoom PASS → 2 Comprehension PASS(usage/통화 규칙 보완) → 3 Structure/Critic/Style PASS(scope·cancel 늦은 영수증·rate 비교 보완) → 4 Zoom PASS → 5 readiness/details/style PASS(raw SDK 관측·복구 inbox·provenance·W&B 숫자 제한 보완).
최종 검토 snapshot SHA256 afd1208fc31fdebb6e495d055e5ff91b9d6394cac67ae831380d642ff85e47ba. 초기 검토는 당시 hash로 보존하고 후속 수정 구간만 재검토했다. 검토자의 파일 읽음 선언과 native 응답 근거이며 독립 tool access transcript는 별도 내보내지 않았다.
설계 통과는 구현·효과 검증이 아니다. 지금 보안은 project 인증/경계·비밀 미수집·bounded 입력·중복 호출 방지, 외부 공개 사용자별 권한 인수는 서버 통합 때 검증한다. 단일 호스트의 작은 별도 SQLite/순수 집계로 범위를 제한한다. 배포: NOT_APPLICABLE(설계 문서).

## 다음 릴레이 W1
목표흐름: 작업등록 → 기획 → 생성 → 검수 → 선택수정 → 합성 → 명시완료 → 누적보고/W&B.
현재: 각 호출 기록/생성당 비교는 있고 work 연결은 없음. 우선 원장·검증·집계3개 모듈을 분리해 추가한다. score: 원장경계3/연결3/표시2 중 원장이 상위 차단점이다.
완료판정은 W1의 상태/프로젝트/멱등/동시성/누락/비교 테스트 PASS다. CONTINUE_WITH_NEXT_FORM=W1. 실제 모델 호출0, W&B 검증자료는 synthetic, GCP 배포는 범위 밖.

## W1 완료
계약 검증62·원장20·집계25, 합계107 tests PASS(-W error). SQLite 재시작·경쟁·late cancel·project격리·provider중복·완료 stage/hash·사용량/비용 누락·비교 제외를 확인했다. 가격은 snapshot/명시근거만 사용한다. SDK 로컬 검사에서 LegacyAPIResponse accessor를 raw.http_response.json()으로 바로잡았다(관측/상태 정책 불변, source 검증에 따른 targeted 수정).
다음 W2: 원장 노드에 텍스트/이미지/내부 HTTP 경로를 연결. CONTINUE_WITH_NEXT_FORM=W2. 배포 NOT_APPLICABLE: 아직 SDK/HTTP를 변경하지 않은 라이브러리 배치.

## W2 완료
텍스트 SDK raw 관측·재시도0·공유검증, 이미지 finally 수집·완료 후 성공재사용, Backend/freeform 선택 work, 내부 HTTP와 runtime의 영속 원장을 연결했다. 외부 팀 텍스트는 생성 코드를 바꾸지 않고 시도 등록/영수증 API를 사용할 수 있다.
모의 전과정: 기획→생성→사용량이 관측된 검수 실패→검수 성공→수정→재생성→실제 PNG 합성→재시작/완료/읽기.7attempts(6API+1local), input70/output78, 추정$0.001852000(가상 단가/자료), 실제 청구null, 재사용 추가POST0. 실제 광고 품질·절감 효과의 증명은 아니다.
W2 code review의 local 잘못된 입력 PENDING 잔존·동시 inbox소비 오탐을 고쳤고 독립 재확인 PASS. strict gateway terminal 재호출 guard는 구조/비평/준비도 범위를 재사용 검토한 뒤 추가했다. 외부API 기본동작은 유지한다.
관련 회귀 포함 315 passed in 4.72s. 로컬 test/모의 HTTP까지만 검증했고 서비스 배포 NOT_RUN; 배포가 없는 라이브러리/API 패키지 반영이므로 CD는 NOT_APPLICABLE이다.
CONTINUE_WITH_NEXT_FORM=W3: 저장된 이력→보고서→W&B 연결. 모델 호출은 추가하지 않는다.

## W3 완료
보고서/CLI와 W&B work_costs·work_stage_usage 및 summary 연결. 기존 미지정 report ID/JSON/HTML 바이트를 유지하고 새 입력 hash를 추가했다. 모델별 토큰/실패·수정/누락/부분합/완료쌍·제외를 표시한다. pair 판정은 공통 compare_work_rows를 재사용한다.
독립 code review의 명시 receipt 금액 변조·조건 불일치 비교 위조 입력을 거절하도록 고쳤고 정상 입력과 함께 재확인했다. report에는 가격 snapshot 원문이 없어 외부 요율 진위까지 인증한다고 쓰지 않는다.
브라우저 실제1482px/390px 확인. 신규 비교표가 모바일846px로 넘치는 FAIL을 발견해 새 work section에만 scoped style을 적용했다. 재검증 documentWidth390, viewport390, 완료쌍3열 전체 표시/넓은 상세표 내부 scroll 확인. screenshot은 로컬 outputs/work-costs-20261103/W3-*.
340 passed in 1.61s. 원격 W&B 게시/정확한 readback은 다음W4. 배포 NOT_RUN, CD NOT_APPLICABLE(검증용 정적 HTML/라이브러리). CONTINUE_WITH_NEXT_FORM=W4: 독립 모의 예제→전체검증→실제W&B→PRmerge.

## W4 검증 완료 · Git 반영 대기
전체2335 tests PASS(-q -W error,36.58s), 설정/링크 검사 PASS, diff 검사 PASS. 실제 OpenAI SDK3.36.0을 MockTransport로 시험했고 구형 선택 생성인자도 모의 회귀로 보호했다. 실제2.x 설치 시험은 하지 않았다.
최종 검토에서 새 입장이 project export 한도를 넘길 수 있는 문제를 고쳤다. work 생성과 attempt/미관측binding의 합산 기록 용량을 transaction에서 검사한다. binding은 모델 호출 전 기록 슬롯을 선점하고, 늦은 영수증 전환은 증가0이어서 옛 한도 초과DB에서도 보존한다. 금전 예약과는 별개다. 독립 구조/준비도 보완 검토 및7개 용량 회귀 PASS.
텍스트 request ID는 HTTP x-request-id다. body 응답 ID를 요청 ID로 바꾸지 않는 계약과 headerless 시험을 추가했다.

가상 예제:4work/12attempts(모델API10+local2), text mock7/image POST mock3/reuse2/paid0. 완료2/취소1/진행1. 알려진 input102/output109, 전체 token/추정비용은 미관측binding1 때문에null, 알려진 추정부분합$0.002584000. 같은 조건의 완료쌍1/제외2작업. 가상단가·검증자료이며 실제 광고 품질/청구/절감 효과가 아니다.
PRIVATE W&B compare-e236da2f35e68cac2d06에서 report.json·work_costs·work_stage_usage·summary를2회 READBACK_VERIFIED, 두 번째deduplicated=true. 원본reportfile SHA256 8db78324e029a8bb71800061b89b040555941c870f874009b152b8208a7ba68f. 첫 게시 뒤 예제를 재생성하지 않았다.
[W&B 검증 run](https://forge.coreweave.com/wandb/uyt5041-alphaflower/beautyai-image-experiments/runs/compare-e236da2f35e68cac2d06).

[목표/현재 흐름](ad-work-flow-20261103.md)과 PNG/HTML을 갱신하고 실제 브라우저에서 두 이미지/범례/차이/우선순위 표를 확인했다. 비용 화면은1482px/390px 검증했고390px 넘침을 수정했다.
Graft wiring139files/2574nodes를 저장했다. deep layer는 이번에 LLM 호출로 재생성하지 않았으며 기존 deep summaries stale 경고와 구조 graph를 구분한다. 필요한 원문은 별도 확인했다.
현 단계 미완료는 PR→feature/image-model merge와 그 영수증뿐이다. 팀 서비스 배포·외부 텍스트 서비스 적용·실제 청구/품질 인수는 범위 밖(NOT_RUN). 배포 CD NOT_APPLICABLE: 서비스 재시작/배포 요청 없는 라이브러리·검증보고서 전달.
