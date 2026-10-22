# 로컬 이미지 모델 준비와 GCP 이전

2026-10-22 · 대상: `plan/models` · 이미지 담당: pi

**로컬 Mac에서 실제 누끼 모델을 먼저 검증하고, 같은 모델·입출력 계약을 GCP로 옮긴다.** B6의 대상은 BiRefNetLite 누끼다. 기본 배경은 `bright-template/1`이며 B7의 SDXL 실제 MPS 512×512 경로도 검증했다. 기술 실행과 광고 품질 판정은 구분한다. B6는 실제 MPS 누끼와 TCP CLI 연결을 합성 fixture로 검증했다. 전체 회귀는 339 tests PASS이며 실사진 품질·CUDA 검증은 남아 있다. 설치, 파일 검증, 모델 추론, 제품 사진 품질 통과를 각각 기록한다.

## 1. 모델과 파일 고정

| 항목 | B6 설정 |
| --- | --- |
| 모델 저장소 | `ZhengPeng7/BiRefNet_lite` |
| 고정 revision | `aa62cd87eafb9cc43056d08ef3615a14628b831d` |
| 내부 cutout profile | `birefnet-lite` · 같은 고정 revision |
| 모델 카드 라이선스 | MIT 표기. 고정 모델 카드와 출처를 함께 보존 |
| 가중치 | `model.safetensors`, 공식 페이지 표시 약 178MB |
| 준비/검증 | `beauty_ai.model_store`의 서버 고정 allowlist·파일 크기·SHA-256 |
| 초기 추론 | 단일 이미지, FP32, 1024×1024 입력, eval/inference mode |
| 출력 | 원본 자산 크기의 RGBA와 binary preserve mask, 수동 확인 경고 |

