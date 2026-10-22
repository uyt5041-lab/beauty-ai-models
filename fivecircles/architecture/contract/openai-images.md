# OpenAI 이미지 래퍼 계약 · B10

상태: 2026-10-22 구현 전에 작성, B10에서 오프라인 구현·검증. 사용자 결정은 OpenAI 이미지 API + API 누끼이며 **실제 호출은 금지**다. 현재 계정·모델 접근·잔액·실제품 품질은 UNKNOWN이다. 과거 B9 HTML 검증 BLOCKED는 이 새 직접 구현 요청으로 성공 처리하지 않는다.

## 범위와 완료 기준

- 생성/참고 배경 생성, 다중 제품 사진 편집, 투명 PNG 생성형 누끼를 OpenAI 직접 Images API에 매핑한다. 누끼 결과는 원본 픽셀·라벨 보존의 보증이 아니며 항상 수동 확인 대상으로 표시한다.
- 기존 CutoutRequest/BackgroundRequest/해시/로컬 실험은 보존한다. 새 kind=api_image는 한 장의 평면 또는 RGBA 결과를 반환하며 기존 3레이어 결과인 것처럼 포장하지 않는다.
- 키 없는 기본 실행은 외부 요청 전 차단한다. 명시적 httpx.MockTransport 주입을 사용하는 오프라인 시험만 수행한다. 실제 키 조회·모델 목록 API·이미지 API 호출·배포는 하지 않는다.
- 프로젝트 자산 권한·영속 내부 job·외부 시도/예약액·안전한 결과 저장·오류·재시작 중복 제출 차단을 연결한다.
- 카피/최종 이미지 사용자 검토 UI, 최종 한글 렌더러, 팀 인수는 이 하위 래퍼의 완료 범위가 아니다. 내부 인증된 모델 실행 API이며 서비스의 사용자 검토를 증명하지 않는다.

## 요청/응답

contracts.py에 ExternalImageRequest, ExternalImageResult, ImageProviderUsage를 추가하고 기존 ImageRequest/ImageResult union을 확장한다.

ExternalImageRequest:
- kind=api_image, operation=generate|edit|cutout.
- pipeline_profile_id=openai-images, pipeline_revision=1.
- model=gpt-image-2.5-sunburst-2026-09-08 (기본) 또는 gpt-image-2-2026-04-21.
- product_images: AssetRef[] 0~5, reference_images: AssetRef[] 0~5, 순서 유지·중복 asset 거절.
- prompt: ImagePrompt|null. generate/edit에는 필수. cutout에는 null(고정 래퍼 프롬프트 사용).
- width=1152, height=1440 기본. 각 16배수, 655360~3686400 pixels, 각 변 최대3840, 종횡비 1:3~3:1. 서버 운영상 2K 초과 제외.
- quality=low|medium|high, 기본 medium. seed/임의 URL/추가 옵션은 거절.
- generate: product_images 없음, reference_images 선택. edit: product_images1~5. cutout: product_images 정확히1, reference_images 없음.
- 영문 negative 필드는 별도 API 옵션이 아니라 명시적인 Avoid 문장으로 프롬프트에 포함한다. 조용히 버리지 않는다.

ExternalImageResult:
- kind=api_image, operation, image:AssetRef, provider=openai, model, attempt_id:UUID.
- provider_request_id: 제한된 ASCII 식별자|null, usage:ImageProviderUsage|null.
- needs_manual_check=true, warnings: 안정 코드 목록. cutout에는 GENERATIVE_CUTOUT_REQUIRES_REVIEW 포함.
- usage에는 입력/출력/합계 및 이미지·텍스트 입력 토큰의 관찰값만 기록한다. 없는 usage는 null. 실제 청구액을 추측하지 않는다.

예: operation=cutout + product_images=[업로드자산] → 투명 PNG image 자산, 생성형누끼 검토 경고.
예: operation=edit + 제품2장 + 배경1장 + 검토된영문프롬프트 →4:5 image 자산1개.
예: live_calls=false, 실제 전송 transport 없음 → PROVIDER_CALLS_DISABLED, 외부 요청0회.

## 모듈 및 호출 계약

