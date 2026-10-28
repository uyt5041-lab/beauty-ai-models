# 화장품 광고 AI 래퍼 참고 구현

소상공인 화장품 광고 제작 MVP에 연결할 Python 참고 패키지입니다. **LLM·제품 분석·카피·한국어 이미지 프롬프트는 김다혜가, 이미지 생성·편집·누끼의 OpenAI API 래퍼와 최종 합성은 pi가 담당합니다.** 기본 모델은 `gpt-image-2.5-sunburst-2026-09-08`, 허용 대안은 `gpt-image-2-2026-04-21`입니다. B10은 오프라인 구현이었고 이후2026-10-22 사용자 승인으로 MidProject 키를 사용한 실제 생성·편집·누끼3회가 성공했습니다. 누끼 테두리와 제품 재구성 문제로 시각 품질은 `NOT_APPROVED`이며, 현재 팀 지급 키 여부·실제 청구액·가격 우위는 미확인입니다.

**2026-10-23 정정:** 현재 제품은 화장품이며, 꽃을 반드시 넣으라는 조건은 제거했습니다. 꽃 사용 자체를 금지하지 않으며 명시한 연출이나 참고 이미지에 따라 사용할 수 있습니다. B11은 화장품 프리셋3개와 제품 보존·문구 여백·누끼 지시를 버전2로 적용하는 오프라인 작업입니다. [프롬프트 안내](docs/cosmeticsPromptGuide.md)를 참고하세요.

B12는 Langfuse의 프리셋별 텍스트 버전을 읽어 최종 문장과 해시를 작업 전에 고정합니다. `production`을 변경해도 이미 제출한 작업의 프롬프트는 바뀌지 않습니다. [관리 방법](docs/langfusePromptManagement.md)과 [실제 비교·검증 기록](fivecircles/test/cosmetics-langfuse-b12.md)을 참고하세요.

현재 코드는 OpenAI 이미지 어댑터·영속 제출 기록과 내부 이미지 FastAPI·SQLite 작업 저장·자산·승인·revision/cache·평가 도구, 로컬 BiRefNetLite·SDXL 실험 경로를 포함합니다. 로컬 MPS 기술 검증은 보존하지만 자체 GPU 구축·모델 튜닝·GCP CUDA 이전은 현재 MVP의 필수 선행조건에서 제외합니다. 입력 검증·원본 제품 보존·최종 한글 합성·품질 확인은 외부 API를 사용해도 필요합니다.

확정 MVP는 **동일 제품 사진2~5장·선택 배경 참고0~5장으로 인스타그램4:5 광고1개**를 만드는 흐름입니다. 사전 조사→카피1개와 사용자 검토→이미지1개와 사용자 검토→최종 한글 합성·캡션·해시태그·다운로드가 목표입니다. 이 서비스 흐름 전체가 구현된 것은 아닙니다. 현재 코드와 목표의 차이·담당·완료 기준은 [SSOT 대조](docs/ssotAlignment.md), 실행 항목은 [이미지 TODO](docs/imageTodo.md)에 있습니다.

**백엔드 연결(2026-10-28 갱신):** [한국어 입력·Python 어댑터·리사이즈 사용법](docs/backendModelAdapter.md). `BackendImageAdapter.generate()`는 백엔드 입력을 기존 영속 래퍼로 연결합니다. `resize_ad_png()`는 원본을 보존하고 고품질 축소 PNG를 만듭니다. 서버 승인·HTTP 통합과 한글 최종 합성은 별도 남은 작업입니다.


**현재 팀 연결 규격 · 2026-10-28:** 김다혜의 `ProductAnalysisResult`와 한국어 `concept·prompt·mood`를 받습니다. `product_analysis`는 필수입니다. 분석 실패·비화장품은 이미지 호출 전에 차단합니다. 프리셋 선택이나 `product_description`·`creative_direction` 분리를 요구하지 않습니다. 새 공통 지시도 한국어입니다. [텍스트→이미지 계약](docs/textImageHandoff.md) · [실행 예제](examples/backend_handoff.py).

**내부 변환은 영어 번역이 아닙니다.** 한국어 `prompt` 원문과 개행을 유지한 채 `ImagePrompt` 형식으로 조립합니다. `positive`는 원문으로 시작하며 전달받은 `concept·mood`와 참고 조건을 뒤에 붙입니다. `negative=""`, `language="ko"`이고, 제공자 공통 지침도 한국어입니다. 별도 번역 호출이나 프리셋 선택은 없습니다.

현재 고정 프롬프트는 [`beauty_ai/korean_image_policy.py`](beauty_ai/korean_image_policy.py)의 `PRESERVE_KO`, `WATERMARK_POLICY_KO`, `CUTOUT_KO`입니다. `BackendImageAdapter`가 `ko-v1`을 선택합니다. 누끼는 `build_korean_cutout_request()`를 사용합니다. 이미 저장한 요청이나 기존 영어 Langfuse 버전은 덮어쓰지 않습니다. 정책 문장을 바꿀 때에는 버전도 올려야 합니다.

