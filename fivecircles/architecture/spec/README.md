# 기술 명세 인덱스

실험 비교: [W&B 계약](../contract/wandb-experiments.md), [실행 안내](../../../docs/wandbExperiments.md), [이미지·FinOps 임시 평가](../../../docs/imageEvaluationPlan.md).

현재 소스와 기존 문서가 근거다. 이 인덱스는 중복 API 정의를 만들지 않는다.

| 근거 | 범위 | 상태 |
| --- | --- | --- |
| [DESIGN.md](../../../DESIGN.md) | 서비스 계층·제안 API·구현 순서 | 설계; 서버 구현 완료 아님 |
| [contracts.py](../../../beauty_ai/contracts.py) | 입력/출력·해시·revision | 현재 구현 |
| [text_gateway.py](../../../beauty_ai/text_gateway.py) | 관찰·생성·검수·재작성 래퍼 | 실제 모델 연결 검증 필요 |
| [image_gateway.py](../../../beauty_ai/image_gateway.py) | 내부 이미지 job 제출·조회·대기 | 내부 API·로컬 worker 구현. OpenAI 단일 PNG adapter·영속 시도 기록은 B10 모의 검증. 실호출은 미수행 |
| [image_integrity.py](../../../beauty_ai/image_integrity.py) | 마스크 반전·동일 캔버스 RGBA 재합성 | 불투명 제품 픽셀 범위 |
| [review_service.py](../../../beauty_ai/review_service.py) | 규칙/모델 검수·snapshot 가드 | 실제 규칙집·승인 서비스 필요 |
| [schemas](../../../schemas/) | 현재 계약 JSON Schema | 변경 시 소스와 일치 검증 |
| [연동 점검](../../../docs/modelIntegrationReview.md) | 역할·영어 프롬프트·Safe Zone·재시도 | 공동 계약 미합의 항목 있음 |

## 변경 원칙

- VLM 관찰을 확인된 제품 사실로 자동 승격하지 않는다.
- 카피는 별도 텍스트 레이어다. 이미지 품질 승인과 문구 검수를 분리한다.
- 사용자 새 이미지 생성과 transport 재시도를 구분한다. 응답 유실 시 기존 job ID/hash를 유지한다.
- 같은 ID/hash 영속 처리·늦은 결과 방지는 서비스 경계에서 유지한다. 외부 API는 제공자 쿼터·동시성·외부 멱등성/중복 과금을 별도로 검증하며, GPU 직렬화는 자체호스팅을 다시 선택할 때만 적용한다.
- 영어 프롬프트/버전 수신은 내부 경로에 구현돼 있다. 외부 API 지원 필드·길이·모델 버전으로 매핑하기 전 계약과 예시를 기록한다.
- 완전 불투명 영역 외 반투명 경계/유리/반사는 별도 시각 평가가 필요하다.

- [OpenAI B10 계약](../contract/openai-images.md) · [현재 흐름 비교](image-runtime-flow.md) · [검증](../../test/relay-b10.md).
