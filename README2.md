# 추가 사용 예제와 기존 안내

## 현재 공개 저장소 운영

2026-10-22 수신한 확정 MVP와의 대조는 [ssotAlignment.md](docs/ssotAlignment.md)를 따릅니다. 아래 제공받은 README 원문은 과거 설명입니다. 현재 MVP는 카피·이미지·최종 광고 각각1개, 최종4:5이며 부분 수정 UI·복수 시안·다른 비율/플랫폼은 고도화입니다. 기존 내부 revision/hash/cache 보호는 계속 사용합니다.

- 저장소: [uyt5041-lab/beauty-ai-models](https://github.com/uyt5041-lab/beauty-ai-models)
- 기본 브랜치 `plan/models`: 모델 참고 코드·설명·테스트를 관리합니다.
- 별도 브랜치 `박지수_협업일지`: 날짜별 Markdown·HTML 기록을 관리하며 PR·코드 브랜치 병합 없이 유지합니다.
- 원본 두 브랜치의 Git 이력을 보존해 이전했습니다. 아래 원본 설명과 `CONTRIBUTING.md`의 팀 규칙은 출처 기록입니다. 원본의 `dev`·PM·PR 절차를 이 공개 저장소의 필수 운영 규칙으로 적용하지 않습니다.
- 실제 비밀값·생성 이미지·로컬 설정·가중치·캐시는 기존 `.gitignore` 기준으로 제외합니다.
- 초기 이전은 기존 원격 브랜치만 포함했고, 이후 `00e455e`에서 `docs/llmTodo.md`, `docs/imageTodo.md`, `docs/modelIntegrationReview.md`를 추가했습니다.

루트 [README.md](README.md)는 사용법·기능·아키텍처·보완점·하지 말 것으로 제한했습니다.
추가 호출 예제와 상세 설계는 아래 자료를 참고하세요.

- [DESIGN.md](DESIGN.md): 제안 API, 상태 관리, 이미지 계약, 구현 순서. 서버 구현 완료를 뜻하지 않습니다.
- [examples/wiring.py](examples/wiring.py): 환경변수로 클라이언트를 조립하는 예제. 실행 가능한 FastAPI 앱이 아닙니다.
- [TEST_REPORT.md](TEST_REPORT.md): 이 브랜치에서 다시 실행한 검증과 미검증 범위.
- [원본 검증 기록](docs/TEST_REPORT.original.md): 제공받은 ZIP의 과거 검증 기록.
- [CONTRIBUTING.md](CONTRIBUTING.md): 팀의 브랜치·검토·통합 규칙.

## 대상 저장소의 기존 README 원문

# project-it-tem

## 제공받은 래퍼의 기존 README 원문

# 화장품 광고 MVP: 텍스트 / 이미지 API 래퍼 초안

업로드한 `통합본`의 OpenAI 텍스트·VLM / FastAPI / GCP L4 이미지 inference 분리를 유지한 **설계 및 참고 구현**입니다. 기존 저장소나 Notion을 수정하지 않았습니다.

## 포함 범위와 제외 범위

포함:
- `OpenAITextGateway`: 관찰 분석, 기획안·카피 생성, 카피 검수, 수정안 생성. Responses API + 구조화 출력 호출 코드.
- `HTTPImageGateway`: 우리가 구현할 private inference API의 작업 제출·조회·대기 클라이언트.
- Pydantic 입력/출력 계약, 모델 오류 변환, 관측 메타데이터.
- `ReviewService`: 규칙 검수와 모델 검수 결합, 검수 불능/출처 오류 처리, 검수 스냅샷 해시.
- 마스크 방향 변환과 원본 제품 레이어 재합성 함수.
- 네트워크 모킹 / 모델 SDK 응답 대체 객체를 이용한 단위 테스트 36개.

**포함하지 않은 것:** SDXL/BiRefNet 실모델 실행, GPU inference 서버, FastAPI 라우터, 로그인·프로젝트 권한, SQLite 저장소/작업 영속화, 실제 법규 사전, 예산 원장, SSE 서버, React 렌더러, 배포 구성. 이들은 `DESIGN.md`의 제안 계약대로 연결할 다음 구현 항목입니다. 특히 private inference URL은 이미 존재하는 외부 서비스 API가 아닙니다.

실제 API 요청이나 실제 규제 판정을 수행한 결과물이 아닙니다. `no_flags`는 설정된 자동 검사에서 표시된 문제가 없다는 뜻이지 적법성 승인이 아닙니다.

## 구성

```text
beauty_ai/
  contracts.py         입력/출력 데이터 구조와 해시
  ports.py             TextGateway 인터페이스
  text_gateway.py      OpenAI 어댑터, 네 가지 작업
  review_service.py    규칙 + LLM 검수, 현재 버전 확인
  image_gateway.py     private inference HTTP 클라이언트
  image_integrity.py   마스크 변환과 제품 재합성
  errors.py            애플리케이션 오류 코드
examples/wiring.py     FastAPI lifespan 등에 연결할 클라이언트 생성 예
schemas/              Pydantic에서 추출한 JSON Schema
DESIGN.md              엔드포인트, 계층, 비동기 작업, 구현 순서
TEST_REPORT.md         실행한 검증과 미검증 항목
```

## 설치와 테스트

Python 3.11 이상:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
```

`.env.example`의 값은 설정 계약입니다. 이 패키지가 `.env` 파일을 자동으로 읽지는 않습니다. 앱의 설정 로더를 사용하거나 환경변수를 직접 export하세요.

모델 ID는 계정에서 사용 가능한 모델 중 Responses 구조화 출력을 지원하는 것으로 지정해야 합니다. VLM 모델은 이미지 입력도 지원해야 합니다. 문서가 경량 모델 사용만 정했으므로 최신 모델명/가격/지원 여부를 임의로 고정하지 않았습니다. 검증된 모델 스냅샷과 SDK 버전은 실제 연결 테스트 후 팀 환경에서 잠그세요. `pyproject.toml`의 범위는 lockfile이 아닙니다.

```bash
export OPENAI_API_KEY='...'
export OPENAI_TEXT_MODEL='계정에서-검증한-모델-ID'
export OPENAI_VISION_MODEL='이미지입력과-구조화출력-지원-모델-ID'
export OPENAI_REVIEW_MODEL='계정에서-검증한-검수-모델-ID'
export INFERENCE_BASE_URL='http://inference:8001'
export INFERENCE_INTERNAL_TOKEN='...'
```

## 호출 예

```python
# FastAPI lifespan 등에서 한 번 생성하고 종료 시 닫습니다.
from beauty_ai.text_gateway import OpenAITextGateway, make_openai_client

sdk = make_openai_client(api_key)
text = OpenAITextGateway(
    sdk,
    text_model=text_model,
    vision_model=vision_model,
    review_model=review_model,
)

# facts / options는 서비스가 DB에서 읽은 확인된 입력입니다.
draft = await text.draft_campaign(facts, options, count=3)
# 후보마다 검수한 뒤 UI에 제시하고 사용자가 선택합니다.
# draft/rewrite의 반환값은 아직 검수 결과가 아닙니다.
from beauty_ai.review_service import ReviewService
review_service = ReviewService(text)
reviewed_candidates = []
for variant in draft.data.variants:
    report = await review_service.review(
        facts, variant.copy_bundle, options,
        copy_revision=1, rules=reviewed_rule_pack,
    )
    reviewed_candidates.append((variant, report))
# 앱은 각 variant ID와 snapshot/report를 저장하고 검수 상태를 표시합니다.

# 수정안은 새 revision으로 저장하고 동일한 규칙+LLM 검수를 다시 실행합니다.
# 자동 재작성 반복은 서비스에서 1회 등으로 상한을 둡니다.
```

실제 RulePack은 팀이 검토한 문서·버전·조항 근거와 함께 서버에서 로드해야 합니다. 테스트의 `금칙샘플` 규칙은 순수 테스트 데이터이며 법규가 아닙니다. 법규 사전이 없는 상태에서 정상 검수가 된 척하지 않도록 draft RulePack은 `unavailable`을 반환합니다.

## 모델 호출에 대한 주의

`analyze_product()`는 앱 자산 저장소에서 검증·재인코딩한 이미지의 data URL을 받습니다. 사용자 입력 URL을 그대로 서버에서 가져오지 마세요. 함수의 접두어 검사는 전체 이미지 검증을 대체하지 않습니다. 업로드 경계에서 파일 형식, 용량, 디코딩 후 픽셀 수, EXIF, 소유권을 검사해야 합니다.

VLM이 라벨에서 읽은 효능 문구는 `ProductObservation`에만 남습니다. 이를 `ProductFacts.confirmed_claims`나 기능성 분류로 자동 승격하면 안 됩니다. 근거 파일 참조가 있다는 것과 그 근거가 유효하다는 것도 별개입니다.

외부 API transport 재시도는 OpenAI SDK 한 곳에서만 처리합니다. 위 레이어에 전체 워크플로 재시도 데코레이터를 추가하지 마세요. 토큰 제한, 타임아웃, 동시성은 초기 조정값이지 성능 보장이 아닙니다. 실제 과금 상한은 별도 예산 원장에서 선예약/정산해야 합니다.

`store=False`를 사용하지만 이것만으로 외부 서비스의 모든 보관을 금지하거나 무보관을 보장하는 것은 아닙니다. 프로젝트 개인정보 정책은 별도 확인이 필요합니다.

## private inference 서버가 반드시 지켜야 하는 계약

```http
PUT /internal/v1/jobs/{server_generated_job_id}
GET /internal/v1/jobs/{server_generated_job_id}
```

`PUT`에 `CutoutRequest` 또는 `BackgroundRequest`를 전달합니다. 서버는 작업 ID와 입력 해시를 **영속 저장한 다음** 202를 반환해야 합니다. 같은 ID와 같은 해시이면 기존 작업을 반환하고, 다른 해시이면 409를 반환해야 합니다. Header 하나를 붙이는 것만으로 멱등성이 생기지 않습니다.

GPU 동시 실행은 inference 서버에서 1개로 직렬화합니다. api 클라이언트의 semaphore만으로 GPU 서버 전체 동시성을 보장하지 마세요. `async def` 안에서 동기 GPU 연산을 직접 실행하지 말고 단일 전용 실행 스레드/프로세스에서 처리합니다. HTTP 연결 종료는 GPU 연산 종료가 아닙니다.

`SubmissionUncertain(job_id)`는 실패 후 새 ID로 재생성하라는 뜻이 아닙니다. 먼저 그 ID로 상태를 조회합니다. 아직 record가 안 보이는 경우에도 요청 처리 경합을 고려하여 새 ID를 발급하지 않습니다. 위의 원자적 멱등성 계약이 구현되어 있다면 동일 ID로 조정된 재전송을 할 수 있습니다.

`JobStillPending(job_id)`도 작업 실패가 아닙니다. 화면은 해당 ID의 상태 조회를 계속할 수 있어야 합니다.

## 이미지 보존 범위

마스크 세 가지를 구분합니다.

- `alpha matte`: 제품 투명도.
- `preserve_mask`: 우리 계약에서는 흰색이 보호 영역.
- `inpaint_mask`: Diffusers 입력에서는 흰색이 재생성 영역.

모델 출력만으로 픽셀 보존을 보장하지 않습니다. 승인된 작업 캔버스의 원본 제품 RGBA를 마지막에 다시 합성합니다. 이 함수는 완전 불투명한 제품 픽셀에 대해서만 일치를 확인할 수 있습니다. 반투명 경계·유리·반사 재질은 육안 검수가 필요합니다. 리사이즈한 경우 비교 기준은 원본 고해상도 사진이 아니라 승인한 변환 후 제품 레이어입니다.

`scene_underlay`는 제품 없는 순수 배경이라고 보장하지 않습니다. MVP는 제품 위치 고정입니다. 3단계 드래그 편집에서 제품을 다른 위치로 옮기려면 제품 없는 배경 생성/복원(clean plate) 계약을 추가해야 합니다.

## 참고 자료

원문 근거: 업로드된 `통합본`의 유저 플로우, E2E 결과물, 제품 보존, 시스템 아키텍처 및 기술 스택 절.

공식 API 동작 확인 자료:
- OpenAI 구조화 출력: https://developers.openai.com/api/docs/guides/structured-outputs
- OpenAI 이미지 입력: https://developers.openai.com/api/docs/guides/images-vision
- OpenAI Python SDK: https://github.com/openai/openai-python
- Diffusers 인페인팅: https://huggingface.co/docs/diffusers/using-diffusers/inpaint

법률/가이드라인의 최신 내용은 이번 작업에서 검증하거나 구축하지 않았습니다. 이 패키지의 API 설계와 법규 데이터의 정확성을 구분하세요.

## FiveCircles 작업 시작

프로젝트에 맞춘 [FiveCircles 안내](fivecircles/README.md)를 읽고 시작합니다. “fivecircles 초기화” 또는 “이미지 TODO의 다음 항목 진행”으로 요청할 수 있습니다.

- [담당·협업 경계](fivecircles/agent/collaboration-protocol.md): 이미지 pi, 텍스트 김다혜.
- [작업 인덱스](fivecircles/architecture/todolist.md): 기존 TODO를 참조하며 10/30 M1·11/6 이미지 M2를 유지합니다.
- [테스트 정책](fivecircles/test/testpolicy.md), [Git 규칙](fivecircles/agent/git.md), [원본 출처·버전](fivecircles/UPSTREAM.md).
- 설정 확인: 저장소 루트에서 `python3 scripts/check_fivecircles.py`.
- 프로젝트 내부 스킬은 `.agents/skills/fivecircles/SKILL.md`에 있습니다.

## 로컬 제품 레이어 준비 (B1)

저장소 루트에서 이미 누끼를 처리한 RGBA PNG와 `VisualBrief` JSON을 전달합니다.

```bash
python -m examples.prepare_product product-rgba.png visual.json \
  --project-id 00000000-0000-0000-0000-000000000001 \
  --store outputs/local-assets
```

업로드를 정규화하고 제품 후보·보호 마스크·흰 배경의 합성 검증 preview를 저장합니다. 반환 자산은 해당 프로젝트에서만 읽을 수 있습니다. `needs_approval=true`이며 실제 누끼/배경 AI나 사용자 승인을 수행하는 명령은 아닙니다. 입력 사진과 생성 파일은 Git에서 제외합니다.

일별 계획은 마일스톤 안내이며 의존성이 준비된 작업은 먼저 완료할 수 있습니다. [현재 구현과 남은 흐름](fivecircles/architecture/spec/image-runtime-flow.html)을 참고하세요.

## 승인 절차가 있는 로컬 이미지 서버 (B3)

이 절의 기본 설정은 기존 투명 PNG를 제품 레이어로 받고 밝은 배경 템플릿을 만듭니다. 기본 `uploaded-alpha`는 RGB·완전 불투명 입력에 `MODEL_UNAVAILABLE`을 반환합니다. 실제 누끼·배경 모델을 선택하는 방법은 아래 B6·B7 절에 있습니다.

루트에서 `python -m pip install -e '.[inference]'`로 설치합니다. `BEAUTY_PROJECT_ID`에는 UUID, `INFERENCE_INTERNAL_TOKEN`에는 32자 이상의 무작위 비밀값을 설정합니다. `BEAUTY_STORAGE_ROOT`의 기본값은 Git 제외 경로 `outputs/inference`입니다. 재시작할 때 같은 저장 경로와 프로젝트 UUID를 사용합니다. `.env`를 자동으로 읽지 않습니다.

```bash
python -m beauty_ai.inference_runtime
```

기본 주소는 `127.0.0.1:8001`입니다. 호스트 로그인·TLS·운영 네트워크·GPU 준비는 별도입니다. `GET /internal/v1/health`는 인증이 필요하며, 로컬 worker 상태와 오류만 반환합니다. `model_readiness: not_assessed`는 모델 검증을 하지 않았다는 뜻입니다.

클라이언트 터미널에도 같은 `INFERENCE_INTERNAL_TOKEN`과 `INFERENCE_BASE_URL=http://127.0.0.1:8001`을 설정합니다. 서버와 클라이언트가 같은 비밀값을 사용하되 코드·Git·로그에는 넣지 않습니다.

```bash
python -m examples.run_image prepare outputs/product-cutout.png examples/bright_visual.json
python -m examples.run_image download <candidate_product_rgba_asset_id> outputs/candidate.png
# candidate.png의 제품 경계·크기·위치를 사람이 확인한 다음에만 실행
python -m examples.run_image approve <candidate_id> <candidate_hash>
python -m examples.run_image render <candidate_id> --job-id <new_uuid> --seed 42
python -m examples.run_image get-job <job_id>
python -m examples.run_image download <composed_preview_asset_id> outputs/preview.png
```

`prepare` 결과는 `approved=false`입니다. 승인 hash가 다르면409, 미승인 배경 제출도409입니다. `approve`는 호스트가 사용자 확인을 전달했다는 기록이며, 최종 광고·문구·법률·이미지 품질 승인이 아닙니다. 파일 저장은 덮어쓰기 없이 수행합니다. 제출 응답이 유실되면 출력된 job ID를 조회하고 같은 ID를 유지합니다.

고정 프로필은 `uploaded-alpha/1`과 `bright-template/1`입니다. 템플릿의 style은 `bright-ad/1`, layout은 `product-right/1`이며 작업 해상도는256~1024,8의 배수입니다. 설정·임의 모델경로는 사용자 요청으로 바꾸지 않습니다. `scene_underlay`, 승인 `product_rgba`, `composed_preview`를 각각 조회할 수 있습니다. `TEMPLATE_NOT_AI_GENERATION`·`MANUAL_QUALITY_REVIEW_REQUIRED` 경고를 숨기지 않습니다.

## 문구 수정·현재 선택·이미지 캐시 (B4)

`ImageEditStore`는 프로젝트·캠페인별 현재 이미지 입력과 성공 결과를 저장합니다. `revision`은 편집 상태 버전이고 `copy_revision`은 호스트가 관리하는 문구 버전입니다. 이 모듈은 문구·검수 내용을 수정하지 않습니다.

```bash
# 최초 선택: 제품 후보는 앞 절에서 승인한 상태여야 합니다.
python -m examples.run_image select <candidate_id> <campaign_id> --expected-revision 0 --copy-revision 1 --seed 42
# active_job_id가 없을 때만 기존 render 명령으로 같은 candidate/seed의 작업을 제출합니다.
python -m examples.run_image render <candidate_id> --job-id <job_id> --seed 42
python -m examples.run_image attach <campaign_id> <job_id> --expected-revision 1
# 문구만 수정하면 같은 이미지 입력의 검증된 결과를 재사용합니다.
python -m examples.run_image select <candidate_id> <campaign_id> --expected-revision 1 --copy-revision 2 --seed 42
python -m examples.run_image get-edit <campaign_id>
```

`select`는 현재 revision과 `expected_revision`이 같을 때만 새 버전을 저장합니다. 결과를 붙일 때도 같은 revision과 요청 hash를 요구합니다. 그사이 제품·구도·seed가 바뀌면 늦은 결과는409로 거절합니다. 성공 cache는 같은 프로젝트에서만 재사용하고 실제 결과 파일이 손상됐으면 miss로 처리합니다. 저장소 장애는 cache miss로 숨기지 않습니다. 오류 뒤 무조건 새 작업 ID를 발급하거나 생성 재시도를 하지 않습니다.

선택 API는 `PUT/GET /internal/v1/edits/{campaign_id}`, 결과 반영은 `PUT /internal/v1/edits/{campaign_id}/result`입니다. 기존 job 조회는 작업의 역사적 결과이고, 현재 화면에 반영할 결과는 edit의 `active_job_id`를 확인해야 합니다. 최종 파일 열람 시에도 자산 권한·파일 무결성 검사를 거칩니다. 실제 CampaignService/UI 연결과 팀 계약 수락은 별도입니다.

`BackgroundRequest.image_prompt`는 텍스트 담당자가 만든 영어 프롬프트를 받는 선택 필드입니다. `positive`, `negative`, `prompt_version`, `source_revision`, `language`를 함께 전달하며 내용·버전이 바뀌면 이미지 hash도 바뀝니다. 필드가 없으면 기존 요청 body/hash를 유지합니다. 길이를 넘으면 거절하고 자동 절단하지 않습니다. 현재 `bright-template`은 이 프롬프트를 실행하지 못하므로 값이 있으면 명시적으로 거절합니다. 실제 모델의 tokenizer 길이·배경 생성 지원은 해당 모델 adapter에서 검증해야 합니다.

## 평가 파일과 작업 시간 기록 (B5)

평가 도구는 로컬 이미지 파일을 비교합니다. 모델을 실행하거나 이미지 품질을 자동으로 합격 처리하지 않습니다. 평가셋과 결과는 Git에서 제외하는 `data/`, `outputs/` 아래에 보관합니다.

```bash
# 예제는 권리 미확인 상태라 이미지 파일을 열지 않고 NOT_ASSESSED를 기록합니다.
python -m beauty_ai.evaluation examples/evaluation_manifest.json --output outputs/evaluation-example.json
# 준비한 실제 평가셋 manifest로 실행합니다. 기존 결과 파일은 덮어쓰지 않습니다.
python -m beauty_ai.evaluation data/evaluation/manifest.json --output outputs/evaluation-run-001.json
# 별도 작업 기록 조회: GPU 연산시간이 아니라 접수부터 종료 기록까지의 서버 경과시간입니다.
python -m examples.run_image get-timing <job_id>
```

manifest에는 평가셋 ID·버전과 case별 ID·dev/holdout 구분·제품 종류·권리 출처·파일 상대경로를 적습니다. 이미지 경로는 manifest 폴더 아래에 있어야 합니다. `rights_status`가 `unknown`이면 해당 파일을 측정하지 않습니다. `declared_authorized`와 `synthetic_fixture`는 작성자의 선언이며, 도구가 권리를 확인한 것은 아닙니다.

필수 입력은 승인 제품 레이어 `product_rgba`와 합성 결과 `composed_preview`입니다. 마스크 비교에는 `predicted_mask`, `reference_mask`를 함께 넣습니다. 정답 마스크가 없으면 IoU·경계 점수는 비워 둡니다. 흰색을 전경으로 해석하며 IoU는 교집합/합집합, 경계는 3×3 침식과 기본 2픽셀 정사각 허용 범위로 계산합니다. 빈 분모는 0점 대신 `null`입니다. 입력 크기·파일 hash·측정 조건도 결과에 남깁니다. 실제 벤치마크와 같은 평가 규약이라는 뜻은 아닙니다.

불투명 제품 픽셀 보존과 부분 투명 픽셀 수를 별도로 기록합니다. 유리·반사·경계의 자연스러움은 사람이 판단해야 합니다. `human_review`도 작성자의 선언이며 `quality_approval`은 항상 `NOT_ASSESSED`입니다. 합성 fixture 통과를 실모델 품질 근거로 쓰지 않습니다.

`GET /internal/v1/jobs/{job_id}/timing`은 프로젝트 인증이 필요합니다. `ledger_elapsed_ms`에는 큐 대기·처리·저장이 포함됩니다. 실행 중이면 `null`, 시계가 뒤로 움직였으면 `null`과 사유를 반환합니다. `server_wall_clock` 값은 시스템 시각 보정의 영향을 받을 수 있습니다. 실패·중단 작업에도 시간이 있을 수 있으므로 `status`를 함께 확인합니다. GPU 시간·최대 VRAM·가격은 측정하지 않으며, 작업 ID와 평가 파일이 같은 결과라는 연결도 자동 추정하지 않습니다.

## 실제 로컬 누끼 모델 (B6)

[로컬 모델 설치·GCP 이전 안내](docs/localModelSetup.md)에 설치와 실행 명령을 정리했습니다. 기본 실행은 기존 alpha/template이고, `birefnet-lite`를 명시하면 고정된 BiRefNet Lite를 사용합니다. 모델 가중치는 약178MB이며 `models/` 아래에 별도로 준비합니다. Python 실행 라이브러리 공간은 추가로 필요합니다.

```bash
python -m pip install -e '.[inference,models]'
python -m beauty_ai.model_store prepare --root models
python -m beauty_ai.model_store verify --root models
export BEAUTY_CUTOUT_BACKEND=birefnet-lite
export BEAUTY_MODEL_ROOT=models
export BEAUTY_MODEL_DEVICE=mps
python -m beauty_ai.inference_runtime
# 별도 클라이언트 터미널: 인증/주소 환경변수 설정 후 실행
python -m examples.run_image prepare outputs/product-photo.png examples/bright_visual.json --cutout-profile birefnet-lite
```

이 경로는 일반 사진에서 제품 alpha를 예측하고 실제 RGBA와 보호 마스크를 저장합니다. 이후 후보 확인·승인 절차는 같습니다. API 요청에서 모델을 내려받지 않으며, 선택한 장치를 사용할 수 없으면 오류를 반환합니다. 검증하지 않은 모델 파일·코드는 실행하지 않습니다. GCP에서는 동일한 API를 유지하면서 CUDA 환경과 모델 파일을 준비하고 장치 설정을 바꿔 검증합니다. Mac과 CUDA의 속도·픽셀 일치는 별도 측정 대상입니다.

M4 Max 64GB에서 합성 제품 fixture로 실제 MPS 추론·TCP API·prepare CLI를 확인했습니다. 첫 작업의 접수→종료 기록은 약6.9초이며 로딩·대기·저장을 포함합니다. 실제 화장품 사진·유리·라벨 경계 품질은 확인하지 않았고, 반환값은 항상 수동 확인 필요 상태입니다. 이 B6 기록은 누끼 모델의 검증입니다.

## 실제 로컬 SDXL 배경 모델 (B7)

SDXL base fp16 실행 파일은 약6.94GB입니다. 고정 모델을 준비하는 명령만 다운로드를 수행하며, 서버는 로컬 파일의 크기·SHA256을 검증한 뒤 사용합니다. 기본 템플릿으로 자동 대체하거나 CPU로 자동 전환하지 않습니다.

```bash
python -m pip install -e '.[inference,models,background]'
python -m beauty_ai.sdxl_model_store prepare --root models
python -m beauty_ai.sdxl_model_store verify --root models
export BEAUTY_CUTOUT_BACKEND=birefnet-lite
export BEAUTY_BACKGROUND_BACKEND=sdxl-inpaint
export BEAUTY_MODEL_ROOT=models
export BEAUTY_MODEL_DEVICE=mps
python -m beauty_ai.inference_runtime
# 별도 클라이언트: 후보 확인·승인을 마친 뒤 실행
python -m examples.run_image render <candidate_id> --job-id <new_uuid> --seed 42 \
  --background-profile sdxl-inpaint --prompt examples/bright_flower_prompt.json
```

`sdxl-inpaint/sdxl-base-fp16-v1`은 `bright-ad/1`, `product-right/1`에 연결됩니다. 각 작업 축의 허용 크기는512·768·1024이며 실제로 검증한 크기는 [설치 문서](docs/localModelSetup.md)의 실행 기록을 따릅니다. Euler scheduler·20steps·guidance7.5·strength0.99는 서버 설정입니다. `select`에도 같은 `--background-profile`, `--prompt`, seed를 전달해야 현재 편집의 입력 hash와 생성 작업이 일치합니다.

예제 프롬프트는 직접 작성한 꽃 배경 입력이며 팀 LLM 산출물은 아닙니다. 영어 프롬프트의 positive·negative 각각을 두 tokenizer로 검사합니다. 시작·끝 토큰 포함77개를 넘으면 내용을 로그에 출력하거나 잘라 쓰지 않고 거절합니다.

제품 영역을 보호하도록 인페인트 마스크를 만들고, 생성 후 텍스트 여백을 중립색으로 복원하며 승인된 제품 레이어를 다시 합성합니다. `scene_underlay`에는 생성 제품 잔상이 남을 수 있습니다. 중복 제품·가짜 문자·부분 투명 경계·조명은 사람이 확인해야 합니다. base SDXL에는 이 경로에서 사용하는 NSFW 검사기가 없으며 `GENERATION_SAFETY_NOT_ASSESSED`를 반환합니다. 유효한 PNG 저장과 광고 품질 승인은 별개입니다.

클라이언트의120초 대기 한도를 넘으면 `JOB_STILL_PENDING`입니다. 출력된 기존 job ID로 `get-job`을 실행하고 새 작업을 중복 제출하지 않습니다. 모델의 OpenRAIL++-M 사용 조건·원본 라이선스·가중치 고정 목록과 GCP 이전 절차는 [설치 문서](docs/localModelSetup.md)에 있습니다.

M4 Max64GB에서 두 모델을 실제 MPS로 실행해512×512 결과 파일을 확인했습니다. 첫 배경 job은 약14.7초(로딩·대기·저장 포함), 실행 후 MPS driver 메모리는 약10.24GB였습니다. 최대 메모리 측정값은 아닙니다. 불투명 제품770픽셀과 텍스트 여백은 보존됐지만, 샘플의 꽃 표현 부족·제품 잔상은 품질 보완 대상으로 남았습니다. 전체 자동 회귀433개 통과와 실제 광고 품질 통과를 구분합니다.

## 작업별 실행 기록 조회 (B9)

```bash
python -m examples.run_image get-execution <job_id>
```

`GET /internal/v1/jobs/{job_id}/execution`은 작업을 볼 수 있는 프로젝트의 내부 인증이 필요합니다. `RECORDED`이면 모델 revision·요청 hash·seed·선택 장치·모델 재사용 여부와 단계별 경과를 돌려줍니다. worker가 끝난 작업 상태와 관찰을 같은 DB transaction으로 저장합니다. 이전 버전의 작업이나 실행 도중 중단된 작업에 관찰이 없으면 `NOT_RECORDED`, `execution=null`입니다. 없는 관찰을 0초로 만들지 않습니다.

모델 로딩·전처리·모델 실행·합성·자산 저장·결과 검사는 monotonic wall-clock 관찰입니다. 일부 단계가 중첩되므로 시간을 모두 더하지 않습니다. 큐 대기는 별도의 서버 wall-clock이며, 장치 동기화 성공 여부도 함께 확인합니다. 로딩된 Python 패키지 버전은 프로세스에서 관찰한 값이며 그 작업이 모든 패키지를 호출했다는 뜻은 아닙니다.

MPS allocated/driver 및 CUDA allocated/reserved는 작업 후 프로세스 메모리 snapshot입니다. 최대 사용량, 작업 하나의 증가량, GPU 비용으로 해석하지 않습니다. 관찰이 불가능하면 `null`과 사유를 반환합니다. 프롬프트 원문·자산 경로/URL·토큰·원문 예외는 이 기록에 넣지 않습니다. 실제 측정 결과와 범위는 [로컬 모델 안내](docs/localModelSetup.md)를 따릅니다.

## 제출 응답이 끊긴 경우 (B8)

`prepare`와 `render`는 작업 ID를 먼저 출력하고 PUT을 한 번 보냅니다. 응답이 유실되거나 접수 여부가 불명확하면 같은 ID를 조회합니다. 올바른 작업 ID·입력 hash·결과 종류를 확인하며, 새 ID를 만들거나 자동으로 PUT을 다시 보내지 않습니다. Python에서는 `HTTPImageGateway.submit_and_wait(request, job_id=..., deadline_seconds=120)`를 사용합니다. 기존 `submit`, `get`, `wait`도 유지됩니다.

전체 대기 기한까지 접수된 작업을 확인하지 못하면 `SUBMISSION_UNCERTAIN`, 접수된 작업은 확인했지만 완료를 확인하지 못하면 `JOB_STILL_PENDING`입니다. 둘 다 GPU 실패나 취소를 뜻하지 않습니다. 조회404 하나만 보고 새 생성을 제출하지 마세요. 출력된 ID를 보관하고 `get-job`으로 이어서 확인합니다. 같은 ID의 단순 조회는 이미지 생성 비용을 추가하는 명령이 아닙니다.

입력 hash나 결과 종류가 다르면 프로토콜 오류로 종료합니다. 인증 오류와 서버가 기록한 실제 작업 실패도 그대로 구분합니다. 오류 메시지에는 토큰·프롬프트·서버 응답 원문을 넣지 않습니다. 사용자가 수동으로 다른 작업 ID를 발급하면 별도 작업이므로, 이 조회 조정이 별도 요청까지 자동으로 합쳐 주지는 않습니다.
