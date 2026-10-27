# K1~K3 검증 · 2026-10-27

- 기준: 사용자 요청, Notion10/26 회의, 계약v0.2 dev0b5bb47.
- 범위: 한국어 프롬프트·Python 연결 어댑터·원본 보존 리사이즈.
- K1: `pytest -q -W error tests/test_korean_prompt.py tests/test_image_prompt.py tests/test_external_image_contracts.py tests/test_openai_images.py tests/test_langfuse_prompts.py tests/test_cosmetics_prompts.py` → 268 PASS.
- K2: backend_adapter/api_image_processor/openai_images/korean_prompt → 123 PASS.
- K3: ad_resize/backend_adapter → 35 PASS.
- 전체: `.venv/bin/python -m pytest -q -W error --junitxml=outputs/backend-relay-20261027/pytest.xml` → 1363 PASS / 23.80초.
- CLI: `python -m examples.backend_handoff --output outputs/backend-relay-20261027/demo` → Fake 제공자1회, 실제 API0회, 재전송 결과 동일, 원본1152×1440과 최종1080×1350 별도 보존.
- 실패: 위 검증에서 실패0. SQLite 재시작/동시 중복/변경 입력/timeout/손상 데이터의 의도된 거절은 테스트 PASS다.
- 영수증: 추정 청구를 만들지 않음. actual_cost_usd=null; 예약은 청구와 별도. 경과 시간은 원장 시작/종료 wall-clock.
- 이미지 품질: 일정 색상·비율·알파 경계·ICC·EXIF·동일 크기 바이트 보존·PNG 디코딩 픽셀 일치. 축소 정보 무손실 주장 없음.
- 한계: Mock transport의 실제 HTTP 직렬화 경계를 시험했다. 팀 FastAPI/인증/검수/예산/전체 브라우저/실제 API·광고 품질은 NOT_RUN.
- 최종 판정: 라이브러리 범위 PASS. 전체 서비스 PARTIAL. CD NOT_APPLICABLE.
- 목표/현재 Mermaid 및 HTML: outputs/backend-relay-20261027/flow/ (로컬 생성 증거, Git 제외). 공개 소스는 fivecircles/architecture/spec/backend-adapter-{target,current}.mmd.


## 흐름 대조와 후속 우선순위

[목표 Mermaid](../architecture/spec/backend-adapter-target.mmd) · [현재 Mermaid](../architecture/spec/backend-adapter-current.mmd)

| 연결 | 상태 | 근거 / 후속 |
|---|---|---|
| 한국어→바이트 변환→영속 래퍼→원본 결과 | MATCHED | 직렬화·재시작·동시 요청·실행 기록 시험 |
| 원본→4:5 축소→최종 크기/해시 | MATCHED | 합성 이미지 및 기존10/23 화장품 PNG 재사용. 원본 해시 유지 |
| 팀 서버→모델 | GAP, 이번 범위 밖 | HTTP·승인·팀 예산 연결은 서빙 작업 |
| 최종 크기→한글 완성→다운로드 | GAP, 후속 | 모델 내 글자/레이어 방식 실험 후 전체 합성 구현 |

점수=상류0~4+연결0~3+안전0~2+검증0~2-위험0~3. 후속 서빙 연결9, 한글 합성7, 타이포 실험6. 사용자 지정 K1~K3만 완료했으므로 범위 확대 없이 종료한다.

브라우저(Ego)에서 목표/현재 PNG와 원본/축소 PNG 4개 로드, 범례·단계·점수 표를 확인하고 스크린샷을 저장했다. 같은 표시 크기에서 제품 색·형태의 눈에 띄는 변화는 보이지 않았다. 이는 모든 이미지의 지각 무손실 보장이 아니다. Playwright 별도 실행은 하지 않았다. Python 라이브러리 검증이며 팀 React 사용자 E2E는 NOT_RUN이다.

라이브러리와 /Users/pio/IdeaProjects/beautyAi 작업 폴더의 변경 파일을 대조해 동기화했다. .env·생성 PNG·테스트 캐시는 Git에 추가하지 않았다. 저장된 Graft 구조 그래프를 갱신했으며 유료 deep 재생성은 하지 않았다.
