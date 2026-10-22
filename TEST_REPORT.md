# 브랜치 업로드 검증 기록

## 최신 릴레이 검증 · 2026-10-22

실행 코드·실제 모델·정적 문서는 아래 범위로 검증했다. 최종 HTML 보고서의 브라우저 재검증은 `file:` 접근에 대한 자동 승인 검토 거절로 BLOCKED다. 다른 브라우저/주소로 우회하지 않았으며 수정 전 스크린샷을 최종판 증거로 사용하지 않는다. 이 제한은 검증한 Python 소스의 Git 게시를 금지하는 제한과 구분한다. SSOT 대조는 문서 작업이며 새 MVP 기능을 구현·인수한 결과가 아니다.

- Python3.11.15, `.venv/bin/python -m pytest -q -W error`: **624 passed in 36.32s**.
- B1 자산·캔버스72 → B2 API·작업 저장/복구112 → B3 승인·제한 profile·로컬 template148 → B4 프롬프트·편집/cache201 → B5 평가·작업 시간280 → B6 모델준비·누끼어댑터339 → B7 SDXL 고정 파일·어댑터·CLI433로 확장했다. 자동 테스트는 실제 가중치를 내려받지 않으며 실모델 smoke는 아래에 구분한다.
- 실제 파일/SQLite, 프로세스 경쟁·강제 종료 복구, 실제 TCP API와 CLI 6명령을 검증했다. 512×512 결과 PNG를 저장·다운로드했다.
- [B1](fivecircles/test/relay-b1.md), [B2](fivecircles/test/relay-b2.md), [릴레이 진행](fivecircles/work/relay.md).
- B6에서 실제 BiRefNet Lite의 Mac MPS 누끼를, B7에서 SDXL의 Mac MPS 배경 생성을 검증했다. GCP CUDA·OpenAI 유료 호출·실제 광고 품질·팀 인수는 검증하지 않았다. 기존 배경 템플릿은 모델 추론 결과가 아니다.
- B5: 실제 파일·정답 마스크·빈 마스크·권리 미확인·누락 정답·경로 제한·FIFO 거절·보고서 덮어쓰기 차단, 실패/중단 작업의 시간과 clock 역행을 검사했다. manifest 예제 CLI는 1건 NOT_ASSESSED, 실제 TCP 시간 조회 CLI는 PASS(70.61ms 서버 wall-clock, GPU 시간 아님).
- 동료 검토에서 manifest가 FIFO이면 멈출 수 있는 문제를 발견해 nonblocking open과 regular-file 검사로 수정했다. 실제 FIFO 회귀 테스트와 전체 테스트를 통과했다.
- B5 wheel 생성 PASS, SHA256 `79bff5bc59cc8bf1319edd0e5d0dd352b3b6180dca67424cf6e85c0679990b2c`.
- B6: 고정 revision `aa62cd87eafb9cc43056d08ef3615a14628b831d`의 코드·설정·177,634,392바이트 가중치 SHA256 검증 PASS. 네 파일 외 코드는 로딩하지 않으며, 실행은 HF offline/telemetry disabled로 수행했다.
- 실제 M4 Max64GB/MPS에서 합성 병 이미지512×512를 TCP API와 prepare CLI로 실행했다. RGBA·binary 보호마스크·미승인 후보 저장, RGB보존, alpha0..255, 수동확인경고 PASS. 첫 job 경과6897.11ms/CLI7.587s는 로딩·큐·저장을 포함한다. GPU연산시간·실사진품질이 아니다.
- 같은 합성 fixture의 파일hash를 실제job출력과 연결해 평가 도구를 실행했다. IoU0.9692321320, boundaryF1 0.7558205565(2px 정사각 허용), opaque보존true. 1개 dev fixture 결과이며 품질승인 NOT_ASSESSED다.
- 검증 환경: torch2.14.1/torchvision0.29.1/transformers5.20.2/timm1.0.30/kornia0.8.3/einops0.8.2/numpy2.4.6. pip check·MPS tensor smoke PASS. 실제 import 중 upstream torch.jit deprecation 경고는 있었고 실행 오류는 없었다. CUDA와 다른버전은 미검증.
- B6 동료 검토: 모델 다운로드의 HTTPException을 안전코드로 변환하고 임시파일 정리 회귀를 추가했다. B6 wheel 생성 PASS, SHA256 `1932d8a7a0bc2b792caf49e839b0596dcf27fea83b48e2eef9fa6c9276b373f8`.
- B7 자동 검증: 20개 고정 파일·중첩 경로·병렬 다운로드 실패 정리·누락/변조 거절, 명시적 모델/장치·두 tokenizer 길이 검사·제품 보존·텍스트 여백 복원·안전한 오류를 확인했다. 라이브러리 import와 `pip check` PASS.
- B7 동료 검토에서 정확히 전체 검정인 원출력을 성공으로 넘기는 공백을 재현했다. 저장 전에 `IMAGE_RESULT_INVALID`로 거절하고, 실패 시 새 자산이 없음을 검증했다. 임의 밝기 기준은 추가하지 않았다.
- B7 실제 첫 두 시도는 모델 로딩 뒤 `MODEL_UNAVAILABLE`로 종료했다. 원인은 Diffusers0.41에서 제거된 pipeline.enable_vae_tiling 호출이었다. 실패 기록을 보존하고 지원 API인 pipeline.vae.enable_tiling과 dtype 인자로 수정했다. 모델 파일 없이 실제 VAE/loader API를 검사하는 회귀2개도 통과했다.
- 수리 후 세번째 실제 MPS/TCP CLI 실행 PASS. 두 모델 모두 호출했고 CPU fallback은 껐다. 합성 fixture512×512, 불투명770픽셀 중 불일치0, Safe Zone 정확한 중립색, 세 결과 파일의 다운로드 SHA와 AssetRef 일치. 배경 job14700.575ms, 누끼2690.63ms, render CLI15.343s는 서버/클라이언트 경과이며 GPU 순수 연산 시간은 아니다.
- 실행 후 MPS allocated7,135,957,248B, driver10,240,196,608B를 관찰했다. 사후 snapshot이며 최대 메모리·CUDA VRAM이 아니다. 런타임의 upstream VAE upcast FutureWarning은 기록됐고 결과 배열의 유한 값·범위 검사와 파일 검증을 통과했다.
- 결과를 직접 열어 보니 꽃 표현이 부족하고 생성된 제품 잔상이 보였다. 기술 실행 PASS를 광고 품질 합격으로 확대하지 않는다. 합성 테스트 확인만 수행했으며 실사진 품질·안전성·사람 인수는 NOT_ASSESSED다.
- B7 최종 wheel 생성·격리 import PASS, SHA256 `614b5570fc177d22dde87e4fb10c60018a6119ebe7fce99883f03dca1b2d673c`. 기본 import는 torch·diffusers를 로딩하지 않는다.
- B8: 동일 ID 제출·조회 조정84개와 실제TCP 응답 유실4개를 추가했다. 실제 서버가 SQLite에 commit한 뒤 전송 경계에서 응답을 유실시켰고, PUT1회·fixture processor1회·동일ID조회·파일hash·프로젝트권한을 검증했다. 이는 실제GPU 장애나 운영프록시 인수 검증이 아니다.
- B8 기한 종료: 작업 확인 전 SubmissionUncertain과 확인 후 JobStillPending을 구분했다. 기한이 지나도 원래 worker는 계속 실행하고, 같은ID로 후속 조회해 결과를 받았다. 새ID·자동PUT·취소는 수행하지 않았다.
- B8 검토 수리: 자유문자열 error_code가 CLI로 출력될 수 있던 경계를 ASCII 안정 코드와 실패상태 검사로 제한했다. 잘못된 코드/상태는 IMAGE_PROTOCOL_ERROR, 정상코드는 유지한다. unit·실TCP·상호검토·전체회귀 PASS.
- B8 wheel 생성·격리 lazy import PASS, SHA256 `8b11c376e19eecdd4eb1dac8ffd4e69f8c0775abc1687f191a55b299ac1f4d43`.
- B9: worker 실행 관찰·SQLite 원자 저장·기존/중단 작업 미기록·프로젝트/hash/상태 대조·안전한 오류·API/CLI를 검증했다. 모델 어댑터는 기존 생성 설정과 결과를 유지하면서 단계·재사용·동기화·작업 후 메모리 snapshot을 기록한다. 수집 실패를 모델 실패로 바꾸지 않는다.
- B9 실제 MPS/TCP 측정: 합성 입력 1개, 누끼1건+배경5건 모두 성공. 배경512 첫 실행47.170초(모델 로딩15.982초), 같은 요청 반복28.488초, 768정사각57.418초, 1024정사각104.426초, 512×768세로50.855초. worker monotonic wall-clock이며 순수 GPU 시간·속도 보장이 아니다. 동일512 요청/seed 두 결과의 파일hash는 일치했지만 다른 환경까지 보장하지 않는다.
- 모든 배경 결과의 다운로드hash/해상도/Safe Zone/불투명 제품 픽셀 검사와 실행 기록 CLI가 통과했다. 작업 후 MPS driver snapshot 중 가장 큰 관찰값은19,876,773,888B였으며 peak가 아니다. 배경5건 중 실패0은 이 작은 합성 시험의 결과이지 일반 실패율 추정이 아니다.
- 실제 화면 확인: 512·1024는 뚜렷한 꽃 표현이 부족했고,768에는 큰꽃,512×768에는 가장자리 꽃·분홍 장식이 보였다. 생성된 뚜껑/제품 외곽/반사 잔상이 남아 광고 품질은 NOT_ASSESSED다. 실제 제품 사진·최대 메모리·CUDA/GCP·팀 인수는 별도다.
- B9 wheel 생성·격리 lazy import·pip check PASS. SHA256 `fa2629ec7fd4e4c8a3e8002450424350ee12777aeb359ac364a14535eb015450`. upstream VAE deprecation/casting 알림은 실제 실행에 남았으며 고정 환경의 파일/픽셀 검증은 통과했다.

---

아래는 2026-10-20 최초 업로드 당시 기록이다. 당시의 미구현 목록을 현재 상태로 해석하지 않는다.

검증일: 2026-10-20

## 최초 업로드 실행 결과

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
