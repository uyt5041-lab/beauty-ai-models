# 추가 사용 예제와 기존 안내

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