[서빙과 맞출 부분: MVP·고도화·현재 차이·담당별 요청](docs/backendAlignment.md)을 함께 확인하세요.

## 1. 쓰는 법

이미지 파트 전체 구현의 통합 기준 브랜치는 **`feature/image-model`**입니다. [폴더 트리·실행 그래프·빠른 시작](docs/imageArchitecture.md)에서 코드 구조와 실험 실행 순서를 확인하세요. 기존 `plan/models`는 계획·문서 이력을 보존합니다.

OpenAI 경로는 [B10 이미지 계약](fivecircles/architecture/contract/openai-images.md)과 [B11 프롬프트 계약](fivecircles/architecture/contract/cosmetics-prompts.md)을 따릅니다. 기본 설정은 **`live_calls=False`·이미지 예산 `0`**입니다. B11은 실제 API 추가 호출 없이 검증합니다. 로컬 모델 설명은 보존한 선택 경로의 사용법입니다.

Python 3.11 이상에서 프로젝트 루트 기준으로 설치합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q -W error
```

외부 통신·키 조회 없이 생성/편집/누끼 연결을 확인하는 예제는 다음과 같습니다. 항상 mock 응답과 합성 fixture를 사용하며 실제 AI 이미지나 품질 평가 결과가 아닙니다. 상세는 [README2](README2.md#openai-이미지-래퍼--실제-호출-없는-예제)를 따릅니다.

```bash
python -m beauty_ai.openai_images_demo --output outputs/openai-wrapper-demo
```

### 고정 프롬프트 위치와 사용법

공통 지시는 제품 형태·재질·라벨 보존, 새 광고 문구 생성 제한, 제품 사진과 참고 사진의 역할을 정합니다. 제품별 설명과 연출 입력을 이 지시에 붙여 최종 프롬프트를 만듭니다.

**워터마크 제외 기본값(2026-10-27):** 새 화장품 생성·편집·누끼 요청은 `watermark_policy="exclude-v1"`을 고정합니다. 사진 위의 워터마크는 제거하고 새 워터마크·서명·플랫폼 표시를 넣지 않도록 지시합니다. 실제 제품의 인쇄 로고·라벨은 보존합니다. 규칙 원문은 [prompt_policy.py](beauty_ai/prompt_policy.py)에 있으며, Langfuse 문장에도 작업 제출 전에 반영해 해시로 고정합니다. 과거 저장 요청은 소급 변경하지 않습니다. 제거 지시는 실제 결과의 제거 성공을 보장하지 않으므로 결과 검토가 필요합니다.

| 파일 | 용도 |
| --- | --- |
| [beauty_ai/cosmetics_prompts.py](beauty_ai/cosmetics_prompts.py) | 로컬 고정 지시와 조립 코드. `PRESETS`는 배경·조명, `_LAYOUTS`는 배치, `_PRESERVE`는 제품 보존, `CUTOUT_PROMPT_V2`는 누끼 지시입니다. |
| [examples/cosmetics_brief.json](examples/cosmetics_brief.json) | 제품별 입력 예시. 제품 설명·프리셋·배치·색·추가 연출을 바꿉니다. |
| [beauty_ai/langfuse_prompts.py](beauty_ai/langfuse_prompts.py) | Langfuse 프롬프트 템플릿과 버전 관리 연결입니다. |
| [vivid-pop-v2.json](prompts/cosmetics/vivid-pop-v2.json) · [vivid-pop-v3.json](prompts/cosmetics/vivid-pop-v3.json) | 밝은 컬러 프롬프트의 버전 저장본입니다. v2는 원본 재질, v3는 제품 폭·길이·캡 비율 보존을 보강했습니다. |

**아래는 과거 영어 프리셋 실험 방법입니다. 현재 팀 연결은 위 한국어 수신 계약을 사용합니다.** 실험 입력 JSON의 `product_description`에 확인한 제품 특징을 영어로 적고, `creative_direction`에 추가 연출을 적습니다. `preset_id`는 `clean_studio`, `premium_minimal`, `vivid_pop` 중 하나이며, `layout_id`는 `product_right`, `product_left`, `product_center_bottom` 중 하나입니다. `brand_colors`는 배경 강조색입니다. 원본 제품 색을 바꾸는 값이 아닙니다.

화장품 프리셋 3개의 로컬 프롬프트를 확인하려면 아래 명령을 사용합니다. 키 조회·네트워크·이미지 생성 없이 파일을 내보냅니다.

```bash
python -m beauty_ai.cosmetics_prompt_demo --output outputs/cosmetics-prompts
```

입력 JSON을 수정한 뒤 비교하려면 다음과 같이 실행합니다. 이 데모는 입력의 제품 설명과 연출을 바탕으로 **세 프리셋을 모두** 내보냅니다.

```bash
python -m beauty_ai.cosmetics_prompt_demo \
  --brief examples/cosmetics_brief.json \
  --output outputs/cosmetics-prompts
