# BeautyAI FiveCircles

> **현재 기준 2026-10-27:** 10/26 회의 v0.2를 우선한다. 한국어 prompt/concept/mood, 이미지·합성 pi, 서빙 이형기, UI 권혁상. 모델 내부 Langfuse/W&B. MVP 베이스라인 팀 $10·자동 재시도0. 아래 과거 영어 전제는 현행 계약이 아니다. 실행 원본: docs/imageTodo.md K1~K3.
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

- **최신 사용자 정정(2026-10-22):** 김다혜는 LLM, pi는 외부 이미지 API 래퍼를 담당한다. 이미지 생성·편집·누끼는 OpenAI로 확정했다. 기본 `gpt-image-2.5-sunburst-2026-09-08`, 허용 대안 `gpt-image-2-2026-04-21`이며 실제 계정 권한·품질은 미확인이다. 로컬 모델 실험 기록은 보존하고 GPU 구축/튜닝·GCP CUDA 이전은 MVP 필수에서 제외한다. [B10 OpenAI 계약](architecture/contract/openai-images.md)과 [API 실행 TODO](../docs/imageTodo.md)를 우선한다.

- B10은 실제 호출 없이 어댑터·영속 제출/예약·내부 연결을 구현한다. 기본 live 차단·예산0이며 고정 job당 POST 1회다. 단일 PNG와 생성형 누끼는 기존 세 레이어·제품 픽셀 보존·승인/cache와 구분한다. 예제는 `python -m beauty_ai.openai_images_demo --output outputs/openai-wrapper-demo`이며 항상 mock이다. 실 API·품질·계정 권한은 검증하지 않았다.
- **B1~B9 역사 기록:** B4까지 로컬 자산·작업 API·worker·승인·template·프롬프트 수신·편집 revision/캐시를 검증했다. B5 로컬 평가 manifest·지표·작업 경과 시간 도구는 280 tests·CLI로 검증했다. B6 고정 BiRefNetLite는 실제 MPS/TCP 합성 fixture와 339 tests로 검증했다. B7 고정 SDXL은 실제 MPS 512 fixture와 433 tests로 기술 검증했다. B8 동일 ID 제출 조정은 521 tests와 실제 TCP 응답 유실 시험으로 검증했다. B9는624 tests·실제 MPS6 jobs의 단계/환경/snapshot과 동일512 재현을 확인했다. 별도 사용자 요청의 SSOT 대조·실행 TODO 문서도 작성했다. 신규 SSOT 12개 기능과 실패 뒤 후속 job 복구·부분 결과 자산 처리, GCP 이전은 미완료다. 광고 품질·실사진·CUDA·서비스 사용자 인증·렌더러 통합도 미검증이다. 최신 근거는 [릴레이 기록](work/relay.md)을 따른다.
- 이미지·합성: pi. 텍스트·한국어 프롬프트·검수: 김다혜. 서빙: 이형기, UI: 권혁상. 영어 변환은 고도화 범위다.
- M1: **2026-10-30 통합**, M2: **2026-11-06 이미지 디테일·고도화 완료**. 텍스트 최종 고정 11/10과 구분한다.
- 일정과 체크 상태의 원본은 [이미지 TODO](../docs/imageTodo.md)다. [LLM 참고 TODO](../docs/llmTodo.md)는 기존 초안이며 실제 팀 배정을 덮어쓰지 않는다.
- 검증: 저장소 루트에서 `python3 scripts/check_fivecircles.py`. 모델 변경 시 [테스트 정책](test/testpolicy.md)을 따른다.
- 전역 MCP·모델·서버를 설치하거나 시작하는 설정이 아니다. 프로젝트 내부 워크플로와 문서를 제공한다.

[기계 판독 설정](project.json) · [도입 계약](architecture/contract/framework-setup.md) · [Git 규칙](agent/git.md)
