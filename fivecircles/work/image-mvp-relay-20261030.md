# BeautyAI MVP 릴레이 · 2026-10-30

## 0. Scope Intake

- 방식: native, 독립 검토 위임. 구현/검증/통합 책임은 root.
- 코드: taemin050-sys/project-it-tem feat/image-generation, 시작 65c2e84.
- 공유 TODO: uyt5041-lab/beauty-ai-models plan/models, 시작 a5f5095.
- 사용자 승인: MVP 구현·TODO 정리·릴레이샷. 기존 범위의 일반 push. PR/force push/main-dev 병합/배포/외부 메시지/새 이미지 API 호출 없음.
- 기존 미커밋 imageTodo·imageIntegrationFollowupPlan 내용을 보존한다.

## 1. Target / Current

타겟: 업로드 → 정규화/저장 → 과금 전 검증 → 시도 원장/이미지 후보 → 제품 검토 → A 합성 → 승인된 결과 전달.
현재: MPO·제한적 Windows 디렉터리 sync 수정은 R1에서 전달했다. Windows 실환경은 NOT_RUN이다. 기존 원장/합성 모듈과 A safe_zone 연결은 다음 릴레이에서 확인한다. 실제 사용자 승인/다운로드 인수는 미완료다.

## 2. Selection

R1 점수 10 = upstream4 + connection3 + safety2 + validation2 - risk1.
실제 연동을 막은 두 우회를 먼저 제거한다. R2는 safe_zone/A, R3는 기존 원장·제품 검토·전달 검증이다.

## 3. Contract

MPO 입력만 대표 frame0을 선택한 뒤 기존 크기 제한·EXIF·ICC·알파·메타데이터 제거를 적용한다.
일반 애니메이션은 계속 거절한다. 저장 결과와 AssetRef는 PNG 계약을 유지한다.
Windows의 directory fsync 불지원만 플랫폼 분기한다. 파일 동기화·replace·DB 실패를 무시하지 않는다.
HEIC는 조건부 후속이다.

## 4. Implementation

R1: assets.py와 전용 회귀 테스트. 구현 COMPLETED, 검증 PASS_WITH_RISKS.
후속 TODO는 imageTodo 13절에 있으며 과거 완료 내역을 다시 미구현으로 표시하지 않는다.

## 5. Validation

기준선 301 PASS(3.64s), Python3.11.15/Pillow13.0.0.
R1: 합성 MPO/EXIF/ICC/제한/애니메이션 및 오류 회귀, 실제 제품 MPO2개 저장/재조회, 독립 Linux 실제 파일/SQLite smoke.
Windows 실환경 NOT_RUN. 실제 제공자 호출0. 실제 팀원 JSON/서빙 UI 인수는 별도다.

## 6. Repair

실패 시 같은 배치에서 원인·수정·재검증을 기록한다. 이전 실패를 숨기지 않는다.

## 7. Publication

검증→logall→지정 브랜치 일반 commit/push→원격 SHA/readback 순서다.
CI: 현재 팀 branch workflow 없음. CD: NOT_APPLICABLE(라이브러리 코드 전달이며 서버 배포 요청 없음).
운영 배포나 다른 담당 코드의 통합은 새 권한으로 간주하지 않는다.

## 8. Report

target/current Mermaid와 상태표를 검증 결과에 맞춰 갱신한다. 시각 보고서 검증은 제품 UI 인수 증거가 아니다.

## 9. Relay

R1 closeout 전 R2 코드를 변경하지 않는다. 완료 후 최고 우선의 실행 가능한 gap으로 계속한다.
실제 자료/실행환경/별도 API 지시가 필요한 항목은 BLOCKED/NOT_RUN 사유를 명시한다.

## 10. Ledger

- Doc: COMPLETED(initial)
- Implementation: R1 COMPLETED
- Validation: 318 PASS + actual MPO2 + Linux10 PASS; Windows actual NOT_RUN
- Repair: NOT_APPLICABLE(initial)
- Push: R1 code VERIFIED(b9d0015), plan publication in this commit
- Report: R1 Mermaid/PNG/HTML/browser PASS
- Relay: R1 CLOSED → R2 A template
- Flow diagram verification: UPDATED / remaining GAP explicitly shown


## R1 검증 영수증

[318회귀·실제MPO·Linux 근거](../test/image-mvp-relay-20261030-r1.md). logall 갱신 완료. Push/report closeout 대기.

## R1 Closeout → R2

- 코드 원격: b9d00152de352499ce7773ac398fe8efb5408e8e; 일반 fast-forward push 및 원격 SHA 확인.
- R1 보고서: [목표·현재 비교](../architecture/spec/image-mvp-relay-flow-20261030.md).
- ego-browser TaskSpace 23에서 PNG2개 로드·필수 제목/표 표시 PASS. 스크린샷을 직접 확인했다.
- 최초 실행의 ChattyP 완료 응답이 유실됐으나 생성된 JSON·PNG 영수증을 읽어 확인했다. 브라우저 작업을 반복하지 않았다.
- 브라우저 결과는 보고서 렌더만 의미한다. 실제 서비스 UI 인수 NOT_RUN.
- 잔여 Windows 실환경·HEIC 지원 결정·실제 팀원 입력/서빙 인수는 공유 TODO로 유지.
- R2 점수8: 기존 safe_zone과 A 중앙 고딕 레이아웃 연결, 새 바인딩만 지시 강화. 기존 요청 hash/재사용은 보존.
- R2 계약: safe_zone top은 기존 최소값0.05를 따른다(이전 초안0.04 교정). CTA는 별도 내부 영역이며 상단 safe_zone 보장 범위로 표현하지 않는다.
- R2 완료 기준: 과거 바인딩 재사용, 새 지시 version, 제품 침범 거부, 한글 합성·크기·캡션 오프라인 검증. 실제 제품 가독성 승인과 구분.

## 2026-10-30 R2 A 템플릿 구현·검증

기존 safe_zone과 합성 geometry를 make_a_template으로 함께 제공했다. 기존 binding fingerprint는 유지하고 신규 safe_zone 요청만 v3 지시를 쓴다.
전체333 PASS; 과거 binding2건 재사용·새 요청·제품 침범/넘침 거절·한글 두 줄·PNG/캡션을 검증했다.
합성 fixture 시각 검토 완료. 실제 팀 카피·서빙 UI는 별도 NOT_RUN. 생성 API0.
코드 게시 전 logall 기록; 검증 영수증 fivecircles/test/image-mvp-relay-20261030-r2.md.

R2 stage ledger: Doc COMPLETED / Implementation COMPLETED / Validation333 PASS / Repair none / Push pending / Report pending.
다음 R3는 기존 원장·수집 재처리·전달 경계 및 실제 후보의 수동 검토다.

R2 코드 게시: 86de3ece0b5fb14463ae787b8c4eb0e7a515a99d, feat/image-generation 원격 SHA 일치.

## R2 Closeout → R3

코드 86de3ece0b5fb14463ae787b8c4eb0e7a515a99d 원격 일치. 전체333 PASS(3.79s).
현재 Mermaid/PNG/HTML과 ego-browser 렌더 검증 PASS. 제품 UI 인수는 NOT_RUN.
R2: Doc/Implementation/Validation/Push/Report COMPLETED; 전체 서비스 흐름은 GAP/PARTIAL.
R3 점수7: 기존 원장·재시도·이미지/캡션 전달 경계 감사, 보관된 실제 후보의 수동 제품/카피 영역 검토.
신규 기능은 확인된 이미지 모듈 gap만 보완한다. 실제 청구액·실서비스 인수는 없는 증거를 만들지 않는다.