```

새 run 폴더의 각 프리셋 아래에서 `provider-prompt.txt`를 열면 제공자에게 전달할 전체 문장을 볼 수 있습니다. `brief.json`은 입력, `image-prompt.json`은 조립 결과, `request-template.json`은 요청 예시입니다. 요청 예시의 사진 자산은 가짜 값이므로 실제 제품 광고에는 업로드한 제품 사진과 `edit` 요청을 사용합니다.

**로컬 `cosmetics-v1`과 Langfuse의 숫자 버전은 별개입니다.** 위 데모는 로컬 빌더를 확인합니다. 실제 요청에 Langfuse 스냅샷(`managed_prompt`)이 있으면 고정된 `rendered_text`를 사용하므로 로컬 파일 수정만으로 그 요청이 바뀌지 않습니다. 공통 지시를 바꿀 때는 기존 버전을 덮어쓰지 말고 새 버전을 만든 뒤, 실험 준비 단계에서 해당 버전을 선택하고 호출 전 미리보기를 확인하세요. v3 파일이 있다는 이유만으로 모든 요청에 v3가 자동 적용되지는 않습니다.

자세한 조립 예시는 [프롬프트 사용 안내](docs/cosmeticsPromptGuide.md), 버전 관리는 [Langfuse 관리 안내](docs/langfusePromptManagement.md), 사진·프리셋·버전 선택과 호출 전 미리보기는 [실험 실행기 안내](docs/experimentRunner.md)를 따릅니다.

설정 예시는 [.env.example](.env.example)에 있습니다. 파일에는 비밀값을 넣지 마세요. 실제 값은 로컬 환경변수나 별도의 비밀 관리 수단으로 주입합니다. **패키지는 `.env`를 자동으로 읽지 않습니다.**

| 환경변수 | 용도 |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API 키 |
| `OPENAI_TEXT_MODEL` | 기획·카피 생성 모델 |
| `OPENAI_VISION_MODEL` | 제품 이미지 관찰 모델 |
| `OPENAI_REVIEW_MODEL` | 문맥 검수 모델 |
| `INFERENCE_BASE_URL` | 직접 기동한 내부 이미지 API 주소 |
| `INFERENCE_INTERNAL_TOKEN` | 내부 서버 인증 토큰 |
| `BEAUTY_PROJECT_ID` | 로컬 이미지 서버의 프로젝트 UUID |
| `BEAUTY_STORAGE_ROOT` | 로컬 자산·작업·승인·편집 DB 저장 경로 |
| `BEAUTY_OPENAI_IMAGE_LIVE_CALLS` | 기본 `false`. 승인된 실행 범위에서만 활성화하며 B11은 오프라인 검증 |
| `BEAUTY_IMAGE_BUDGET_USD` / `BEAUTY_IMAGE_RESERVATION_USD` | 서버 예약 한도·요청별 예약액. 기본 `0` / `1`, 실제 과금액이나 제공자 가격이 아님 |
| `BEAUTY_OPENAI_IMAGE_TIMEOUT_SECONDS` | OpenAI 이미지 요청 대기 제한. 기본 `180`초 |
| `BEAUTY_CUTOUT_BACKEND` / `BEAUTY_BACKGROUND_BACKEND` | 보존한 로컬 실험 backend. 기본 alpha/template과 구분 |
| `BEAUTY_MODEL_ROOT` / `BEAUTY_MODEL_DEVICE` | 검증 모델 루트·명시 장치 |

모델은 계정에서 사용할 수 있고 해당 입력·구조화 출력을 지원하는지 확인한 뒤 설정합니다. 동시성·시간 제한의 초기값은 `.env.example`과 [클라이언트 조립 예제](examples/wiring.py)를 참고하세요.

**과거 참고 연결:** `examples/wiring.py`와 `OpenAITextGateway`·`ReviewService`는 보존한 참고 코드입니다. 현재 팀의 텍스트 생성·검수는 김다혜 모듈을 사용하며 이미지 어댑터에서 이 클래스를 호출하지 않습니다. 실행 순서는 **검증된 제품 이미지 관찰 → 사용자가 제품 사실 확인 → 카피 후보 생성 → 후보 전체 검수 → 선택·수정 시 새 revision으로 재검수 → 이미지 작업 제출·상태 조회**입니다. `examples/wiring.py`는 이 연결을 위한 예시이며 `uvicorn`으로 실행하는 API 서버가 아닙니다.

내부 이미지 API는 `python -m pip install -e '.[inference]'`로 의존성을 설치한 뒤 호스트에서 `create_app(ledger, assets, credentials, processor=None)`로 조립합니다. `ledger`는 `JobLedger`, `assets`는 `LocalAssetStore`, `credentials`는 **서버 소유 32자 이상 Bearer 토큰 → 프로젝트 UUID** 매핑입니다. 사용자가 보낸 project 헤더를 권한으로 사용하지 않습니다. 토큰은 환경변수나 비밀 저장소에서 주입합니다.

`processor`를 제공하지 않으면 작업은 영속 `queued` 상태로 남습니다. 같은 ledger와 자산 저장소를 쓰는 worker에 동기 processor를 연결해야 실행됩니다. 이 factory를 생성하는 것만으로 모델 로딩이나 외부 포트 공개가 시작되지는 않습니다. worker는 POSIX 잠금을 지원하는 단일 호스트/공유 로컬 파일시스템을 전제로 합니다.

로컬 실행기는 위 환경변수를 설정한 뒤 `python -m beauty_ai.inference_runtime`으로 기동합니다. 기본 주소는 `127.0.0.1:8001`입니다. 기존 투명 PNG를 제품 후보로 준비하고 사람이 확인한 뒤 명시적으로 승인하면 밝은 템플릿 위에 합성합니다. `uploaded-alpha/1`은 실제 누끼 모델이 아니며 RGB·완전 불투명 입력은 지원하지 않습니다.

보존한 로컬 실험의 실제 누끼는 `.[inference,models]` 선택 의존성과 고정 모델 준비가 필요합니다. `BEAUTY_CUTOUT_BACKEND=birefnet-lite`·명시적 MPS 장치를 설정하고 `prepare --cutout-profile birefnet-lite`를 사용합니다. API에서 모델을 자동 다운로드하거나 CPU/template으로 자동 대체하지 않습니다. [로컬 모델 설치·GCP 이전](docs/localModelSetup.md)을 따르세요.

보존한 로컬 실험의 실제 배경은 `.[inference,models,background]`와 명시적 SDXL 파일 준비 후 `BEAUTY_BACKGROUND_BACKEND=sdxl-inpaint`로 선택합니다. 승인된 후보에 `render/select --background-profile sdxl-inpaint --prompt examples/bright_cosmetics_prompt.json`을 사용합니다. 두 tokenizer의 77 token 한도를 넘으면 거절하며 잘라서 실행하지 않습니다.

로컬 평가 도구는 `python -m beauty_ai.evaluation examples/evaluation_manifest.json --output outputs/evaluation-report.json`으로 실행합니다. 예제의 권리는 `unknown`이므로 이미지를 읽지 않고 미평가로 기록합니다. 실제 평가는 권리와 출처를 확인한 파일을 manifest 폴더 아래에 두고, 비공개 `data/`·`outputs/`에서 실행하세요. 기존 결과 파일은 덮어쓰지 않습니다.

B10은 **806 tests PASS**이며 로컬 beautyAi에서도 신규182개와 mock CLI를 확인했습니다. **B10 배치 당시** 실제 OpenAI 호출은0회였으며, 이후 승인된3회 실행과 구분합니다. [B10 검증](fivecircles/test/relay-b10.md).

단계별 실행 명령은 [README2.md](README2.md), 기존 로컬 검증은 [B9 기록](fivecircles/test/relay-b9.md)을 참고하세요. **B9 당시 624 tests PASS + 실제 MPS 6 jobs/실행 관찰 PASS** 기록입니다. B10의 테스트 총수나 실제 OpenAI 호출 검증을 뜻하지 않습니다. 같은512 입력·seed의 두 번 실행에서 출력 hash가 같았습니다. B8 실제 TCP 응답 유실 조정 근거도 보존했습니다. [TEST_REPORT.md](TEST_REPORT.md)는 누적 검증 기록입니다.

## 2. 기능

| 구현 요소 | 현재 하는 일 |
| --- | --- |
| `OpenAIImagesAdapter` | 생성·편집·생성형 누끼 요청 변환, 고정 모델·PNG 1장·base64 응답 검증. 실제 호출은 기본 차단 |
| `ExternalImageProcessor` / `ProviderAttemptStore` | 자산 검증, 작업당 1회 POST를 위한 영속 예약·제출 기록, 결과 저장과 요청 ID·nullable usage 관찰 |
| `openai_images_demo` | 네트워크·키 없는 mock 전용 예제. 합성 fixture만 생성 |
| 화장품 프롬프트 빌더 / `cosmetics_prompt_demo` | 영어 입력을 프리셋3개·제품 보존·문구 여백 조건으로 조립하고 버전2 요청/프롬프트를 준비. LLM 호출 없이 예제 내보내기 |
| `OpenAITextGateway` (과거 참고, 서비스 연결에서 미사용) | 제품 이미지 관찰, 광고 기획·카피 후보 생성, 문맥 검수, 수정안 생성 |
| 구조화 계약 | Pydantic 입력·출력 검증과 [JSON Schema](schemas/) 제공 |
| `ReviewService` (과거 참고, 서비스 연결에서 미사용) | 전체 카피의 규칙 검사와 LLM 검사 병합, 검토된 규칙집 확인, revision·해시로 검수 대상 식별 |
| `HTTPImageGateway` | 기존 제출·조회·폴링과 submit_and_wait 조정. PUT 1회·동일 ID 조회·단일 deadline, ID/hash 검사와 접수 미확인·대기 종료 구분 |
| `inference_api.create_app` | Bearer 인증과 프로젝트 범위의 job PUT/GET·자산 업로드/조회·인증된 health 응답 |
| `JobLedger` | SQLite WAL 기반 atomic ID/hash 등록, owner·terminal 보호, durable queued 조회, 접수→종료 wall-clock 기록 |
| `SingleHostWorker` | 프로세스 간 POSIX 실행 잠금, 종료 확인 후 interrupted 복구, 결과 파일 검증 후 success |
| `LocalAssetStore` | 파일 형식·크기·회전·색 공간 정규화, 프로젝트별 파일/SQLite 저장과 hash 검증 |
| `image_preparation.py` | 제품 후보 캔버스·보호 마스크 생성, Safe Zone 검사와 불투명 픽셀 보존 검사 |
| `image_integrity.py` | 보호 마스크를 인페인트 마스크로 반전하고 승인 RGBA를 장면 위에 재합성 |
| `ProductApprovalStore` | 후보 snapshot·project·파일·형상 검증, 명시적 확인과 정확한 hash로 승인 저장 |
| `model_store` / `BiRefNetCutoutProcessor` | 고정 4개 파일 준비·hash 검증, 명시 장치의 실제 누끼와 RGBA·마스크 저장. MPS 합성 fixture 검증 |
| `sdxl_model_store` / `SDXLBackgroundProcessor` | 고정 fp16 파일·두 tokenizer 검사, 실제 MPS inpaint·여백 복원·승인 제품 재합성. 512/768/1024/512×768 fixture 기술 검증 |
| `LocalTemplateProcessor` | 서버 고정 profile로 기존 alpha 처리·밝은 템플릿 합성, 3개 레이어 저장과 보존 검사 |
| `inference_runtime` / CLI | 로컬 서버 조립, 준비→확인→승인→합성→조회·다운로드 및 이미지 선택·결과 연결 명령 |
| `ImagePrompt` | 텍스트 담당이 만든 프롬프트·버전·revision 수신, 문자 수 검증, 기존 요청 hash 호환 |
| `ImageEditStore` | 현재 selection의 revision CAS, 프로젝트별 검증된 성공 결과 캐시, 늦은 결과 연결 거절 |
| `evaluation.py` / `evaluation_metrics.py` | 권리 선언·파일 범위를 검사한 로컬 manifest 평가, 불투명 픽셀 보존·mask IoU·경계 지표와 파일 hash 기록 |
| `execution_trace` / `get-execution` | 작업 ID별 단계·모델 revision·seed·환경·장치 동기화·작업 후 메모리 관찰. 미기록은 NOT_RECORDED/null |
| 공통 오류·메타데이터 | 거절·미완료·타임아웃·프로토콜 오류 분리, 요청 ID·사용량·소요 시간 기록 |

검수 상태는 `no_flags`·`warnings`·`blocked`·`unavailable`입니다. `no_flags`는 설정된 자동 검사에서 표시된 이슈가 없다는 뜻이며 법적 승인이나 광고 효과의 보장이 아닙니다. 규칙집이 준비되지 않았거나 검수가 실패하면 `unavailable`로 남깁니다.

## 3. 아키텍처

목표는 `다혜의 제품 정보·프롬프트 → pi의 OpenAI 이미지 래퍼 → 결과 검증·저장 → 사용자 검토·최종 렌더`입니다. `api_image` 요청은 별도 processor로 연결됩니다. 내부 자산·job 기반은 재사용하지만 새 결과는 **PNG 1장**이므로 기존 세 레이어 결과의 승인·캐시를 자동 통과하지 않습니다. API 누끼는 생성형 편집이며 제품 픽셀·라벨 보존을 보장하지 않습니다. 아래 연결 코드의 존재는 실제 계정 호출·사용자 인수 완료를 뜻하지 않습니다.

아래는 보존한 내부 서버·참고 코드 구조입니다. 현재 서비스 어댑터 흐름은 `ProductAnalysisResult → 실패/비화장품 차단 → 한국어 프롬프트·원본 사진 → OpenAI 이미지 래퍼 → 원본 보존·resize_ad_png`입니다.

```text
[호스트 구현 필요: UI / 사용자 인증 / CampaignService / SSE / Export]
   ├─ OpenAITextGateway ── OpenAI API
   ├─ ReviewService ── 규칙 검사 + TextGateway 문맥 검사
   └─ HTTPImageGateway ── 내부 FastAPI 앱
                           ├─ Bearer → project / LocalAssetStore
                           └─ JobLedger (SQLite WAL)
                                └─ SingleHostWorker (POSIX 실행 잠금)
                                     └─ 주입한 processor
                                          ├─ ExternalImageProcessor → ProviderAttemptStore → OpenAIImagesAdapter
                                          │   (generate / edit / cutout, 기본 live 차단)
                                          ├─ LocalTemplateProcessor (기존 alpha / 밝은 템플릿)
                                          ├─ BiRefNetCutoutProcessor (고정 local files / 실제 MPS 누끼)
                                          └─ SDXLBackgroundProcessor (고정 local fp16 / 실제 MPS 배경)
                           ├─ ProductApprovalStore (정확한 후보 snapshot 승인)
                           └─ ImageEditStore (현재 revision / project cache / result attach)

