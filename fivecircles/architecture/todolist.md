# BeautyAI 작업 인덱스

이 파일은 배치와 원본 체크리스트를 연결한다. 제품 항목의 체크 상태는 링크한 원본 한 곳에서 관리한다.

## BEAUTY-FC-001 · 프레임워크 도입 · CUSTOM

완료 기준: [도입 계약](contract/framework-setup.md). 검증 결과는 [도입 검증](../test/setup-validation.md)에 기록한다.

- [x] 원본 저장소/고정 버전/라이선스 기록
- [x] BeautyAI 담당·계약·Git·테스트·로컬 스킬 구성
- [x] 기존 소스/TODO 보존, 경로와 두 폴더 일치 확인

## 모델 구현 배치

| 배치 | 시점 | 원본 / 종료 조건 |
| --- | --- | --- |
| 계약/입력 정리 | 10/22–10/26 | [LLM·이미지 연결 점검](../../docs/modelIntegrationReview.md), 제안과 합의 구분 |
| M1 통합 | 10/27–10/30 | [이미지 TODO](../../docs/imageTodo.md), 실제 API·모델·재시도 사례 |
| M2 디테일/고도화 | 11/02–11/06 | [이미지 TODO](../../docs/imageTodo.md), 품질·성능·복구·인수 기준 |
| 텍스트 연동 유지 | 텍스트 최종 고정 11/10까지 | [LLM 참고 TODO](../../docs/llmTodo.md)와 팀원 원문 구분, 버전 변경 영향 회귀 |

구현할 항목을 선택하면 작업 ID·원본 항목 링크·담당·완료 기준·검증을 work/worklog에 기록한다. 프레임워크 초기화는 모델 완료를 뜻하지 않는다.

## 구현 릴레이 (날짜는 대기 조건 아님)

- [x] B1 자산 파일/DB·캔버스·보존 검사 구현 및 72 tests/CLI 검증. [원본 완료 항목](../../docs/imageTodo.md), [검증](../test/relay-b1.md).
- [x] B2 영속 작업 API·원자적 멱등성·단일 호스트 worker 복구. 112 tests 및 원격8cb6402 확인.
- [ ] B3 profile·승인·실행 연결 및 결과 파일 검증. 로컬148 tests·CLI PASS, publication 마무리 중.
- [ ] B4 revision/캐시·평가/인계, 남은 LLM 공통 경계.
- [ ] 실제 GPU·모델 품질·팀 인수: 실행 환경/공동 확인 근거 필요.
