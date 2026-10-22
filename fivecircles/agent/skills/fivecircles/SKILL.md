---
name: fivecircles
description: Initialize and run the BeautyAI six-stage workflow using project requirements, image and LLM TODOs, tests, and fast-forward Git rules.
---

# BeautyAI FiveCircles

- 프로젝트 루트 `fivecircles/README.md`를 먼저 읽는다.
- `fivecircles/agent/authority.md`, `fivecircles/agent/workflow.md`, `fivecircles/agent/operational-guidance.md`, `fivecircles/agent/collaboration-protocol.md`를 읽고 역할/현재 요청 범위를 확인한다.
- `fivecircles/requirements/current.md`, `fivecircles/requirements/decisions.md`, `fivecircles/architecture/spec/README.md`, `fivecircles/architecture/todolist.md`, `fivecircles/work/update.md`에서 필요한 문서만 연다.
- 이미지 pi, 텍스트 김다혜. 기존 LLM 참고 초안의 pi 표기를 실제 팀원 배정으로 사용하지 않는다.
- docs의 원본 체크리스트를 보존한다. 미합의 제안을 완료로 표시하지 않는다.
- 변경 전 영향/계약을 설명하고 작은 범위를 구현해 `fivecircles/test/testpolicy.md`에 따라 검증한다.
- 로그는 `fivecircles/work/`, 실제 실패는 `fivecircles/test/errorlogs/`에 기록한다. 원본 TODO 체크 상태는 검증 근거가 있을 때만 바꾼다.
- Git 반영 요청 시 `fivecircles/agent/git.md`를 따른다. PR은 만들지 않으며 강제 push하지 않는다.
- 프로젝트 내부 스킬이다. 전역 MCP/모델/배포 설정이나 자동 외부 메시지를 설치/실행하지 않는다.
