# API 래퍼 설계 제안

## 최신 방향 · 2026-10-22 사용자 정정

**LLM은 김다혜, 이미지도 외부 상용 API 래퍼는 pi가 담당한다.** 이미지 제공자·모델은 미정이며 비교 리서치 후 선정한다. 과거 원문의 자체 GCP 이미지 추론 전제는 현재 MVP 실행 방식을 결정하지 않는다. 외부 이미지 API 연결은 아직 구현하지 않았다.

목표는 제품 사진·선택 배경 참조·검토된 프롬프트를 외부 이미지 생성/편집 API에 전달하고, 응답을 검증·저장해 사용자 검토와 최종4:5 한글 렌더에 연결하는 것이다. 누끼가 필요한 경로도 외부 API 지원을 확인한다. 원본 보존·리사이즈·한글 합성과 같은 결정적 로컬 처리는 모델 자체 호스팅과 구분한다. 기존 단일 source·3개 레이어 계약을 모든 제공자가 지원한다고 가정하지 않는다.

자산·작업·권한·해시·캐시와 평가는 재사용하되 제공자 지원 기능·외부 요청 ID·중복 과금·결과 다운로드 계약을 추가한다. [전환 계약](fivecircles/architecture/contract/image-runtime.md#외부-이미지-api-전환--2026-10-22-사용자-정정)과 [현재 TODO](docs/imageTodo.md)가 우선한다. 로컬 BiRefNetLite·SDXL은 실험 자료로 보존하며 아래 기존 설계는 출처와 구현 이력으로 읽는다.

## 1. 원문에서 유지하는 것과 이번 제안

원문 유지: React + FastAPI + GCP L4 inference, OpenAI 텍스트·VLM, 규칙+LLM 검수, 카피 먼저 생성, 이미지/제품/텍스트 분리, 로컬 파일+SQLite, 단계별 확장.

이번 제안: 구체적인 클래스·메서드, endpoint 이름, request/response JSON, 자산 ID, revision/hash, 멱등 작업 제출, 명시적 검수 불능 상태, 마스크 이름 및 원본 재합성, timeout과 초기 동시성 값. 원문에서 확정된 구현 사항으로 읽으면 안 됩니다.

미정 유지: DOM/Canvas vs Pillow 텍스트 합성, 누끼 모델, IP-Adapter/IC-Light 적용 시점, 실제 OpenAI 모델, 최종 채널 범위. 원문은 IP-Adapter를 기본 스택과 후속 확장 양쪽에 적고 있으므로 pipeline profile 옵션으로 분리합니다.

이 설계에서 좁힌 MVP 가정: 국내 인스타 피드 1종, 제품 위치 고정, 카피 후보 최대 3개 중 하나 선택, 이미지 1장 생성. 원문에 1:1와 4:5가 모두 있으므로 실제 채택 비율은 template registry에서 설정하며 두 비율을 동시에 필수 구현하지 않습니다.

## 2. 계층

```text
React
  -> FastAPI routes: 인증, 프로젝트 권한, 요청/응답
    -> CampaignService: 단계 순서, revision, 작업 저장, 검수 상태, 예산
      -> ProductAnalysisService
           -> TextGateway.analyze_product -> OpenAI
           -> ImageGateway.submit(cutout) -> private inference
      -> CopyService / ReviewService
           -> draft / rules / review / rewrite -> OpenAI
      -> ImageService
           -> ImageGateway.submit(render_background) -> private inference

Renderer / ExportService: 별도 계층, 텍스트 합성과 출력
```

wrapper는 HTTP/SDK 통신과 구조화 데이터 변환을 담당합니다. workflow, 규제 정책, DB 트랜잭션, SSE는 wrapper 내부에 넣지 않습니다. 단, provider 오류/사용량을 애플리케이션 공통 형태로 바꾸는 것은 wrapper의 역할입니다.

텍스트와 이미지가 같은 `generate(prompt)` 인터페이스를 공유할 필요는 없습니다. 같은 오류/추적 규약을 공유하되 작업별 계약을 유지합니다.

## 3. 텍스트 계약

| 메서드 | 입력 | 출력 | 저장 위치 |
|---|---|---|---|
| analyze_product | 검증된 제품 이미지 1~3장 | ProductObservation | 관찰/확인 대기 |
| draft_campaign | ProductFacts + AdOptions | CampaignDraft | 검수 전 후보 |
| review_copy | 전체 CopyBundle + facts + rule pack + 규칙 검출 | LLMReview | ReviewService가 합쳐 report 저장 |
| rewrite_copy | 기존 copy + facts + issues | CopyBundle | 재검수 전 후보 |

공통 `_parse`는 structured output, timeout, 동시성 제한, refusal/incomplete 구분, 메타데이터를 처리합니다. 업무별 public 메서드는 각자의 고정된 지시문과 스키마를 사용합니다. 프론트가 system prompt, 모델명, 출력 스키마를 바꾸지 못하게 합니다.

Draft의 `used_claim_ids`는 존재하는 ID인지 서버에서 확인합니다. 이것만으로 실제 문장의 근거 충족을 입증하지는 않으므로 문맥 검수를 별도로 수행합니다.

## 4. 검수 서비스

```text
현재 제품 정보 + 광고 설정 + 전체 카피 snapshot
  -> 규칙 검사
  -> LLM 문맥 검사
  -> 규칙 결과를 보존해서 병합
  -> issues + uncertainties + status + snapshot hash 저장
```

상태:
- `no_flags`: 설정된 자동 검사에서 표시된 이슈 없음. 법적 승인 아님.
- `warnings`: 근거/표현/분류 불확실성, 사람 확인 필요.
- `blocked`: 서비스 정책상 출력 진행을 막는 이슈 존재.
- `unavailable`: 검수 미완료/모델 오류/검토된 rule pack 없음.

RulePack의 `reviewed`는 팀이 관리하는 데이터 상태입니다. 외부 사용자나 LLM이 이를 지정해서 검증을 생략하면 안 됩니다.

광고에 포함되는 headline, subheadline, CTA, caption, hashtags, badges를 모두 검수합니다. 제목만 검사하고 캡션을 건너뛰지 않습니다. 금칙어를 일반화해서 법적 위반이라고 단정하지 않으며, 실제 rules는 적용 대상/예외/조건을 충분히 모델링한 검토된 규칙으로 보강해야 합니다. 현재 regex Rule은 최소 구현 예입니다.

자동 재작성은 제안상 최대 1회. 재작성 후에는 규칙과 LLM을 모두 다시 호출하고, 미해결이면 사람이 수정합니다. 무한 재작성 루프는 만들지 않습니다. 원문은 재작성 후 1차 검사만 적었지만 이 부분은 문맥 오류 재유입 방지를 위한 강화 제안입니다.

카피 수정은 새 revision을 만들고 이전 검수를 무효화합니다. 검사 완료 직전 DB의 현재 revision과 다시 비교합니다. 늦게 도착한 이전 검수 결과로 최신 카피의 상태를 바꾸지 않습니다.

출력 시 서버는 저장된 report와 현재 product/copy/options/rules/prompt/model snapshot hash를 비교합니다. 클라이언트가 `approved=true`나 report JSON을 보내도 승인 근거로 쓰지 않습니다. 프론트 로컬 다운로드 자체를 법적으로 통제할 수 있다는 의미는 아닙니다.

## 5. 이미지 계약

ImageGateway는 OpenAI Images API 래퍼가 아니라 자체 GCP inference 서버의 클라이언트입니다.

작업 1: `cutout`
- source AssetRef, pipeline profile ID/revision.
- 결과: 제품 RGBA, 보호 마스크, 경고, 수동 확인 필요 여부.

작업 2: `render_background`
- 원본 AssetRef, 승인 제품 RGBA, 보호 마스크.
- 고정된 visual brief: 컨셉, 무드/레이아웃 ID와 버전, 제품 위치, 텍스트 여백, 작업 해상도.
- seed, pipeline profile ID/revision.
- 결과: 생성 scene underlay, 승인 제품 layer, 제품을 재합성한 preview, 경고.

이미지 출력에 한국어 카피를 굽지 않습니다. 카피 JSON과 레이어를 renderer에 전달합니다. 따라서 프론트 합성이든 서버 Pillow 합성이든 텍스트/이미지 모델 wrapper는 바뀌지 않습니다.

GCP inference 구현 순서:
1. source 자산을 소유권 검사를 거친 내부 참조로 확인.
2. profile registry에서 누끼/SDXL 모델과 scheduler, adapter 설정을 선택.
3. 동일 working canvas에 제품을 배치하고 보호 마스크를 생성.
4. 마스크 의미를 adapter 경계에서 Diffusers 규칙으로 변환.
5. 배경/씬 생성.
6. 원본 제품 레이어 재합성 및 불투명 영역 비교.
7. 자산 저장, 결과 metadata 저장, 작업 성공 상태 commit.

레이아웃 여백은 prompt만으로 완전히 보장하지 않으므로 결과 품질 검사를 추가합니다. 제품 라벨·경계·중복 제품 생성·여백·투명 용기는 별도 테스트셋이 필요합니다. 아직 그 품질 검사를 구현한 패키지가 아닙니다.

API/inference가 한 VM의 Compose에 있다는 MVP 가정을 사용할 경우 `/data/assets`를 공유하고 ID로 조회합니다. VM이 나뉘면 asset store 구현을 바꾸면 됩니다. 원격 URL 다운로드를 도입할 때는 allowlist/서명 URL 등 별도 접근 통제가 필요합니다.

## 6. 사용자 API 제안

| endpoint | 반환/역할 |
|---|---|
| POST /v1/products/{id}/analysis-jobs | 202 + job ID, VLM/누끼 병렬 시작 |
| POST /v1/projects/{id}/copy-jobs | 202 + job ID, 후보 생성과 검수 |
| PUT /v1/projects/{id}/copy | If-Match 기준 revision 갱신 + 재검수 job |
| POST /v1/projects/{id}/image-jobs | 202 + job ID, 선택된 visual brief만 이미지 생성 |
| GET /v1/jobs/{id} | snapshot/status/result/error |
| GET /v1/jobs/{id}/events | SSE 진행 이벤트 |
| GET /v1/projects/{id}/manifest | 레이어/카피/레이아웃/검수 metadata |

업로드, 사용자 인증, 선택 후보 확정 route는 앱의 기존 CRUD 방식에 맞춰 별도 연결합니다. 위 route는 코드에 구현된 완성 서버가 아니라 제안 계약입니다.

HTTP 202의 의미는 작업 접수입니다. GPU 생성 완료를 기다리는 긴 POST를 만들지 않습니다. 이미지 실패는 텍스트 검수 결과를 삭제하지 않습니다.

## 7. 큐와 재시작

원문의 API asyncio 큐는 workflow 실행 순서용으로 유지합니다. inference는 별도로 GPU 동시 실행을 1개로 제한합니다. API 프로세스 여러 개의 semaphore가 GPU 전체 직렬화를 보장한다고 가정하지 않습니다.

MVP는 단일 API worker로 시작합니다. SQLite가 job 상태의 기준이고 asyncio.Queue는 실행할 job ID를 깨우는 역할입니다. DB에 commit한 작업을 queue에 넣기 전에 프로세스가 종료되어도 시작 시 queued 작업을 재검색합니다. GPU 워커 종료 시 running 작업은 interrupted로 기록하거나 실제 워커 상태를 확인해서 조정합니다.

아래 영속화는 아직 구현되지 않았습니다:

```text
jobs: id, project_id, kind, request_hash, status, stage,
      input_snapshot_json, result_json, error_code, created_at, updated_at
job_events: job_id, sequence, event_type, payload_json, created_at
ai_calls: id, job_id, provider, model, operation, request_id,
          input_tokens, output_tokens, prompt_version, elapsed_ms
assets: id, project_id, storage_key, sha256, mime, width, height
```

`PUT /internal/v1/jobs/{id}`의 idempotency와 GPU 직렬화는 inference 서버에서 실제 구현해야 합니다. 클라이언트 단위 테스트로 그 서버 동작까지 검증했다고 간주하면 안 됩니다.

SSE는 상태 전달일 뿐 실행 주체가 아닙니다. 브라우저가 닫혀도 job은 유지합니다. reconnect 시 Last-Event-ID 이후 저장 이벤트를 재전송하고, 최종 상태는 GET 조회로 복구합니다. provider token streaming과 앱 단계 SSE는 별개입니다. 기본은 stage events만 표시하며 검수 전 카피 토큰을 최종 승인문구처럼 보여주지 않습니다.

## 8. revision, cache와 예산

검수 key: 제품 snapshot + 카피 전체 + 옵션/시장 + copy revision + rule pack 내용/버전 + 검수 prompt/model.

이미지 key: 제품/누끼/마스크 hash + visual brief + pipeline/프리셋/템플릿 버전 + seed. 문구 자체는 포함하지 않습니다. 단, 문구 수정으로 template이나 safe zone이 바뀌면 visual key도 달라집니다.

동일 사용자의 동일 프로젝트 내 재사용을 기본으로 하며 프로젝트 권한을 확인하지 않은 전역 cache 결과를 노출하지 않습니다.

모델별 실제 토큰 사용량과 provider request ID를 기록합니다. 원문 제공 API 예산을 고려하여 서비스 계층에서 호출 전 예상 상한을 예약하고 사용량을 정산하는 예산 원장을 추가합니다. 실패/응답 분실로 사용량이 불명확한 호출을 자동으로 무료 처리하지 않습니다. 가격표는 이 패키지에 하드코딩하지 않습니다.

## 9. 구현 순서

1. 이 패키지의 Pydantic 계약과 mock 테스트를 팀 공통 기준으로 확정.
2. 텍스트 wrapper 실연결 + 구조화 출력/refusal/사용량 검증.
3. reviewed rule pack 작성, ReviewService와 copy revision 저장 연결.
4. 누끼 inference job, 단일 GPU executor, 멱등 ledger 연결.
5. 템플릿 배경으로 manifest까지 E2E 연결.
6. 동일 render 계약 안에 SDXL backend 연결 및 제품 보존 품질 평가.
7. SSE, 예산, 오류 복구, stale review 경쟁 상태에 대한 통합 테스트.

React/텍스트/이미지 담당자가 위 계약으로 병렬 개발할 수 있으며, 5번까지는 SDXL 품질 최적화를 기다리지 않고 연결할 수 있습니다.