- openai_images.py: OpenAIImagesConfig(live_calls=False, api_key 비공개, timeout_seconds), OpenAIImagesAdapter(config, transport=None).
- adapter.validate_request(request), adapter.execute(request, product_images:list[bytes], reference_images:list[bytes], *, client_request_id:UUID) → ProviderImageResponse(image_bytes, provider_request_id, usage).
- 전송은 고정 https://api.openai.com/v1만, redirect/환경 proxy/자동 POST retry 없음. generate+참조0은 generations JSON, 나머지는 edits multipart image[]; n=1, PNG, quality/size 명시. cutout은 background=transparent.
- ProviderImageResponse는 자산 저장 전 제한된 바이트와 정규화 메타데이터다. URL-only 응답은 거절하고 다운로더로 우회하지 않는다.
- api_image_processor.py: ExternalImageProcessor(assets, adapter, attempts, budget_limit_usd, reservation_usd). process_job(request, project_id, job_id) → ExternalImageResult. 모든 참조를 권한/hash 검증 후 제출 예약. decode/크기/alpha 확인 후 자산 저장.
- provider_attempts.py: SQLite 외부 시도 기록. job_id/project/request_hash/attempt_id/상태/예약액/request_id/usage/결과asset/오류만 저장. 프롬프트·키·이미지본문 미저장.
- inference runtime의 router는 api_image만 이 processor로 전달한다. worker는 process_job을 이용해 내부 job ID를 전달한다. 기존 processor callable은 보존한다.
- GET /internal/v1/jobs/{job_id}/provider: 프로젝트 범위 외부 시도 receipt. query만 가능, 재제출/과금 조정 실행 API는 제공하지 않는다.

## 중복 제출·예산·실패

- 외부 POST 전에 SQLite write transaction으로 같은 job의 1회 attempt와 예약액을 영속 저장한다. 예약 실패/예산 초과/기능 비활성/입력 실패는 전송0회.
- 전송 후 transport timeout/5xx/응답 유실은 PROVIDER_SUBMISSION_UNKNOWN. 자동 POST 재시도 없음. 같은 job의 재호출과 프로세스 재시작도 기록을 보고 새 전송하지 않는다.
- 외부 직접 Images에는 영속 job 조회·과금 멱등성 보장을 가정하지 않는다. 내부 same-ID는 우리 재전송을 막는 것이며 제공자의 exactly-once 보증이 아니다.
- 결과 검증/저장 실패도 예약을 무료로 정산하지 않는다. 같은 요청 재생성 대신 확인 필요로 남긴다.
- budget_limit_usd는 서버가 지정한 이미지 한도, reservation_usd는 요청당 운영 예약액이다. 이 값이 실제 가격의 상한임을 보장하지 않는다. 시작 기본 한도0. 매 전송 후 예약은 유지한다. 실제 과금과 다를 수 있으며 다혜 LLM의 별도 사용까지 통제하지 않는다. 운영 정산/예산 증액·실계정 시험은 후속.
- 인증/쿼터/속도제한/정책거절/잘못된응답은 안정 코드로 분리하고 본문/키/URL/프롬프트를 오류 문자열에 노출하지 않는다.
- 강제 중단된 내부 running job은 기존 interrupted 처리, 외부 submitting 예약은 불확실·확인필요로 읽는다. 자동 재실행하지 않는다.

## 검증과 릴레이

B10: 계약/모의 전송/내부 job·자산·시도·오프라인 CLI 통합. 정상generate/edit/cutout·최대입력·거절·429/쿼터·timeout·잘못된base64/크기/alpha·다른project·동일job·예산경합·저장실패·재시작을 모의 응답으로 검증한다. 전체 기존 회귀와 패키지/설정/secret 제외 검사 후 일반push와원격readback, 로컬beautyAi동기화.
B11 후보: 검증에서 발견된 안전성·계약·오프라인 연동 GAP만 이어 처리. 실호출·실품질·UI/팀인수는 현재 범위 밖이며 실제호출 금지로 NOT_RUN.

근거: [OpenAI 편집](https://developers.openai.com/api/reference/python/resources/images/methods/edit), [이미지 가이드](https://developers.openai.com/api/docs/guides/image-generation), [Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst). 2026-10-22 확인. 모델 지원 문서와 실제 팀 계정 권한은 별개다.
