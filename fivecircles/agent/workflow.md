# Workflow OS (Agent Runtime)

이 문서는 “에이전트가 어떻게 행동해야 하는가”를 정의합니다.
기술 설계/분석 내용은 **architecture/spec/**에만 기록하고, 여기에는 운영 절차만 둡니다.

## Development Cycle (6-stage)
1) Requirements
2) Design
3) Implementation
4) Test
5) Integrate (Commit/Push)
6) Maintenance

---

## Stage 1) Requirements
Goal: 요구사항을 명확히 하고, 모순을 발견하고, 합의 가능한 형태로 고정한다.

Actions:
- requirements/current.md 읽기
- 불명확/충돌/리스크가 있으면 requirements/debates/에 debate 파일 생성
- 합의된 결론을 requirements/decisions.md에 기록(확정)

Exit checklist:
- DoD(성공조건) 최소 1개
- 범위(in/out) 명시
- 모호한 용어 정의(필요 시)
- 결정사항이 decisions.md에 “확정”으로 남음

---

## Stage 2) Design(Architecture)
!중요: 에이전트는 구현 전 설계문서에 맞게 작업을 수행하도록 /architecture/spec 의 내용을 확인한다.
Goal: Batch를 작은 태스크로 분해하고 테스트 접근을 함께 적는다.

Actions:
- requirements/current.md, requirements/decisions.md 확인 후 필요한 상세 스펙 작성: 모델 입력/출력, 이미지 job 상태, 제품 보존, 승인·검수, 오류와 버전 계약
- architecture/todolist.md에 Batch 작성/수정
- todo 완료 체크 후 정리, 남은것 확인, 작업추가

Exit checklist:
- 태스크가 작은 산출물로 분해됨
- 각 태스크별 테스트 접근(단위/통합) 1줄 이상

---

## Stage 3) Implementation
!중요: 에이전트는 구현 전 설계문서에 맞게 작업을 수행하도록 /architecture/spec 의 내용을 확인한다.
Goal: 선택된 방법론 프로파일(TDD/Custom/None)에 따라 구현한다.

Actions:
- work/worklog.md에 Intent/변경점 기록
- 필요한 기술 분석은 architecture/spec/에 정리(예: API, DB schema, 성능 고려)

Exit checklist:
- 변경점(what/why)이 로그에 남음
- 최소 단위 기능 1개 완료

---

## Stage 4) Test
Goal: 자동화된 테스트로 기능을 검증한다.

Actions:
- testpolicy에 따라 unit/integration 실행
- 실패 시 test/errorlogs/에 날짜 파일 생성
- work/worklog.md에 증거(출력/CI 링크 등) 기록

Exit checklist:
- 변경 범위에 해당하는 검증 통과(문서/설정은 경로·내용·해시 검사)
- 통합 테스트 통과(해당 시; 실모델/GPU 미실행은 NOT_RUN)
- 리그레션 체크 기록

---

## Stage 5) Integrate (Commit/Push)
Goal: 요청된 반영 범위와 실제 검증 결과를 Git에 고정한다.

- `agent/git.md`를 따른다. 로컬 설정 요청만 있으면 검증된 변경 상태로 남기고 반영 상태를 명시한다.
- 커밋/푸시가 요청되면 fetch, 원격과의 선후 관계, 변경 파일, 비밀값 제외를 확인한다.
- 테스트 정책에서 해당되는 검증을 통과한 변경만 선택해 커밋한다. 보존할 사용자 변경은 분리한다.
- `git merge --ff-only origin/plan/models`로 최신화할 수 있을 때만 진행한다. 강제 push와 임의 rebase를 하지 않는다.
- 일반 push 후 로컬 HEAD와 원격 SHA를 비교하고 `work/integrate-log.md`에 결과를 기록한다.
- PR 생성 및 협업일지 브랜치와의 병합은 하지 않는다.

---

## Stage 6) Maintenance
Goal: 기술부채/새 요구사항을 정리하고 Requirements로 환류한다.

Actions:
- maintenance/maintenance.md 업데이트
- 요구사항 변경이 있으면 requirements/current.md 업데이트 + decisions.md에 기록

Exit checklist:
- 기술부채 후보 기록
- 요구사항 변경 시 Requirements로 환류 완료
