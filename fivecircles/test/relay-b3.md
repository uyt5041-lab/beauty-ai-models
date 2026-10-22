# B3 검증 · PASS_WITH_RISKS

2026-10-22 KST · [구현 계약](../architecture/contract/image-runtime.md).

- root 실행 근거: `.venv/bin/python -m pytest -q -W error` → **148 passed in 12.14s**.
- 패키징(root 실행 근거): `pip wheel --no-deps --no-build-isolation` PASS. 추출한 wheel을 Python `-I` 격리 모드에서 읽어 runtime/approval 모듈을 import했고, 소스 경로가 checkout 밖의 패키지 산출물임을 확인했다.
- 담당 구현 검증: 승인 저장소 17개, processor 17개, runtime 1개. 관련 35 tests PASS(3.95s).
- 실제 TCP CLI smoke: 별도 `python -m beauty_ai.inference_runtime` 프로세스를 loopback에 기동하고 `examples.run_image`의 prepare→candidate download→approve→render→get-job→preview download 명령을 실행.512×512 PNG 확인 후 정상 종료. 모델 호출은 없고 `TEMPLATE_NOT_AI_GENERATION` 경고를 유지했다.
- 승인: 준비 시 approved=false, literal true 확인, 정확한 candidate hash, project 격리, 반복 승인·재시작·동시 프로세스 검증. 실제 파일·binary mask·alpha 영역·Safe Zone을 승인/실행 전에 재검증.
- 프로필: uploaded-alpha/1은 이미 투명한 RGBA만 허용. bright-template/1은 서버 고정 style/layout/revision,256~1024 및8배수. 승인 RGBA와 scene/preview를 구분하고 불투명 픽셀·파일을 확인.
- 독립 읽기 검토: 현재 계약 범위의 차단 결함 미발견. GET 후보는 저장된 승인 기록 조회이며 현재 파일 유효성은 승인/실행에서 검증한다. 승인 기록은 실제 사람 신원·최종 광고 품질·현재 편집 선택의 증거가 아니다.
- 추가 수리: HTTPImageGateway가 모든409를 IDEMPOTENCY_CONFLICT로 해석하던 문제를 수정했다. PRODUCT_APPROVAL_REQUIRED/CONFLICT는 허용 목록으로 구분하며 기존409 의미는 유지한다. 신규 회귀1개와 관련 38 tests PASS(2.83s).
- 수리: 초기 processor 테스트의 변조 자산 기대값을 승인 경계의 `INVALID_IMAGE_REQUEST`로 맞췄다. 파일 거절 동작은 처음부터 정상. [오류 기록](errorlogs/2026-10-22-relay-b3.md).
- 보고서: [목표/현재 비교](../architecture/spec/image-runtime-flow.html). 브라우저 검증 PASS: PNG 2개 decode, 제목 7개·범례 5개·GAP 12행·우선순위 6행, 가로 넘침 없음. [스크린샷](playwright-screenshots/image-runtime-flow-b3.png)은 검증 결과를 문서에 도장 찍기 직전 화면이다.
- 전체 목표 판정: **GAP/PARTIAL**. 실제 GPU/누끼·배경 AI, 프롬프트 수신·편집 revision·캐시, 렌더러·평가셋·사람 인수는 남음.
- 이 배치는 로컬 API·template 실행 코드 전달이다. 운영 CD NOT_APPLICABLE, Git publication은 root의 push/원격 SHA 확인으로 별도 기록한다.
