# 기술 명세 인덱스

현재 소스와 기존 문서가 근거다. 이 인덱스는 중복 API 정의를 만들지 않는다.

| 근거 | 범위 | 상태 |
| --- | --- | --- |
| [DESIGN.md](../../../DESIGN.md) | 서비스 계층·제안 API·구현 순서 | 설계; 서버 구현 완료 아님 |
| [contracts.py](../../../beauty_ai/contracts.py) | 입력/출력·해시·revision | 현재 구현 |
| [text_gateway.py](../../../beauty_ai/text_gateway.py) | 관찰·생성·검수·재작성 래퍼 | 실제 모델 연결 검증 필요 |
| [image_gateway.py](../../../beauty_ai/image_gateway.py) | 이미지 job 제출·조회·대기 | 클라이언트; GPU 서버 없음 |
| [image_integrity.py](../../../beauty_ai/image_integrity.py) | 마스크 반전·동일 캔버스 RGBA 재합성 | 불투명 제품 픽셀 범위 |
| [review_service.py](../../../beauty_ai/review_service.py) | 규칙/모델 검수·snapshot 가드 | 실제 규칙집·승인 서비스 필요 |
| [schemas](../../../schemas/) | 현재 계약 JSON Schema | 변경 시 소스와 일치 검증 |
| [연동 점검](../../../docs/modelIntegrationReview.md) | 역할·영어 프롬프트·Safe Zone·재시도 | 공동 계약 미합의 항목 있음 |

## 변경 원칙

- VLM 관찰을 확인된 제품 사실로 자동 승격하지 않는다.
- 카피는 별도 텍스트 레이어다. 이미지 품질 승인과 문구 검수를 분리한다.
- 사용자 새 이미지 생성과 transport 재시도를 구분한다. 응답 유실 시 기존 job ID/hash를 유지한다.
- 같은 ID/hash 영속 처리·GPU 직렬화·늦은 결과 방지는 서버 구현으로 검증해야 한다.
- 영어 프롬프트/버전의 현재 스키마 전달은 미완성이다. 필드 변경 전 계약과 예시를 먼저 기록한다.
- 완전 불투명 영역 외 반투명 경계/유리/반사는 별도 시각 평가가 필요하다.
