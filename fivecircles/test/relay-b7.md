# 릴레이 B7 검증 · 로컬 SDXL 배경

2026-10-22 · plan/models · 전체 제품 판정 **GAP/PARTIAL**

## 코드와 회귀

- root 전체 회귀: `.venv/bin/python -m pytest -q -W error` → **433 passed in 9.39s**. 실제 라이브러리 API 수리 후 관련 adapter 44 tests PASS.
- pip check·wheel build·격리 lazy import PASS. wheel SHA-256: `614b5570fc177d22dde87e4fb10c60018a6119ebe7fce99883f03dca1b2d673c`.
- 모델 revision `462165984030d82259a11f4367a4eed129e94a7b`. 고정 fp16 runtime 18개, 6,941,187,536 bytes와 모델 카드·LICENSE를 allowlist로 관리한다. 모델 파일 다운로드·실제 forward 증거는 아래에 별도 기록한다.
- profile sdxl-inpaint / sdxl-base-fp16-v1, 밝은 광고 style/layout, 두 tokenizer의 각 positive/negative 77 token 한도. 기본 template과 실모델·프롬프트 입력을 분리한다.
- 검토 수리: 원출력 전체가 정확히0이면 자산 저장 전에 IMAGE_RESULT_INVALID. finite·shape·range 오류도 거절한다. 임의 품질 점수를 만들지 않는다.

## 실제 모델 실행

- 고정 SDXL 20개 파일 준비·SHA 검증 VERIFIED. BiRefNet도 실제 실행했다. MPS, cpu_fallback_enabled=false, 합성 fixture 512×512.
- 최초 2회는 구성 요소 로딩 뒤 MODEL_UNAVAILABLE. 원인은 Diffusers 0.41.0에서 제거된 pipeline.enable_vae_tiling()였다. pipeline.vae.enable_tiling()로 수리하고 구 API를 허용했던 대체 backend 테스트와 실제 설치 API 확인을 보완했다. 실패 2회 원본은 Git 제외 로컬에 보존했다.
- 세 번째 실제 TCP/CLI 실행 PASS. receipt의 model_invoked/cutout_model_invoked=true, approval=SYNTHETIC_TEST_CONFIRMATION_ONLY, quality_approval/actual_product_photo_quality/safety_assessment=NOT_ASSESSED, cuda_execution=NOT_RUN.
- opaque_pixels=770, opaque_mismatched_pixels=0, safe_zone_exact_neutral=true. scene_underlay/product_rgba/composed_preview 실제 파일과 hash를 확인했다.
- scene_underlay SHA-256 `d648e34969fa1071059275a8ff55c6d1baba87173503e4d5381623cb3b58ab26`; product_rgba `505dc63827703de7a7de9ba0c91a09d3e59b0fd8b6e5691aab0f28b93b8e49a0`; composed_preview `1926867f4c42ccea44024e3c47d01d8f3614d27fcce2ff69226e615d358a50bb`.
- cutout ledger_elapsed_ms=2690.63; background ledger_elapsed_ms=14700.575; render CLI=15.343s; 전체 smoke=18.653s. 서버 wall-clock이며 로딩·대기·저장을 포함한다. 순수 GPU 연산 시간·반복 성능으로 해석하지 않는다.
- 작업 후 MPS allocated=7135957248 / driver=10240196608 bytes. post_job_snapshot_not_peak이며 peak·CUDA VRAM 수치가 아니다.
- root 시각 관찰: 꽃 표현이 부족하고 큰 cap/반사 형태의 생성 제품 잔상이 보였다. 기술 실행 PASS이며 광고 품질 합격은 아니다. 수동 품질·안전 미평가·clean plate 미보장·부분 alpha 검수 경고를 유지했다.
- 근거는 로컬 Git 제외 `outputs/b7-model-smoke/receipt.json` 및 실제 이미지에 보관했다. 공개 Git에는 모델 가중치·출력 이미지를 넣지 않는다.

## 시각 검증과 후속

- [목표/현재 흐름](../architecture/spec/image-runtime-flow.html): **브라우저 검증 PASS** (Task Space 143). PNG 2개 decode(552×2540, 1568×3366), 제목 7개·범례 5개·GAP 16행·우선순위 7행, 문서/화면 폭 1492px로 가로 넘침 없음. [최종 스크린샷](playwright-screenshots/image-runtime-flow-b7.png)을 직접 확인했다.
- [설치·이전](../../docs/localModelSetup.md): 고정 모델·선택 의존성·명시적 prepare·두 tokenizer·품질/안전 미평가 경계.
- 다음 로컬 후보: cold/warm·크기별 반복 측정, 단계별 trace, 불확실 제출의 job ID/hash 조회·조정. 단순 경과 시간을 GPU 순수 시간으로 해석하지 않는다.
- 실사진 품질·CUDA/GCP·렌더러·사람 안전/품질·팀 인수는 별도다. 실제 모델 파일·생성 이미지는 공개 Git에서 제외한다. Git publication은 root가 별도로 검증한다.

원본 TODO 2-C의 실제 고정 배경 생성·제품/텍스트 보호 조건 2개만 추가 체크했다(누적 29개).
