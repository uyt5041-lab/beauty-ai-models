# 화장품 광고 AI 래퍼 참고 구현

소상공인 화장품 광고 제작 MVP에 연결할 Python 참고 패키지입니다. 텍스트·VLM은 OpenAI API로, 이미지 작업은 별도의 private inference 서버로 분리합니다. 내부 이미지 FastAPI 앱·SQLite 작업 저장소·단일 호스트 worker·명시적 제품 승인·로컬 템플릿 실행·편집 revision·프로젝트 캐시·로컬 평가 도구를 포함합니다. **실제 누끼·배경 AI 모델과 광고 UI·렌더러까지 연결된 완성 서비스는 아닙니다.**

## 1. 쓰는 법

Python 3.11 이상에서 프로젝트 루트 기준으로 설치합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q -W error
```

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

모델은 계정에서 사용할 수 있고 해당 입력·구조화 출력을 지원하는지 확인한 뒤 설정합니다. 동시성·시간 제한의 초기값은 `.env.example`과 [클라이언트 조립 예제](examples/wiring.py)를 참고하세요.

호스트 애플리케이션에서 `OpenAITextGateway`와 `HTTPImageGateway`를 한 번 생성해 재사용하고 종료 시 SDK·HTTP 클라이언트를 닫습니다. 실행 순서는 **검증된 제품 이미지 관찰 → 사용자가 제품 사실 확인 → 카피 후보 생성 → 후보 전체 검수 → 선택·수정 시 새 revision으로 재검수 → 이미지 작업 제출·상태 조회**입니다. `examples/wiring.py`는 이 연결을 위한 예시이며 `uvicorn`으로 실행하는 API 서버가 아닙니다.

내부 이미지 API는 `python -m pip install -e '.[inference]'`로 의존성을 설치한 뒤 호스트에서 `create_app(ledger, assets, credentials, processor=None)`로 조립합니다. `ledger`는 `JobLedger`, `assets`는 `LocalAssetStore`, `credentials`는 **서버 소유 32자 이상 Bearer 토큰 → 프로젝트 UUID** 매핑입니다. 사용자가 보낸 project 헤더를 권한으로 사용하지 않습니다. 토큰은 환경변수나 비밀 저장소에서 주입합니다.

`processor`를 제공하지 않으면 작업은 영속 `queued` 상태로 남습니다. 같은 ledger와 자산 저장소를 쓰는 worker에 동기 processor를 연결해야 실행됩니다. 이 factory를 생성하는 것만으로 모델 로딩이나 외부 포트 공개가 시작되지는 않습니다. worker는 POSIX 잠금을 지원하는 단일 호스트/공유 로컬 파일시스템을 전제로 합니다.

로컬 실행기는 위 환경변수를 설정한 뒤 `python -m beauty_ai.inference_runtime`으로 기동합니다. 기본 주소는 `127.0.0.1:8001`입니다. 기존 투명 PNG를 제품 후보로 준비하고 사람이 확인한 뒤 명시적으로 승인하면 밝은 템플릿 위에 합성합니다. `uploaded-alpha/1`은 실제 누끼 모델이 아니며 RGB·완전 불투명 입력은 지원하지 않습니다.

로컬 평가 도구는 `python -m beauty_ai.evaluation examples/evaluation_manifest.json --output outputs/evaluation-report.json`으로 실행합니다. 예제의 권리는 `unknown`이므로 이미지를 읽지 않고 미평가로 기록합니다. 실제 평가는 권리와 출처를 확인한 파일을 manifest 폴더 아래에 두고, 비공개 `data/`·`outputs/`에서 실행하세요. 기존 결과 파일은 덮어쓰지 않습니다.

단계별 실행 명령은 [README2.md](README2.md), 최신 검증은 [B5 기록](fivecircles/test/relay-b5.md)을 참고하세요. **전체 280 tests PASS + 실제 평가/timing CLI·wheel build PASS**입니다. [TEST_REPORT.md](TEST_REPORT.md)는 초기 래퍼 검증 기록입니다.

## 2. 기능

| 구현 요소 | 현재 하는 일 |
| --- | --- |
| `OpenAITextGateway` | 제품 이미지 관찰, 광고 기획·카피 후보 생성, 문맥 검수, 수정안 생성 |
| 구조화 계약 | Pydantic 입력·출력 검증과 [JSON Schema](schemas/) 제공 |
| `ReviewService` | 전체 카피의 규칙 검사와 LLM 검사 병합, 검토된 규칙집 확인, revision·해시로 검수 대상 식별 |
| `HTTPImageGateway` | 내부 이미지 작업의 제출·조회·폴링, 작업 ID·입력 해시 확인, 불확실한 제출과 대기 상태 구분 |
| `inference_api.create_app` | Bearer 인증과 프로젝트 범위의 job PUT/GET·자산 업로드/조회·인증된 health 응답 |
| `JobLedger` | SQLite WAL 기반 atomic ID/hash 등록, owner·terminal 보호, durable queued 조회, 접수→종료 wall-clock 기록 |
| `SingleHostWorker` | 프로세스 간 POSIX 실행 잠금, 종료 확인 후 interrupted 복구, 결과 파일 검증 후 success |
| `LocalAssetStore` | 파일 형식·크기·회전·색 공간 정규화, 프로젝트별 파일/SQLite 저장과 hash 검증 |
| `image_preparation.py` | 제품 후보 캔버스·보호 마스크 생성, Safe Zone 검사와 불투명 픽셀 보존 검사 |
| `image_integrity.py` | 보호 마스크를 인페인트 마스크로 반전하고 승인 RGBA를 장면 위에 재합성 |
| `ProductApprovalStore` | 후보 snapshot·project·파일·형상 검증, 명시적 확인과 정확한 hash로 승인 저장 |
| `LocalTemplateProcessor` | 서버 고정 profile로 기존 alpha 처리·밝은 템플릿 합성, 3개 레이어 저장과 보존 검사 |
| `inference_runtime` / CLI | 로컬 서버 조립, 준비→확인→승인→합성→조회·다운로드 및 이미지 선택·결과 연결 명령 |
| `ImagePrompt` | 텍스트 담당이 만든 프롬프트·버전·revision 수신, 문자 수 검증, 기존 요청 hash 호환 |
| `ImageEditStore` | 현재 selection의 revision CAS, 프로젝트별 검증된 성공 결과 캐시, 늦은 결과 연결 거절 |
| `evaluation.py` / `evaluation_metrics.py` | 권리 선언·파일 범위를 검사한 로컬 manifest 평가, 불투명 픽셀 보존·mask IoU·경계 지표와 파일 hash 기록 |
| 공통 오류·메타데이터 | 거절·미완료·타임아웃·프로토콜 오류 분리, 요청 ID·사용량·소요 시간 기록 |

검수 상태는 `no_flags`·`warnings`·`blocked`·`unavailable`입니다. `no_flags`는 설정된 자동 검사에서 표시된 이슈가 없다는 뜻이며 법적 승인이나 광고 효과의 보장이 아닙니다. 규칙집이 준비되지 않았거나 검수가 실패하면 `unavailable`로 남깁니다.

## 3. 아키텍처

```text
[호스트 구현 필요: UI / 사용자 인증 / CampaignService / SSE / Export]
   ├─ OpenAITextGateway ── OpenAI API
   ├─ ReviewService ── 규칙 검사 + TextGateway 문맥 검사
   └─ HTTPImageGateway ── 내부 FastAPI 앱
                           ├─ Bearer → project / LocalAssetStore
                           └─ JobLedger (SQLite WAL)
                                └─ SingleHostWorker (POSIX 실행 잠금)
                                     └─ 주입한 processor
                                          ├─ LocalTemplateProcessor (기존 alpha / 밝은 템플릿)
                                          └─ [연결 필요: 실제 누끼·배경 AI 모델]
                           ├─ ProductApprovalStore (정확한 후보 snapshot 승인)
                           └─ ImageEditStore (현재 revision / project cache / result attach)

