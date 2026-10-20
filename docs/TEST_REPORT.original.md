# 검증 결과

검증일: 2026-10-20

## 수행한 검증

```text
....................................                                     [100%]
36 passed in 0.15s
```

명령: `python -m pytest -q -W error`

- 경고를 오류로 처리한 상태에서 36개 테스트 통과.
- 제품 관찰/카피 후보/검수/수정 카피의 LLM 출력 schema가 객체별 `additionalProperties=false`이고 모든 필드를 required로 선언하는지 정적 확인.
- Python 구문 컴파일 확인.
- 실제 OpenAI SDK 호출은 대체 응답 객체를 사용했고, HTTP는 httpx.MockTransport로 검증.
- 마스크 변환과 알파 합성은 Pillow로 실제 연산해 확인.

## 미검증 및 미구현

OpenAI SDK는 실행 환경에 설치되어 있지 않았고 네트워크 제약으로 설치하지 못했습니다. 따라서 실 SDK import/실계정 모델 지원/외부 API 호환성/과금/응답 품질은 검증하지 않았습니다. 호출 형식은 공식 문서를 확인해 작성했습니다.

실제 GCP, SDXL, BiRefNet, LCM, IP-Adapter는 호출하지 않았습니다. 생성 화질/속도/VRAM/투명용기 정확도/진짜 모델 실행 후 제품 보존을 측정하지 않았습니다.

private inference 서버, 멱등성 DB, GPU 실행 직렬화, API 인증·권한, 영속 큐, SSE, renderer, 예산 원장, 실규제 rule pack은 제공하지 않았습니다. 이들은 설계 계약으로만 제시했습니다.

## 테스트 실행 환경

- pydantic: 2.13.5
- httpx: 0.28.1
- Pillow: 12.3.0
- pytest: 9.0.2
- pytest-asyncio: 1.3.0

패키지 의존성 범위는 실제 배포 환경의 lockfile을 대체하지 않습니다.
