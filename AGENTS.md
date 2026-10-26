# BeautyAI 작업 지침

## 저장된 그래프 우선 사용

- 코드 탐색은 `graft check .`로 저장된 그래프의 최신 여부를 확인한 뒤 `graft map .` 또는 `graft ask "질문" .`부터 사용한다.
- 그래프가 오래됐으면 `graft build .`로 갱신한다. 함수 구조·호출 관계는 `graft skeleton`, `graft callers`로 좁히고 필요한 원문 구간만 읽는다.
- 저장된 deep 요약을 재사용한다. 전체 파일 반복 조회, 동일 질의 반복, 매 작업의 `--deep` 재생성을 피한다. 요약으로 확정할 수 없는 동작과 변경할 코드는 해당 원문으로 확인한다.
- `graft build --deep .`는 별도 LLM API 호출이다. 키는 실행 환경에서만 전달하고, API 키가 없어 구조 빌드로 대체되거나 의미 요약이 일부 실패하면 deep 완료로 보고하지 않는다.

이 저장소에서 시작할 때 [FiveCircles](fivecircles/README.md)를 읽는다.

- 현재 작업은 Python 모델 래퍼와 이미지 구현 계획이다. 현재 구현과 계획을 구분한다.
- 이미지 담당 pi, 텍스트 담당 김다혜. 담당·경계는 [협업 규칙](fivecircles/agent/collaboration-protocol.md)을 따른다.
- 원본 체크리스트는 `docs/imageTodo.md`, `docs/llmTodo.md`, `docs/modelIntegrationReview.md`다. 실제 담당/합의 상태를 유지하며 중복 체크리스트를 만들지 않는다.
- 설계/계약 영향이 있으면 변경 전에 의도·범위·입출력 예시를 설명한다. 미합의 내용을 확정으로 쓰지 않는다.
- README는 사용법·기능·아키텍처·보완점·하지 말 것의 5개 절을 유지한다. 추가 안내는 README2에 둔다.
- Git은 현재 `plan/models`와 독립 `박지수_협업일지`를 유지한다. PR 생성이나 두 브랜치 병합을 하지 않는다. 반영이 요청되면 fast-forward 방식으로 확인하고 일반 push한다.
- 2026-10-26 사용자 요청: 이미지 구현 전체의 통합 기준은 `codex/image-main`이다. `plan/models` 이력에서 이어지며 별도 feature 조각으로 나누지 않는다. 구조 설명은 `docs/imageArchitecture.md`를 따른다. PR과 협업일지 병합은 하지 않는다.
- 승인된 작업은 자율적으로 진행한다. 템플릿을 이유로 추가 승인 절차를 만들지 않는다. 자동 커밋/푸시·배포·외부 메시지 전송 권한은 이 파일만으로 생기지 않는다.
- 기존 사용자 변경을 보존한다. 비밀값·모델 가중치·생성 이미지·캐시는 공개 Git에서 제외한다.
- 설정 검사: `python3 scripts/check_fivecircles.py`. Python 모델 검증: `python -m pytest -q -W error` (test 의존성 설치 후).
