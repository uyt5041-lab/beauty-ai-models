# BeautyAI FiveCircles

FiveCircles의 6단계 개발 흐름을 beautyAi에 적용한 프로젝트 설정이다.
원본은 [uyt5041-lab/fivecircles](https://github.com/uyt5041-lab/fivecircles)이며 © 2026 Pio, [CC BY 4.0](LICENSE)로 제공된다. 변경 내역과 고정 버전은 [UPSTREAM.md](UPSTREAM.md)를 참고한다.

## 시작 순서

1. [에이전트 진입점](agent/README.md), [권한 순서](agent/authority.md), [작업 흐름](agent/workflow.md).
2. [현 요구사항](requirements/current.md)과 [확정/미확정 결정](requirements/decisions.md).
3. [기술 인덱스](architecture/spec/README.md), [담당 경계](agent/collaboration-protocol.md).
4. [TODO 인덱스](architecture/todolist.md), [최근 기록](work/update.md), [테스트 정책](test/testpolicy.md).

“fivecircles 초기화”라고 요청하면 위 문서를 읽고 역할·작업·미완료 조건부터 확인한다. “이미지 TODO의 다음 항목 진행”은 원본 TODO를 기준으로 범위를 정한 뒤 구현과 해당 검증까지 진행한다.

## 구조

| 위치 | 용도 |
| --- | --- |
| requirements/ | 요구사항, 확정 결정, 아직 합의하지 않은 질문 |
| architecture/spec/, architecture/contract/ | 기술 근거, 구현 전 계약 |
| architecture/todolist.md | 기존 TODO로 연결하는 작업 인덱스 |
| agent/ | 담당, 협업, Git, 로컬 스킬 |
| work/ | 변경·검증·Git 반영 기록 |
| test/ | 검증 기준, 실패와 재발 방지 |
| maintenance/, scoring/ | 남은 개선, 선택적 평가 |

## BeautyAI 기준

- B3까지 로컬 자산·영속 작업 API·worker·제품 승인 저장·제한 template 실행을 구현했다. 실제 GPU/누끼·배경 모델·서비스 사용자 인증·렌더러 통합은 미검증이다. 최신 근거는 [릴레이 기록](work/relay.md)을 따른다.
- 이미지: pi. 텍스트·영어 프롬프트: 김다혜. API/렌더러 담당은 별도 확인 대상이다.
- M1: **2026-10-30 통합**, M2: **2026-11-06 이미지 디테일·고도화 완료**. 텍스트 최종 고정 11/10과 구분한다.
- 일정과 체크 상태의 원본은 [이미지 TODO](../docs/imageTodo.md)다. [LLM 참고 TODO](../docs/llmTodo.md)는 기존 초안이며 실제 팀 배정을 덮어쓰지 않는다.
- 검증: 저장소 루트에서 `python3 scripts/check_fivecircles.py`. 모델 변경 시 [테스트 정책](test/testpolicy.md)을 따른다.
- 전역 MCP·모델·서버를 설치하거나 시작하는 설정이 아니다. 프로젝트 내부 워크플로와 문서를 제공한다.

[기계 판독 설정](project.json) · [도입 계약](architecture/contract/framework-setup.md) · [Git 규칙](agent/git.md)
