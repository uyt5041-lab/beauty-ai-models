# 로컬 이미지 모델 준비와 GCP 이전

2026-10-22 · 대상: `plan/models` · 이미지 담당: pi

**로컬 Mac에서 실제 누끼 모델을 먼저 검증하고, 같은 모델·입출력 계약을 GCP로 옮긴다.** B6의 대상은 BiRefNetLite 누끼다. 배경은 기존 `bright-template/1`이며 실제 배경 생성 모델은 다음 배치다. B6는 실제 MPS 누끼와 TCP CLI 연결을 합성 fixture로 검증했다. 전체 회귀는 339 tests PASS이며 실사진 품질·CUDA 검증은 남아 있다. 설치, 파일 검증, 모델 추론, 제품 사진 품질 통과를 각각 기록한다.

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
| 실제 배경 모델 | 별도 모델·prompt/tokenizer·생성/재합성 검증 | 미완료 |

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

## 6. 이후 배경 모델 범위

SDXL Base는 다음 배치의 검토 대상이며 B6에서 설치·추론했다고 표시하지 않는다. 공식 목록의 `sd_xl_base_1.0.safetensors` **단일 가중치는 약 6.94GB**다. 전체 저장소·파이프라인의 파일 크기와 실행 메모리는 이 값보다 크거나 다를 수 있다. [공식 SDXL 파일 목록](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/tree/main).

배경 모델을 연결할 때는 모델 사용 조건·고정 revision·필요 파일·CPU/MPS/CUDA 지원, prompt/tokenizer·해상도·seed·생성 설정을 별도 계약으로 고정한다. 실제 생성 배경에 승인 제품을 재합성하고 기존 3개 레이어·불투명 픽셀·현재 revision 검사를 유지한다. 밝은 로컬 template 결과를 SDXL 실행으로 기록하지 않는다.

[이미지 TODO](imageTodo.md) · [이미지 실행 계약](../fivecircles/architecture/contract/image-runtime.md) · [목표/현재 흐름](../fivecircles/architecture/spec/image-runtime-flow.html)
