# 릴레이 B6 검증 · 로컬 BiRefNetLite 실누끼

2026-10-22 · plan/models · 전체 제품 판정 **GAP/PARTIAL**

## 코드·고정 파일·회귀

- root 전체 회귀: `.venv/bin/python -m pytest -q -W error` → **339 passed in 4.30s**.
- 고정 모델: ZhengPeng7/BiRefNet_lite, revision `aa62cd87eafb9cc43056d08ef3615a14628b831d`. source/config/가중치 4개 실제 크기·SHA 검증 PASS. 가중치는 177634392 bytes. 개별 hash는 model_store.py에 고정했다.
- model_store는 명시적 prepare에서만 다운로드하고 partial/변조/extra code를 거절한다. handler.py는 제외한다. peer 검토의 HTTPException 오류 정제를 수리·회귀했다.
- adapter는 verified local files·safetensors만 로딩하고 명시한 장치를 사용한다. MPS fallback을 거절하며 요청 자산 검증→단일 실행→장치 동기화→결과 저장을 수행한다.
- optional backend 단위 검증에는 대체 의존성을 사용한다. 아래 실제 모델 실행과 구분한다.

## 실제 MPS·TCP 실행

- 환경: Python 3.11.15, torch 2.14.1, torchvision 0.29.1, transformers 5.20.2, timm 1.0.30, kornia 0.8.3, einops 0.8.2, numpy 2.4.6. Apple M4 Max·64GB·MPS. pip check·MPS tensor 확인 PASS.
- HF_HUB_OFFLINE=1·HF_HUB_DISABLE_TELEMETRY=1로 실제 runtime을 기동하고 별도 subprocess CLI `prepare --cutout-profile birefnet-lite`를 TCP로 실행했다.
- 입력은 직접 만든 합성 병 512×512 fixture다. 실제 forward 후 RGB 유지·alpha 0..255·RGBA/보호 마스크 저장을 확인했다. 모서리 alpha 0, 중앙 254. 픽셀 수: opaque 978 / transparent 194411 / partial 66755.
- job succeeded, candidate approved=false, needs_manual_check=true와 경고를 확인했다. 이미지가 생성돼도 사람 승인으로 바꾸지 않았다.
- 첫 job ledger_elapsed_ms=6897.11, CLI wall-clock=7.587s. 로딩·대기·파일 저장을 포함하며 GPU 순수 연산 시간·warm 성능·반복 성능을 뜻하지 않는다.
- 실제 cutout job의 파일 hash를 평가 manifest 입력과 대조했다. 합성 병 dev fixture 1건의 mask IoU=0.9692321320, boundary F1=0.7558205565(tolerance 2px), opaque_preserved=true. quality_approval은 NOT_ASSESSED이며 실사진 품질·평가셋 통과가 아니다.
- wheel build와 격리 환경의 lazy import PASS(torch 미수입 확인). SHA-256: `1932d8a7a0bc2b792caf49e839b0596dcf27fea83b48e2eef9fa6c9276b373f8`.
- 실제 실행에 timm의 torch.jit FutureWarning이 기록됐다. 실행 실패는 없었고 root가 마스크를 직접 확인했다. 이는 사람의 광고 품질 합격 판정이 아니다.
- 모델 파일과 합성 fixture·출력·raw evidence는 로컬 `outputs/b6-model-smoke` 등 Git 제외 경로에만 보관한다. 실사진 품질·실제 배경 모델·CUDA/GCP·팀 인수는 미검증이다.

## 시각 보고서와 다음 단위

- [목표/현재 흐름](../architecture/spec/image-runtime-flow.html): **브라우저 검증 PASS** (Task Space 142). PNG 2개 decode(552×2540, 1568×2972), 제목 7개·범례 5개·GAP 16행·우선순위 7행, 문서/화면 폭 1492px로 가로 넘침 없음. [최종 스크린샷](playwright-screenshots/image-runtime-flow-b6.png)을 직접 확인했다.
- [로컬 설치·GCP 이전](../../docs/localModelSetup.md): 고정 모델·선택 의존성·명시적 준비·MPS/CUDA 검증 구분.
- 다음 B7은 root가 선택한 로컬 실제 배경 모델 배치다. B6 게시 후 SDXL의 사용 조건·고정 revision·필요 파일·메모리·tokenizer·adapter 계약부터 확인한다. B6에서 SDXL을 설치/실행했다고 주장하지 않는다.
- 원본 TODO의 실제 alpha/RGBA 저장과 수동 경고 2개만 추가 체크했다(누적 27개). Git publication은 root가 별도로 검증한다.