모델 출처와 크기는 [공식 BiRefNetLite 파일 목록](https://huggingface.co/ZhengPeng7/BiRefNet_lite/tree/aa62cd87eafb9cc43056d08ef3615a14628b831d), 라이선스·입력 전처리는 [고정 모델 카드](https://huggingface.co/ZhengPeng7/BiRefNet_lite/blob/aa62cd87eafb9cc43056d08ef3615a14628b831d/README.md)를 따른다. 파일 크기는 설치 후 메모리 사용량이 아니다. 모델 카드의 예제를 그대로 실행하는 대신 이 저장소가 검토·고정한 소스와 가중치를 사용한다.

원격 사용자 코드를 무조건 신뢰하거나 `main`의 최신 파일을 API 요청마다 읽지 않는다. 준비 단계에서 고정된 source/config/weight 파일을 받아 개별 hash와 크기를 검사하고, 런타임은 검증된 로컬 파일만 읽는다. API 본문으로 임의 모델 저장소·파일 경로·실행 코드를 바꾸지 못한다.

## 2. 선택 의존성과 명시적인 준비

Python 환경은 프로젝트 전용 가상환경을 사용한다. 기본 래퍼와 내부 API 설치는 [README](../README.md)의 절차를 따른다. 모델 라이브러리는 기본 패키지와 분리된 `models` 선택 의존성으로 설치한다.

```bash
python -m pip install -e '.[inference,models]'
```

선택 의존성의 고정 버전은 [pyproject.toml](../pyproject.toml)에 있다. 검증한 버전은 Python 3.11.15, torch 2.14.1, torchvision 0.29.1, transformers 5.20.2, timm 1.0.30, kornia 0.8.3, einops 0.8.2, numpy 2.4.6이다. `pip check`와 MPS tensor 확인을 통과했다. 패키지 설치만으로 모델을 내려받거나 추론하지 않는다.

프로젝트 루트에서 모델 준비를 명시적으로 실행한다.

```bash
python -m beauty_ai.model_store prepare --root models
python -m beauty_ai.model_store verify --root models
```

`prepare`는 `<root>/birefnet-lite/aa62cd87eafb9cc43056d08ef3615a14628b831d/`에 `birefnet.py`, `BiRefNet_config.py`, `config.json`, `model.safetensors` 4개만 준비한다. `handler.py`는 실행·준비 대상에서 제외한다. 검증 디렉터리는 이 4개 파일만 허용하므로 별도 메모나 다른 모델 파일을 넣지 않는다. 고정 모델의 `bb_pretrained=false`로 별도 backbone 로딩·다운로드를 사용하지 않는다.

`prepare`만 네트워크 다운로드를 수행한다. `verify`는 이미 준비한 로컬 파일을 검사한다. 파일 크기·hash가 맞아야 준비 완료이며, 부분 다운로드·누락·변조 파일을 정상 모델로 사용하지 않는다. 준비 실패를 로컬 template 결과로 대체하지 않는다.

`models/`·실제 입력 `data/`·결과 `outputs/`는 Git 제외 경로다. 모델 가중치, 다운로드 캐시, 실사진, 생성 결과, 로컬 설정은 커밋하지 않는다. 모델·환경 버전과 검증 요약만 공개 문서에 남긴다.

## 3. 런타임 환경과 사용 순서

기존 프로젝트 UUID·내부 토큰·자산 저장 경로에 더해 모델 설정을 호스트 환경변수로 준다. `.env`는 자동으로 읽지 않는다.

```bash
export BEAUTY_CUTOUT_BACKEND=birefnet-lite
export BEAUTY_MODEL_ROOT="$PWD/models"
export BEAUTY_MODEL_DEVICE=mps
export PYTORCH_ENABLE_MPS_FALLBACK=0
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
python -m beauty_ai.inference_runtime
```

| 변수 | 의미 |
| --- | --- |
| `BEAUTY_CUTOUT_BACKEND=birefnet-lite` | 실제 누끼 adapter 선택. 기본 uploaded-alpha와 구분 |
| `BEAUTY_MODEL_ROOT` | 서버가 준비·검증할 모델 루트. 사용자 요청 경로가 아님 |
| `BEAUTY_MODEL_DEVICE=mps` | Mac에서 명시적으로 MPS 선택. `cpu`·`cuda`도 장치별 검증 필요 |
| `PYTORCH_ENABLE_MPS_FALLBACK=0` | 지원하지 않는 MPS 연산을 CPU로 자동 대체하지 않음 |
| `HF_HUB_OFFLINE=1` / `HF_HUB_DISABLE_TELEMETRY=1` | 준비 후 런타임을 offline으로 실행하고 telemetry를 끔 |

요청한 장치를 사용할 수 없거나 모델 로딩이 실패하면 오류로 남긴다. MPS 실패 뒤 CPU나 template으로 자동 전환하지 않는다. 실행 성공에 CPU가 사용됐는데 MPS 성공으로 표시하지 않는다. 운영체제의 GPU 지원 여부와 실제 모델 연산 지원 여부도 구분한다.

서버 기동 후 별도 터미널에서 사용자가 준비한 권리 확보 입력과 visual JSON으로 후보를 만든다.

```bash
python -m examples.run_image prepare data/product.png data/visual.json --cutout-profile birefnet-lite
```

`--cutout-profile`의 기본값은 `uploaded-alpha`다. 실제 모델을 쓰려면 `birefnet-lite`를 명시한다. CLI가 서버에 보내는 revision은 위 고정값이며 임의 revision을 받지 않는다. 후보 확인·승인·합성 명령은 [README2](../README2.md)의 절차를 따른다.

사용 순서는 **원본 RGB 자산 저장 → 고정 cutout profile로 실제 누끼 → 제품 후보 캔버스 준비 → 사람이 후보 확인 → 정확한 candidate hash 승인 → 배경/제품 합성 → 실제 레이어 파일 확인**이다. 누끼 결과가 생겨도 `needs_manual_check=true`와 `MODEL_SEGMENTATION_REQUIRES_REVIEW`를 유지한다. 자동 추론은 제품 식별·광고 품질·사람 승인을 대신하지 않는다.

alpha는 원본 RGB와 합쳐 RGBA로 저장한다. preserve mask는 alpha가 0보다 큰 영역을 보호한다. 빈 출력·잘못된 출력은 실패다. 투명·반사 용기, 작은 라벨, 여러 제품의 분리는 별도 평가 대상이다. 승인과 캐시·revision 경계는 기존 이미지 계약을 따른다.

## 4. 로컬 검증 기록

검증 기준 환경은 Apple Silicon Mac(M4 Max, 메모리 64GB)이다. 환경을 확인한 사실만으로 모델이 실행됐다고 기록하지 않는다.

| 확인 단계 | 필요한 근거 | 현재 상태 |
| --- | --- | --- |
| 소스·가중치 준비 | 고정 revision, allowlist 파일 hash·크기 검증 | PASS · 4개 파일, 가중치 177634392 bytes |
| 장치·라이브러리 | Python·torch·torchvision·모델 의존성 버전, 실제 선택 장치 | PASS · 위 고정 버전과 MPS |
| 실제 추론 | 모델 로딩·실제 MPS forward·출력 자산·시간 기록 | PASS · 합성 병 512×512, RGB 유지·alpha 0..255·RGBA/마스크 |
| 서버 연결 | TCP runtime·별도 prepare CLI·job·후보 파일 | PASS · approved=false, 수동 경고 유지. 사람 승인·품질은 별도 |
| 제품 품질 | 권리 확보 실사진·정답/사람 기준·반복 결과 | 미완료 |
| 실제 배경 모델 | 별도 모델·prompt/tokenizer·생성/재합성 검증 | B7 실제 MPS 512 fixture 기술 검증 PASS. 품질·안전 미평가 |

전체 회귀는 **339 tests PASS(4.30s, -W error)**다. optional backend 단위 테스트는 실제 모델 호출을 대체하지 않으며 실제 MPS smoke를 별도로 수행했다. 실행 중 timm의 torch.jit FutureWarning이 기록됐고 실패는 없었다. [B6 검증 기록](../fivecircles/test/relay-b6.md).

첫 실제 job의 `ledger_elapsed_ms=6897.11`, CLI wall-clock은 7.587s였다. 모델 로딩·대기·파일 작업을 포함한 단일 실행 기록이다. 같은 job 파일 hash와 연결한 dev fixture 1건의 IoU는 0.9692321320, boundary F1은 0.7558205565(tolerance 2px), opaque_preserved는 true다. 품질 승인은 NOT_ASSESSED로 유지했다. 합성 제품 fixture 한 장의 실제 forward는 모델 실행 증거다. 화장품 실사진 품질이나 전체 평가셋 통과의 증거는 아니다. [평가 도구](../beauty_ai/evaluation.py)는 원본과 결과 파일의 지표를 측정하지만 `quality_approval=NOT_ASSESSED`를 유지한다. job의 `ledger_elapsed_ms`는 접수→종료 wall-clock이며 모델 순수 연산 시간과 다르다. 시간의 측정 구간·동기화·장치와 반복 횟수를 함께 기록한다.

## 5. GCP로 이전할 때

로컬 검증이 끝나면 팀 GCP의 **us-east1** 환경으로 옮긴다. VM/GPU·CUDA·드라이버·PyTorch 조합과 비용·접근 범위는 인프라 담당과 확인한다. 이 문서는 VM 생성·서비스 배포·GCP 추론 완료의 증거가 아니다.

1. 같은 저장소 commit과 모델 revision·파일 hash를 사용한다. 모델 파일은 Git에 올리지 않고 대상 환경에서 명시적으로 준비·검증한다.
2. 대상 환경에 맞는 Python/PyTorch와 CUDA 조합을 기록한다. 로컬 MPS 패키지를 그대로 복사해 호환된다고 가정하지 않는다.
3. `BEAUTY_MODEL_DEVICE=cuda`로 명시하고 CUDA 가용성·모델 로딩·실제 forward를 별도로 검증한다. 실패를 CPU 결과로 숨기지 않는다.
4. 동일한 권리 확보 샘플과 profile·입출력 계약으로 누끼·승인·합성·결과 파일을 확인한다. MPS/CUDA의 픽셀 완전 일치와 동일 성능을 가정하지 않는다.
5. cold/warm 지연·반복 실패·VRAM·동시성·OOM·재시작·비용을 별도로 측정한다. Mac 통합 메모리와 CUDA VRAM 수치를 동일 지표로 비교하지 않는다.
6. 인증·프로젝트 경계·영속 저장·worker 잠금·현재 revision을 실제 배포 환경에서 확인한 뒤 API/렌더러 담당과 인수한다.

## 6. B7 SDXL 로컬 배경 준비

**상태: B7 실제 MPS 512×512 fixture 기술 실행 PASS, 최종 회귀 433 tests PASS.** 실제 광고 품질·생성 안전·실사진·CUDA는 미평가/미검증이다.

| 항목 | B7 고정 설정 |
| --- | --- |
| 모델 | `stabilityai/stable-diffusion-xl-base-1.0` |
| revision | `462165984030d82259a11f4367a4eed129e94a7b` |
| 내부 profile / pipeline revision | `sdxl-inpaint` / `sdxl-base-fp16-v1` |
| 실행 클래스 | Diffusers `StableDiffusionXLInpaintPipeline` |
| 모델 파일 | fp16 런타임 18개, 6,941,187,536 bytes(약 6.94GB) |
| 출처 파일 | 같은 revision의 모델 카드·LICENSE도 보존. 전체 allowlist는 20개 |
| 패키지 | 기존 models 선택 의존성 + background: diffusers 0.41.0, accelerate 1.15.0 |
| 고정 생성 | 각 축 512/768/1024, batch 1, steps 20, guidance 7.5, strength 0.99 |
| 정밀도 | MPS/CUDA fp16, CPU float32, VAE force_upcast 유지·tiling 사용 |
| scheduler / 스타일 / 배치 | 고정 EulerDiscreteScheduler / bright-ad v1 / product-right v1 |

개별 파일의 크기·SHA-256과 총량은 [SDXL manifest](modelManifests/sdxl-base-fp16.json)에 고정한다. [고정 공식 모델 목록](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/tree/462165984030d82259a11f4367a4eed129e94a7b)에서 필요한 fp16 파일만 선택한다. refiner나 전체 저장소를 다운로드하지 않는다. 다운로드 크기와 모델 실행 메모리는 다른 수치다.

### 선택 설치·파일 준비

```bash
python -m pip install -e '.[inference,models,background]'
python -m beauty_ai.sdxl_model_store prepare --root models
python -m beauty_ai.sdxl_model_store verify --root models
```

준비 위치는 `models/sdxl-base/462165984030d82259a11f4367a4eed129e94a7b/`다. 원자적으로 준비한 고정 중첩 경로·파일만 허용한다. 누락·변조·외부 symlink·다른 파일을 정상 모델로 취급하지 않는다. 요청 처리 중 네트워크 다운로드·임의 원격 코드 실행을 하지 않는다. 재실행은 검증된 기존 파일을 사용한다.

### 런타임과 요청

위 BiRefNet 설정에 다음 변수를 추가한 뒤 서버를 시작한다. `BEAUTY_MODEL_ROOT`·`BEAUTY_MODEL_DEVICE`는 두 모델이 공유한다. 모델 준비 뒤에는 `HF_HUB_OFFLINE=1`·telemetry off·MPS fallback 0을 유지한다.

```bash
export BEAUTY_BACKGROUND_BACKEND=sdxl-inpaint
python -m beauty_ai.inference_runtime
```

설정하지 않으면 기존 `bright-template`이 기본이다. 실제 배경 요청을 template으로 자동 대체하지 않는다. 기존 BiRefNet 또는 uploaded-alpha 경로로 제품 후보를 준비하고, 사람이 후보를 확인·승인한 뒤 배경을 생성한다.

`CANDIDATE_ID`는 준비 API가 반환한 값, `JOB_ID`는 한 번 생성해 보관한 UUID로 설정한다. 불확실한 응답 뒤에는 같은 job ID를 조회한다.

```bash
python -m examples.run_image render "$CANDIDATE_ID" --job-id "$JOB_ID" \
  --background-profile sdxl-inpaint --prompt examples/bright_flower_prompt.json
```

`select`도 같은 `--background-profile`·`--prompt`를 받는다. 현재 campaign·expected revision·copy revision은 기존 편집 계약을 따른다. 예제 [bright_flower_prompt.json](../examples/bright_flower_prompt.json)은 밝은 분홍·청록 광고 장면과 꽃 소품을 요청하는 호스트 작성 입력이다. LLM이 생성한 문구나 실제 이미지 생성 결과로 표시하지 않는다.

### 프롬프트·제품·여백 경계

- ImagePrompt는 필수다. positive와 negative 각각을 두 CLIP tokenizer로 검사한다. 특수 토큰을 포함해 각 검사에서 77개 이하여야 하며 넘으면 `INVALID_IMAGE_REQUEST`다. 본문을 로그에 노출하거나 몰래 자르지 않는다. 이미지 모듈은 프롬프트를 번역하거나 카피를 생성하지 않는다.
- 제품 승인·실제 파일·geometry를 재검증하고 승인 RGBA를 밝은 중립 캔버스에 올려 초기 이미지를 만든다. 제품 보호 마스크를 반전해 흰색을 재생성 영역으로 전달한다.
- 텍스트 Safe Zone은 mask에서 보호하고, VAE 복원 뒤에도 같은 중립색 사각형을 다시 채운다. 사각형이 비어 있다는 사실은 최종 문구 가독성·디자인·광고 품질을 보장하지 않는다.
- 생성씬 위에 승인 RGBA를 다시 합성하고 alpha=255 제품 픽셀 보존을 검사한 뒤 3개 레이어를 저장한다. 생성씬에 제품 잔상·중복 제품이 전혀 없다고 보장하지 않는다. 부분 투명 경계는 사람이 확인한다.
- 생성 출력의 배열 형상·유한 값·범위를 검사하고 원출력 전체가 정확히0이면 거절한다. 임의 밝기 임계값이나 품질 합격 기준은 만들지 않는다. 요청 seed로 CPU generator를 새로 만들지만 MPS/CUDA·라이브러리 변경 후 픽셀 완전 일치를 보장하지 않는다. 실제 선택 장치 완료를 동기화한 뒤 반환한다.
- 기본 SDXL pipeline에는 NSFW checker가 연결되지 않는다. `GENERATION_SAFETY_NOT_ASSESSED`·`MANUAL_QUALITY_REVIEW_REQUIRED` 경고를 유지하며 생성 성공을 안전성·품질 승인으로 표시하지 않는다.

### 모델 출처와 사용 조건

고정 모델은 **CreativeML Open RAIL++-M** 라이선스다. 준비된 모델 카드·라이선스와 출처를 보존한다. 배포 담당자는 사용 제한, 하위 사용자 고지·라이선스 제공과 필요한 수정 표시를 원문 기준으로 검토해야 한다. 모델 카드의 연구 목적·출력 한계도 함께 확인한다. 이 문서나 코드 실행은 광고 사용·재배포의 법적 승인을 뜻하지 않는다. [고정 라이선스](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/462165984030d82259a11f4367a4eed129e94a7b/LICENSE.md), [고정 모델 카드](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/462165984030d82259a11f4367a4eed129e94a7b/README.md).

B7 최종 회귀는 **433 tests PASS(9.39s)**이며 pip check·최종 wheel·격리 lazy import를 통과했다. 고정 SDXL 20개 파일 준비·SHA 검증 완료 후 실제 MPS 배경 생성을 별도로 검증했다. 처음 2회는 제거된 `pipeline.enable_vae_tiling()` 호출 때문에 실패했고, 지원되는 `pipeline.vae.enable_tiling()`로 수리하며 구 API를 허용했던 테스트도 보완했다. [B7 검증 기록](../fivecircles/test/relay-b7.md).

### 실제 실행 결과와 남은 측정

세 번째 실행은 CPU fallback 없는 MPS에서 합성 fixture 512×512를 처리했다. 시험용 확인만 수행한 후보로 실제 배경을 생성했고, 제품 opaque 770개 중 불일치 0·Safe Zone exact neutral·3개 레이어 저장을 확인했다. 이 확인은 실제 사람/상품 승인이나 광고 품질 인수가 아니다.

| 항목 | 실제 기록 | 해석 범위 |
| --- | --- | --- |
| cutout ledger | 2690.63ms | 접수→종료, 로딩/대기/파일 포함 |
| background ledger | 14700.575ms | 접수→종료, GPU 순수 연산 시간 아님 |
| render CLI | 15.343s | 별도 CLI wall-clock |
| MPS allocated | 7135957248 bytes | 작업 후 관찰, peak 아님 |
| MPS driver | 10240196608 bytes | 작업 후 관찰, CUDA VRAM 아님 |
| 품질/안전 | NOT_ASSESSED | 꽃 표현 부족·제품 잔상 관찰, NSFW checker 없음 |

root가 결과를 확인했을 때 꽃 표현이 부족하고 큰 cap/반사 형태의 제품 잔상이 보였다. 기술 실행 PASS를 광고 품질 합격으로 확대하지 않는다. 같은 모델의 로컬 cold/warm·크기별 반복 측정, 단계별 trace·메모리 측정, 응답 유실 후 job 조회·조정은 후속 구현 후보다. 실사진 품질·GCP CUDA·렌더러·팀 인수는 별도로 확인한다.

[이미지 TODO](imageTodo.md) · [이미지 실행 계약](../fivecircles/architecture/contract/image-runtime.md) · [목표/현재 흐름](../fivecircles/architecture/spec/image-runtime-flow.html)