공통 계약: contracts.py / schemas/    이미지 후보: image_preparation.py
제품 재합성: image_integrity.py      결과 파일 검증 → terminal commit
별도 로컬 평가: evaluation.py → mask / pixel 측정 (모델 호출·품질 승인 없음)
```

내부 API는 `PUT/GET /internal/v1/jobs/{job_id}`, `POST /internal/v1/assets`, `GET /internal/v1/assets/{asset_id}`를 제공합니다. 동일 프로젝트·작업 ID·입력 hash는 기존 작업을 반환하고, 같은 ID·다른 hash는 `409`입니다. 다른 프로젝트의 job·자산은 미존재와 같이 처리합니다. 접수는 DB commit 이후 응답하며, worker는 DB에서 queued 작업을 다시 찾습니다.

`POST /internal/v1/products/prepare`, `GET /internal/v1/products/{candidate_id}`, `PUT /internal/v1/products/{candidate_id}/approval`로 후보 준비·조회·승인을 수행합니다. 준비는 항상 미승인 상태이며 확인값 `true`와 정확한 candidate hash가 필요합니다. 자산·구도가 바뀌면 새 승인이 필요합니다. 저장된 승인은 호스트의 사용자 확인 전달 기록이며 사람 신원·최종 품질 인증이나 현재 편집 선택을 뜻하지 않습니다.

`GET /internal/v1/health`는 인증 후 해당 앱의 worker 구성·실행 상태·안전한 fatal code를 알려줍니다. worker fatal은 `503`이며 `model_readiness`는 `not_assessed`입니다. health 성공은 모델 준비나 이미지 품질 증거가 아닙니다.

`GET /internal/v1/jobs/{job_id}/timing`과 `examples.run_image get-timing`은 해당 프로젝트 작업의 접수→종료 경과 시간을 반환합니다. 대기·처리·저장을 포함한 서버 wall-clock이며 GPU 연산 시간이 아닙니다. 아직 실행 중이거나 시계가 역행한 경우 시간 값은 `None`입니다.

평가 manifest는 dataset/version·dev/holdout·제품 유형·권리와 사람 판정의 선언을 기록합니다. 마스크 정답이 없으면 지표는 `None`, 권리가 미확인이면 파일 측정을 건너뜁니다. 출력의 `model_invoked`는 `false`, `quality_approval`은 `NOT_ASSESSED`이며 선언된 사람 판정은 검증된 신원이 아닙니다. 입력 경로는 manifest 폴더 내부로 제한하고 URL·외부 경로는 받지 않습니다.

실행 잠금은 한 호스트·같은 ledger에 연결한 동기 processor를 직렬화합니다. 실행 중 stop timeout은 취소가 아니며 잠금을 유지합니다. 새 worker는 이전 프로세스 종료로 잠금을 획득한 뒤 running 작업을 interrupted로 수습합니다. 다중 VM 조정과 분리된 GPU subprocess 관리는 포함하지 않습니다.

`PUT/GET /internal/v1/edits/{campaign_id}`로 현재 이미지 선택을 저장·조회하고, `PUT /internal/v1/edits/{campaign_id}/result`로 완료 job을 연결합니다. 현재 revision과 요청 hash가 맞아야 연결되며, 실제 파일을 확인한 성공 결과만 프로젝트 캐시에서 재사용합니다. 문구 revision만 바뀌면 같은 이미지 결과를 재사용하고 visual·seed·profile·자산·프롬프트가 바뀌면 다른 key입니다. 캐시 miss가 자동으로 새 작업을 실행하지는 않습니다.

검수 snapshot은 제품·카피·옵션·규칙·검수 모델·프롬프트에 묶입니다. `BackgroundRequest.image_prompt`가 있으면 텍스트·버전·revision을 이미지 hash에 포함하며, 없으면 기존 body/hash를 유지합니다. 영어 프롬프트 생성·언어 판별·실모델 tokenizer 검증은 하지 않습니다. 로컬 bright-template은 프롬프트를 해석하지 않으므로 제공된 image_prompt를 거절합니다. [현재 흐름](fivecircles/architecture/spec/image-runtime-flow.html)과 [설계](DESIGN.md)를 참고하세요.

## 4. 미완성으로서 보완해야 할 점

- **서비스 통합:** 사용자 로그인·CampaignService·SSE·내보내기·렌더러와 실제 UI 연결이 필요합니다. 내부 API 인증·job 복구·이미지 selection revision·프로젝트 캐시는 로컬 구현했습니다.
- **실제 모델:** 사용자 지시에 따라 로컬 Mac에서 모델 revision·CPU/MPS 호환성과 작은 샘플을 먼저 검증한 뒤 GCP로 이전합니다. 고정 환경·warm-up·실제 누끼/배경 생성·품질 검증은 아직 필요합니다. 로컬 `uploaded-alpha/1`, `bright-template/1`의 고정 profile은 구현했으나 실제 누끼·SDXL 모델은 아닙니다.
- **제품 승인·편집:** 정확한 snapshot 승인과 늦은 result attach 차단을 구현했습니다. 실제 사용자 신원·승인 UI·편집 서비스와 최종 품질 승인은 별도로 연결해야 합니다.
- **검수 데이터:** 출처·버전·검토 이력이 있는 규칙집, 제품 사실 확인, 카피 수정 후 재검수·DB revision 경쟁 검사가 필요합니다.
- **품질·실환경:** 측정 도구는 구현했지만 실제 고정 평가셋·권리 근거·판정 기준을 준비하고 라벨·경계·투명/반사·그림자·여백·최종 텍스트 합성, 실제 모델 호출·지연·VRAM·비용을 평가해야 합니다. 불투명 픽셀 검사만으로 광고 품질을 승인하지 않습니다.
- **운영:** 실제 네트워크 접근 범위·비밀값 관리·배포·예산 상한·모델 모니터링을 구성해야 합니다. 로컬 HTTP/파일/DB 테스트와 운영 GPU 검증을 구분합니다.

## 5. 하지 말 것

- API 키, 실제 `.env`, 로컬 설정·DB·로그·테스트 캐시, 업로드·생성 이미지, 모델 가중치를 커밋하지 않습니다. `.env.example`에는 빈 값이나 명백한 예시만 둡니다.
- VLM이 읽은 라벨·효능 문구를 확인된 제품 사실이나 기능성 분류로 자동 승격하지 않습니다. 업로드 이미지·사용자 텍스트를 시스템 지시로 취급하지 않습니다.
- 검수 실패·규칙집 부재·오래된 snapshot을 통과로 처리하지 않습니다. LLM 결과로 규칙 검출을 지우거나 `no_flags`를 법적 적합 판정으로 설명하지 않습니다.
- 사용자 제공 URL을 검증 없이 서버에서 가져오지 않습니다. 이미지 디코딩·용량·픽셀 수·메타데이터와 자산 접근 권한을 별도로 검증합니다.
- 제출 응답 유실이나 폴링 시간 초과 뒤 새 작업 ID로 무조건 재생성하지 않습니다. 기존 ID를 조회·복구합니다. 클라이언트 semaphore를 서버 전체 GPU 동시성이나 영속 멱등성으로 간주하지 않습니다.
- 평가 지표·불투명 픽셀 일치를 품질 합격으로 바꾸거나 wall-clock 경과를 GPU 연산 시간·VRAM·비용으로 설명하지 않습니다. 실제 평가 이미지·권리 자료·산출 보고서는 공개 Git에 넣지 않습니다.
- 로컬 템플릿을 AI 모델 결과로 표시하거나 `TEMPLATE_NOT_AI_GENERATION`·`MANUAL_QUALITY_REVIEW_REQUIRED` 경고를 숨기지 않습니다.
- SDK 재시도 위에 무제한 재시도를 덧씌우지 않습니다. 카피 수정 때문에 배경을 재생성하거나, 참고 문서의 속도·메모리·보존율 수치를 실측 결과처럼 쓰지 않습니다.
- 공개 저장소는 `plan/models`에서 모델 코드를, `박지수_협업일지`에서 날짜별 작업 기록을 관리합니다. 협업일지는 PR·병합 없이 별도로 관리합니다. 자세한 운영 방식은 [README2.md](README2.md)를 따릅니다.
