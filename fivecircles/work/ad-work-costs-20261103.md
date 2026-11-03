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
| W1 | work원장·검증·순수누적 | IN_PROGRESS |
| W2 | 텍스트/이미지/API/재시작 연결 | PENDING |
| W3 | 보고서·W&B | PENDING |
| W4 | 전체흐름·실제W&B·PR·CI·merge | PENDING |

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
