# 브랜치 업로드 검증 기록

검증일: 2026-10-20

## 현재 실행 결과

```text
python -m pytest -q -W error
....................................                                     [100%]
36 passed in 1.73s
```

- Python 3.14.7의 별도 가상환경에서 `pip install -e '.[test]'`로 설치했습니다.
- 사용 버전: pydantic 2.14.1, httpx 0.28.1, openai 3.35.1, Pillow 13.0.0, pytest 9.1.1, pytest-asyncio 1.4.0. 재현을 위한 관찰 기록이며 lockfile은 아닙니다.
- `python -m compileall -q beauty_ai examples tests` 통과.
- 실제 OpenAI SDK import 및 `AsyncOpenAI.responses.parse` 메서드 존재 확인 통과. 예시 키로 클라이언트 객체만 생성·종료했고 네트워크 요청은 하지 않았습니다.
- SDK 응답은 대체 객체, 이미지 서버 HTTP는 `httpx.MockTransport`로 검증했습니다. Pillow 마스크 변환·알파 합성은 실제 로컬 연산으로 검사했습니다.

## 테스트가 확인한 범위

검수 규칙 보존, 미확인·실패·오래된 검수 결과의 처리, 출력 구조와 도메인 참조 검증, 이미지 작업 ID·입력 해시·결과 유형 확인, 불확실한 제출과 폴링 시간 제한, 제품 알파 마스크와 합성 유틸리티를 대상으로 한 단위 검증입니다. 실제 광고 품질이나 규제 적합성을 입증하지 않습니다.

## 미검증·미구현

- 실제 OpenAI 계정의 모델 지원·API 응답·과금·한국어 품질.
- 실제 private inference 서버, GCP, SDXL, BiRefNet, LCM, IP-Adapter, GPU 속도·VRAM·제품 보존 품질.
- UI, FastAPI 라우트, 인증·프로젝트 권한, 영속 DB·작업 복구·SSE, 파일 저장·내보내기, 실제 서비스 E2E.
- 실제 법규 기반 규칙집의 완전성·최신성·검토 및 광고의 법적 적합성.

## 원본 기록

ZIP에 포함됐던 검증 결과는 [docs/TEST_REPORT.original.md](docs/TEST_REPORT.original.md)에 그대로 보존했습니다. 원본의 “OpenAI SDK 미설치”는 당시 기록이며, 이번에는 SDK 설치·import까지 확인했습니다. 양쪽 모두 실제 외부 API·GPU 실행을 검증한 결과는 아닙니다.