공통 계약: contracts.py / schemas/    이미지 후보: image_preparation.py
제품 재합성: image_integrity.py      결과 파일 검증 → terminal commit
별도 로컬 평가: evaluation.py → mask / pixel 측정 (모델 호출·품질 승인 없음)
```

내부 API는 `PUT/GET /internal/v1/jobs/{job_id}`, `POST /internal/v1/assets`, `GET /internal/v1/assets/{asset_id}`를 제공합니다. 동일 프로젝트·작업 ID·입력 hash는 기존 작업을 반환하고, 같은 ID·다른 hash는 `409`입니다. 다른 프로젝트의 job·자산은 미존재와 같이 처리합니다. 접수는 DB commit 이후 응답하며, worker는 DB에서 queued 작업을 다시 찾습니다.

`GET /internal/v1/jobs/{job_id}/provider`는 해당 프로젝트의 제출 기록만 조회합니다. 작업당 POST는 1회로 제한하며 timeout·응답 유실·5xx는 `PROVIDER_SUBMISSION_UNKNOWN`으로 남깁니다. 같은 작업이나 재시작에서 자동 재제출하지 않습니다. 내부 멱등성을 제공자의 exactly-once 처리로 확대하지 않습니다. 제출 후 예약은 실패·저장 실패·과금 미확인에도 유지하며 실제 청구액으로 표시하지 않습니다. 제공자 작업 조회·자동 정산 기능은 없습니다. 관찰되지 않은 `usage`는 `null`입니다.

`HTTPImageGateway.submit_and_wait`는 제출과 조회를 하나의 monotonic deadline으로 묶습니다. PUT은 한 번만 보내며 응답이 유실되면 같은 ID로 GET합니다. 404만으로 새 작업을 만들지 않습니다. 유효 job을 끝까지 보지 못하면 `SubmissionUncertain`, 접수는 확인했지만 완료를 확인하지 못하면 `JobStillPending`입니다. 두 상태 모두 서버 취소·실패를 뜻하지 않습니다. 서버 오류 코드는 제한한 형식·상태만 허용하며 임의 원문은 프로토콜 오류로 거절합니다. 기존 `submit/get/wait`는 그대로 사용할 수 있습니다. CLI prepare/render도 이 조정 메서드를 사용합니다.

`POST /internal/v1/products/prepare`, `GET /internal/v1/products/{candidate_id}`, `PUT /internal/v1/products/{candidate_id}/approval`로 후보 준비·조회·승인을 수행합니다. 준비는 항상 미승인 상태이며 확인값 `true`와 정확한 candidate hash가 필요합니다. 자산·구도가 바뀌면 새 승인이 필요합니다. 저장된 승인은 호스트의 사용자 확인 전달 기록이며 사람 신원·최종 품질 인증이나 현재 편집 선택을 뜻하지 않습니다.

`GET /internal/v1/health`는 인증 후 해당 앱의 worker 구성·실행 상태·안전한 fatal code를 알려줍니다. worker fatal은 `503`이며 `model_readiness`는 `not_assessed`입니다. health 성공은 모델 준비나 이미지 품질 증거가 아닙니다.

`GET /internal/v1/jobs/{job_id}/timing`과 `examples.run_image get-timing`은 해당 프로젝트 작업의 접수→종료 경과 시간을 반환합니다. 대기·처리·저장을 포함한 서버 wall-clock이며 GPU 연산 시간이 아닙니다. 아직 실행 중이거나 시계가 역행한 경우 시간 값은 `None`입니다.

`GET /internal/v1/jobs/{job_id}/execution`과 CLI `get-execution`은 기록된 worker 단계·모델 재사용·환경·메모리 관찰을 조회합니다. terminal과 관찰은 같은 DB transaction에 저장하며 프로젝트·요청 hash·상태를 대조합니다. 기존 작업에 관찰이 없으면 `NOT_RECORDED`와 `null`입니다. 단계는 monotonic wall-clock이며 일부 전처리가 inference에 중첩됩니다. 단순 합산하거나 순수 GPU 시간으로 부르지 않습니다. 메모리는 작업 후 process snapshot이며 peak·요청별 증가량이 아닙니다.

평가 manifest는 dataset/version·dev/holdout·제품 유형·권리와 사람 판정의 선언을 기록합니다. 마스크 정답이 없으면 지표는 `None`, 권리가 미확인이면 파일 측정을 건너뜁니다. 출력의 `model_invoked`는 `false`, `quality_approval`은 `NOT_ASSESSED`이며 선언된 사람 판정은 검증된 신원이 아닙니다. 입력 경로는 manifest 폴더 내부로 제한하고 URL·외부 경로는 받지 않습니다.

실행 잠금은 한 호스트·같은 ledger에 연결한 동기 processor를 직렬화합니다. 실행 중 stop timeout은 취소가 아니며 잠금을 유지합니다. 새 worker는 이전 프로세스 종료로 잠금을 획득한 뒤 running 작업을 interrupted로 수습합니다. 다중 VM 조정과 분리된 GPU subprocess 관리는 포함하지 않습니다.

`PUT/GET /internal/v1/edits/{campaign_id}`로 현재 이미지 선택을 저장·조회하고, `PUT /internal/v1/edits/{campaign_id}/result`로 완료 job을 연결합니다. 현재 revision과 요청 hash가 맞아야 연결되며, 실제 파일을 확인한 성공 결과만 프로젝트 캐시에서 재사용합니다. 문구 revision만 바뀌면 같은 이미지 결과를 재사용하고 visual·seed·profile·자산·프롬프트가 바뀌면 다른 key입니다. 캐시 miss가 자동으로 새 작업을 실행하지는 않습니다.

검수 snapshot은 제품·카피·옵션·규칙·검수 모델·프롬프트에 묶입니다. `BackgroundRequest.image_prompt`가 있으면 텍스트·버전·revision을 이미지 hash에 포함하며, 없으면 기존 body/hash를 유지합니다. 영어 프롬프트 생성·언어 판별은 하지 않습니다. SDXL 경로는 positive/negative를 두 tokenizer에서 특수 토큰 포함 각각 77개 이하로 검사합니다. 로컬 bright-template은 프롬프트를 해석하지 않으므로 제공된 image_prompt를 거절합니다. [현재 흐름](fivecircles/architecture/spec/image-runtime-flow.html)과 [설계](DESIGN.md)를 참고하세요.

## 4. 미완성으로서 보완해야 할 점

- **새 연결의 실제 인수:** 분석 수신·실패 차단·공통 한국어 정책은 로컬 모의 시험 대상입니다. 팀 서버의 요청 매핑·분석 결과와 사진 결속·승인·예산 연결, 새 정책의 실사진 품질 평가와 최종 한글 합성은 별도입니다.

- **서비스 통합:** 사용자 로그인·CampaignService·SSE·내보내기·렌더러와 실제 UI 연결이 필요합니다. 내부 API 인증·job 복구·이미지 selection revision·프로젝트 캐시는 로컬 구현했습니다.
- **OpenAI 실제 검증:** 승인된3회 기술 실행과 실제 서비스 인수를 구분합니다. 팀 키·잔액·예산을 확인하고 같은 실제 제품 사진으로 새 프롬프트의 품질·처리 시간·성공한 최종 광고당 비용을 측정해야 합니다. seed·마스크 옵션은 현재 계약에 없고 생성형 누끼·복수 참조의 제품 보존 성능은 미검증입니다. 로컬 MPS 6건은 기존 실험 증거이며 외부 API 검증이 아닙니다. GPU 메모리/OOM·CUDA 검증은 자체 호스팅을 다시 선택할 때만 적용합니다.
- **제품 승인·편집:** 정확한 snapshot 승인과 늦은 result attach 차단을 구현했습니다. 실제 사용자 신원·승인 UI·편집 서비스와 최종 품질 승인은 별도로 연결해야 합니다.
- **검수 데이터:** 출처·버전·검토 이력이 있는 규칙집, 제품 사실 확인, 카피 수정 후 재검수·DB revision 경쟁 검사가 필요합니다.
- **품질·실환경:** 기존 로컬 실험에는 제품 잔상이, 후속 OpenAI 누끼에는 테두리와 제품 재구성 문제가 관찰돼 광고 품질을 승인하지 않았습니다. 꽃 장식은 기본 프리셋의 필수 조건이 아닙니다. 측정 도구는 구현했지만 실제 고정 평가셋·권리 근거·판정 기준을 준비하고 라벨·경계·투명/반사·그림자·여백·최종 텍스트 합성, 실사진 모델 품질·반복 지연·외부 API 사용량·비용을 평가해야 합니다. 불투명 픽셀 검사만으로 광고 품질을 승인하지 않습니다.
- **운영:** 실제 429/쿼터·권한·응답 유실·청구 정산과 사용자 대기 UI를 검증해야 합니다. OpenAI 응답은 base64 PNG만 수신하며 URL-only 응답은 거절합니다. 서버 예약 예산은 LLM과 공유하는 팀 계정의 실제 잔액을 알거나 제한하지 못합니다. 현재 잔액·실제 청구는 미확인입니다.

## 5. 하지 말 것

- 다혜님에게 프리셋 선택이나 영어 변환을 요구하지 않습니다. 실패 기본 분석값을 성공으로 쓰거나, 라벨 OCR을 확인된 사실로 복사하거나, VLM용 축소본으로 원본을 덮어쓰지 않습니다.

- B11 오프라인 작업에서 실제 이미지 API를 추가 호출하거나 live 설정·예산을 올리지 않습니다. 실제 실행은 승인된 범위와 계정·운영 한도에 맞춰 수행합니다. mock 성공을 모델 품질·비용·실제 호출 성공으로 설명하지 않습니다.
- 제품에 원래 인쇄된 그림·라벨·로고는 보존하며 배경색을 제품색으로 적용하지 않습니다.
- 생성형 누끼 결과를 원본 픽셀 보존·기존 제품 레이어 승인으로 처리하거나 단일 PNG 결과를 세 레이어 캐시에 자동 연결하지 않습니다.
- API 키, 실제 `.env`, 로컬 설정·DB·로그·테스트 캐시, 업로드·생성 이미지, 모델 가중치를 커밋하지 않습니다. `.env.example`에는 빈 값이나 명백한 예시만 둡니다.
- VLM이 읽은 라벨·효능 문구를 확인된 제품 사실이나 기능성 분류로 자동 승격하지 않습니다. 업로드 이미지·사용자 텍스트를 시스템 지시로 취급하지 않습니다.
- 검수 실패·규칙집 부재·오래된 snapshot을 통과로 처리하지 않습니다. LLM 결과로 규칙 검출을 지우거나 `no_flags`를 법적 적합 판정으로 설명하지 않습니다.
- 사용자 제공 URL을 검증 없이 서버에서 가져오지 않습니다. 이미지 디코딩·용량·픽셀 수·메타데이터와 자산 접근 권한을 별도로 검증합니다.
- 제출 응답 유실이나 폴링 시간 초과 뒤 새 작업 ID로 무조건 재생성하지 않습니다. 기존 ID를 조회·복구합니다. 클라이언트 semaphore를 서버 전체 GPU 동시성이나 영속 멱등성으로 간주하지 않습니다.
- 평가 지표·불투명 픽셀 일치를 품질 합격으로 바꾸거나 wall-clock 경과를 GPU 연산 시간·VRAM·비용으로 설명하지 않습니다. 실제 평가 이미지·권리 자료·산출 보고서는 공개 Git에 넣지 않습니다.
- SDXL 실행 성공을 안전 검사 완료로 표시하지 않습니다. 기본 경로에는 NSFW checker가 없고 `GENERATION_SAFETY_NOT_ASSESSED`·수동 품질 경고를 유지합니다.
- 로컬 템플릿을 AI 모델 결과로 표시하거나 `TEMPLATE_NOT_AI_GENERATION`·`MANUAL_QUALITY_REVIEW_REQUIRED` 경고를 숨기지 않습니다.
- SDK 재시도 위에 무제한 재시도를 덧씌우지 않습니다. 카피 수정 때문에 배경을 재생성하거나, 참고 문서의 속도·메모리·보존율 수치를 실측 결과처럼 쓰지 않습니다.
- 공개 저장소는 `plan/models`에서 모델 코드를, `박지수_협업일지`에서 날짜별 작업 기록을 관리합니다. 협업일지는 PR·병합 없이 별도로 관리합니다. 자세한 운영 방식은 [README2.md](README2.md)를 따릅니다.

**무드 출처:** 사용자 입력을 반영한 다혜님의 `concept·prompt·mood`를 받습니다. 이미지 쪽은 전달된 광고 무드를 그대로 사용하며 원본 사용자 무드를 별도로 요구하지 않습니다. `ProductAnalysis.mood`는 제품 관찰 참고값이며 광고 무드를 덮어쓰거나 빈 값을 자동 보완하지 않습니다.
