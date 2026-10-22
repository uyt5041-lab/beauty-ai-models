# 이미지 구현 릴레이 계약

2026-10-22 · 요청: plan/models TODO를 릴레이샷으로 구현한다. 날짜는 마일스톤이며 완료 가능한 작업을 앞당긴다.

## 목표와 현재 상태

기존 클라이언트·계약에서 실제 자산/작업 실행으로 이어지는 작은 실행 단위를 만든다. 팀 합의, GPU 실측, 사람의 제품 승인과 로컬 검증을 구분한다.

## 배치 1 · 자산 입력과 제품 캔버스

- `LocalAssetStore(root, max_upload_bytes=20 MiB, max_pixels=16,000,000)`.
- `put_upload(data: bytes, *, project_id: UUID) -> AssetRef`: PNG/JPEG/WebP만 실제 decode, animation/빈 파일/과대 입력 거절. EXIF 회전 적용, RGB/RGBA로 변환, 메타데이터를 제거해 PNG로 저장한다.
- `put_image(image: PIL.Image, *, project_id: UUID) -> AssetRef`: 내부 결과 PNG 저장. 임시 파일→fsync→rename 후 SQLite 자산 메타데이터 commit. commit 전에는 참조를 반환하지 않는다.
- `read(ref: AssetRef, *, project_id: UUID) -> PIL.Image`: DB의 project/ID/전체 메타데이터와 실제 파일 hash/크기/형식을 검증한다. 외부 경로나 URL을 받지 않는다.
- `get_ref(asset_id: UUID, *, project_id: UUID) -> AssetRef`: 프로젝트 경계 밖은 미존재와 같은 오류. 인증된 project_id를 주는 호출자는 서비스 계층이다.
- 오류는 `GatewayError`의 안정적인 코드로 반환하며 원본 파일/키를 메시지에 노출하지 않는다.
- `prepare_product_canvas(product_rgba, visual) -> PreparedProduct`: alpha bounds를 crop하고 종횡비 유지 fit 후 중앙 배치한다. 제품 영역은 left/top ceil, right/bottom floor로 안쪽 반올림한다. text safe zone은 바깥쪽 반올림해 보수적으로 보호한다.
- `PreparedProduct.product_rgba`, `preserve_mask`는 같은 캔버스. 보호 마스크는 alpha>0을 흰색으로 표시한다. resize/배치 결과는 **승인 후보**이며 자동 승인하지 않는다.
- `inspect_composite(preview, approved_rgba) -> IntegrityReport`: alpha=255 RGB 동일성, 비교 픽셀 수, partial-alpha 유무. 픽셀 검사는 이미지 전체/유리/광고 품질 승인이 아니다.
- 테스트: 프로젝트 교차 접근, 파일 변조/메타데이터 위조, 과대/깨짐/애니메이션/EXIF, 저장 후 재개, 비율/영역/마스크/불투명 픽셀, 부분 투명과 빈 alpha.

## 다음 연결 단위

2. SQLite 영속 job ledger와 internal HTTP API. 동일 ID/hash 원자적 등록, 권한/조회, queued 복구, 단일 호스트 실행 잠금과 실제 실행 종료 확인 후 interrupted.
3. 서버 profile, 명시적인 제품 승인, 실행 adapter, 레이어 저장/검증, 실패 복구. 실제 모델 adapter는 승인된 환경/모델이 확인되면 연결·검증한다.
4. 버전/영어 프롬프트 입력, 편집 revision·프로젝트 캐시·예산 경계, 고정 평가 및 인계 도구. 미합의 계약은 구현 선택과 팀 수락을 분리한다.

## 입출력 예시와 영향

- 깨진 PNG → `INVALID_IMAGE`; 너무 큰 업로드 → `IMAGE_TOO_LARGE`.
- 프로젝트 A의 자산을 B에서 조회 → `ASSET_NOT_FOUND`.
- 파일 내용이 AssetRef hash와 다름 → `ASSET_INTEGRITY_ERROR`.
- 누끼 배치 → 같은 크기 RGBA+보호 마스크, 사용자/서비스의 별도 승인 필요.
- 모델 호출, 외부 다운로드, 운영 배포를 배치 1에서 실행하지 않는다. 기존 wire schema는 유지한다.

## 인수와 반영

각 배치는 단위/파일·DB 통합/해당 API smoke를 수행하고 실제 결과를 기록한다. 원격은 일반 push와 SHA readback으로 검증한다. PR을 만들지 않는다. 공용 배포 대상이 정해지지 않은 라이브러리/로컬 서버 코드의 publication은 Git commit으로 증명하며 운영 배포는 별도 미수행으로 표시한다.

## 미확정

실제 GPU 접근/예산/모델, 팀 승인자, 최종 사용자 인증·렌더러, Safe Zone·프롬프트 팀 계약은 확인되지 않았다. 로컬 구현 완료로 이 항목을 체크하지 않는다.

## 배치 2 · 영속 job/API/단일 호스트 worker

- SQLite `JobLedger(path)`: `submit(job_id, project_id, request) -> ImageJob`, `get(job_id, project_id) -> ImageJob`, `claim_next(owner_id) -> ClaimedJob | None`, `finish(job_id, owner_id, result=None, error_code=None) -> ImageJob`, `recover_interrupted() -> int`.
- `ClaimedJob`: `job: ImageJob`, `project_id: UUID`, `request: ImageRequest`. 요청 스냅샷을 DB에 저장한다.
- `submit`은 동일 project/ID/hash면 기존 상태를 반환한다. 타 프로젝트 ID는 `IMAGE_JOB_NOT_FOUND`, 동일 project/ID·다른 hash는 `IDEMPOTENCY_CONFLICT`. `BEGIN IMMEDIATE`와 PK로 동시 등록/claim을 직렬화한다.
- 상태: queued → running → succeeded/failed. 종료가 확인된 이전 worker의 running만 interrupted. terminal은 다시 덮어쓰지 않는다. queued는 DB 검색으로 복구하며 인메모리 큐가 기준이 아니다.
- `finish`는 현재 running의 owner가 일치해야 한다. result kind가 request kind와 맞는지 검사한다. 결과 파일 검증은 worker가 수행한 후 finish를 호출한다. 실패는 안정적인 error code만 저장한다.
- `SingleHostWorker(ledger, assets, processor, *, poll_seconds=0.1)`: `start() -> bool`, `stop(timeout=None) -> bool`, `is_running` property. processor는 `(request, project_id) -> ImageResult` 동기 callable.
- worker는 ledger DB 경로 옆의 POSIX flock을 실행 전체 동안 보유한다. 이미 잠긴 경우 start=False이며 다른 프로세스의 running을 조정하지 않는다. 잠금 획득 후에만 interrupted 복구, DB queued claim을 실행한다.
- 종료 요청은 현재 processor가 끝날 때까지 잠금을 유지한다. join timeout은 실행 실패·중단의 증거가 아니며 잠금을 해제하지 않는다. worker 종료 후 OS가 잠금을 회수한 경우 다음 owner가 복구한다. 한 호스트/공유 로컬 FS만 지원한다. 다중 VM은 별도 조정기 필요.
- 결과 AssetRef를 project 범위에서 실제 read/검증한 뒤 success. 저장 실패/잘못된 타입/processor 오류/OOM은 failed, 비밀정보 없는 code를 기록. 임의 예외 문자열은 API에 돌려주지 않는다.
- FastAPI `create_app(ledger, assets, credentials, processor=None)`; credentials는 서버의 Bearer token→project UUID 매핑. 토큰으로 project를 결정하며 사용자가 body/header에서 project를 지정해 권한을 바꾸지 못한다.
- PUT/GET `/internal/v1/jobs/{job_id}`는 기존 HTTPImageGateway wire 계약을 유지. 요청 자산의 파일/권한을 확인한 뒤 ledger에 commit하여 202. 동일 요청 재전송도 같은 job. GET은 200, 미존재/다른 프로젝트 404, hash 충돌 409, 인증 실패 401, 형식 422, 자산 오류 400.
- POST `/internal/v1/assets`: 최대 업로드 바이트까지만 stream으로 읽고 store에 저장하여 201 AssetRef. GET `/internal/v1/assets/{asset_id}`는 인증된 project에서 검증된 PNG bytes 반환.
- 응답 validation 오류는 유형만 공개한다. 위치에도 임의 사용자 키가 들어갈 수 있어 제외하며 입력값/토큰을 echo하지 않는다. default 앱은 외부 포트를 열지 않으며 테스트에서 명시적으로 loopback smoke를 수행한다.
- tests: 실제 SQLite 동시 프로세스, 재개/claim/잘못된 owner, POSIX lock 경쟁·실행 중 stop timeout·crash 복구·result tamper, 인증·project scope·PUT idempotency·wait timeout. 테스트 processor는 모델 품질 증거가 아니다.

## 배치 3 · 승인 저장과 제한된 실행 프로필

- 기존 CutoutRequest/BackgroundRequest/ImageJob의 wire/hash는 바꾸지 않는다. 새 준비/승인 API는 독립 DTO다.
- `ProductApprovalStore(path, assets)`: `prepare(project_id, source, cutout, visual) -> ProductCandidate`, `get(candidate_id, project_id)`, `approve(candidate_id, project_id, candidate_hash, *, confirmed: bool)`, `require_approved(request: BackgroundRequest, project_id) -> ProductCandidate`.
- ProductCandidate: candidate_id UUID, candidate_hash, source AssetRef, product_rgba AssetRef, preserve_mask AssetRef, visual VisualBrief, approved bool. 준비 시 실제 source/cutout을 project에서 read하고 prepare_product_canvas로 생성/저장한다. candidate_hash는 source/product/mask/visual만 hash. UUID/승인시각은 제외.
- SQLite 저장·조회 시 snapshot hash 확인. prepare는 항상 approved=False. approve는 confirmed=True와 현재 candidate_hash 일치·자산 실제검증을 요구한다. wrong project는 미존재, hash불일치는409, 미승인은409. 같은 승인 재전송은 같은결과. 승인 행은 token을 가진 호스트가 사용자 확인을 전달했다는 기록이며 실제 사람 신원을 인증했다는 뜻은 아니다. UI는 별도.
- require_approved는 request의 source/product/mask/visual fingerprint와 승인된 row를 정확히 매칭하고 파일·alpha·binary mask 일치·Safe Zone/제품범위를 검증한다. 승인 뒤 새 visual/자산이면 재승인 필요. seed/profile 변경은 제품 승인 자체를 변경하지 않으며 별도 profile 검증한다.
- `LocalTemplateProcessor(assets, approvals)`와 `validate_request(request)` 지원. profile `uploaded-alpha` revision `1`은 이미 투명 alpha가 있는 RGBA만 수신하고 binary keep mask를 만든다. RGB/완전불투명 입력은 MODEL_UNAVAILABLE. 누끼 모델인 척하지 않으며 항상 needs_manual_check=True.
- 배경 profile `bright-template` revision `1`: 검증된 로컬 작업 크기256~1024,8의배수. server-owned style/layout allowlist와 version을 고정하고 외부 모델경로·실행코드를 받지 않는다. template은 텍스트를 굽지 않고 seed로 결정되는 밝은색 장면을 만든다. 실제 AI생성·그림자품질은 미검증.
- processor는 승인→파일/geometry→고정프로필 확인 후 scene_underlay/product_rgba/composed_preview를 저장한다. approved foreground의 opaque pixels 검사. 부분투명·template 한계를 warnings로 남기고 자동 품질승인하지 않는다.
- API: POST `/internal/v1/products/prepare` {source,cutout,visual}, GET `/internal/v1/products/{candidate_id}`, PUT `/internal/v1/products/{candidate_id}/approval` {candidate_hash,confirmed:true}. project는 기존 Bearer 매핑에서 결정. 기존 job endpoint는 등록 전 runtime validator, worker는 실행 직전 동일 조건을 재확인한다.
- CLI `python -m beauty_ai.inference_runtime`: 환경변수의 UUID/token/storage root로 조립. 기본127.0.0.1, 명시적설정으로만 포트변경. API키/GCP정보는 코드·예제에 쓰지 않는다.
- 예: candidate 준비→미승인 background409→호스트가 실제확인 후 approve→same background접수→template3layers조회. 다른visual이면409. 모델이 필요한 RGB입력은 지원불가코드.

## 배치 4 · 프롬프트 수신·편집 revision·프로젝트 캐시

- 영어 프롬프트 생성 담당은 텍스트 팀원이다. 이미지 모듈은 `ImagePrompt(positive, negative='', prompt_version, source_revision, language='en')`만 수신한다. positive1~4000문자, negative최대2000문자, 버전1~80문자, revision>=1. language는 발신자의 선언이며 언어 판별 결과가 아니다. 실제모델 tokenizer 한도는 별도 adapter 검증. 무음 절단하지 않는다.
- `BackgroundRequest.image_prompt: ImagePrompt | None = None` 추가. None이면 직렬화에서 이 새필드만 생략하여 기존 body/해시/DB snapshot을 유지한다. 실제 prompt가 있으면 텍스트·버전·revision 모두 content_hash에 포함한다. content_hash 공통 함수는 변경하지 않는다.
- local bright-template은 prompt를 해석하지 않으므로 non-None image_prompt는 `INVALID_IMAGE_REQUEST`로 거절한다. 수신/해시/ledger호환 검증은 합성 fixture로 하고 실모델을 호출했다고 주장하지 않는다.
- `ImageEditStore(path, assets, ledger)`: `select(campaign_id, project_id, expected_revision, request: BackgroundRequest, copy_revision) -> ImageSelection`; `get(campaign_id, project_id)`; `attach(campaign_id, project_id, expected_revision, job_id) -> ImageSelection`.
- `ImageSelection`: campaign_id UUID, revision>=1, copy_revision>=1, request_hash, request BackgroundRequest, active_job_id UUID|None. 프로젝트는 호출자의 신뢰된 컨텍스트. 사용자 문구 자체·검수결과는 이 저장소에서 수정/삭제하지 않는다.
- 최초select는 expected_revision=0, 이후 현재 revision 일치 시만 +1. SQLite BEGIN IMMEDIATE로 CAS. copy_revision 역행 거절. image request가같고 copy_revision만 변하면 project+request_hash의 성공결과 cache를 실제파일검증 후 재사용한다. visual/seed/profile/자산/prompt변경은 다른 key다.
- attach는 같은 project의 ledger succeeded BackgroundResult만 허용하며 현재 selection revision/request_hash와 일치해야 한다. 결과파일과 layer참조를 검증한 후 active_job/cache를 원자적으로 기록한다. queued/failed/interrupted·타project·다른input·늦은revision은 현재선택을 바꾸지 않는다.
- cache는 project마다 분리하고 실파일이 사라지거나 hash가틀리면 hit로쓰지 않고 해당cache를 제거하여 active_job_id=None으로 반환한다. 저장소 장애는 miss로숨기지 않고 error. get은 저장메타데이터이며 최종다운로드/내보내기 전 실제자산검증은 유지한다.
- 오류 EDIT_NOT_FOUND404, EDIT_CONFLICT409, IMAGE_JOB_NOT_READY409, EDIT_STORAGE_ERROR503; 입력형상오류 INVALID_IMAGE_REQUEST400. 같은revision을놓고2개편집이면1개만승인. 자동 retry/새GPU작업은 수행하지 않는다.
- API PUT `/internal/v1/edits/{campaign_id}` {expected_revision,copy_revision,request}; GET samepath; PUT `/internal/v1/edits/{campaign_id}/result` {expected_revision,job_id}. tokenproject에서만 접근. select 등록 전에 profile/제품승인/파일검증, attach는 ledger권위/실파일을 사용한다.
- 예: revision1/jobA→문구수정revision2같은imagehash는cachedjobA. 제품변경revision3다른hash→늦은jobA attach(revision1)409. 이미지실패가문구내용/검수DB를지우지않는다. 실제CampaignService/UI연동은별도이며팀수락으로표시하지않는다.

## 배치 5 · 평가 도구와 시각 기준

- 로컬 파일을 측정하는 도구이며 모델을 호출하거나 품질 합격을 자동 승인하지 않는다. 사용 가능한 실제 평가셋/모델/사람 기준은 별도다.
- `mask_metrics(predicted, reference, *, threshold=128, tolerance=2) -> MaskMetrics` in evaluation_metrics.py. 같은크기 grayscale마스크를 white=foreground로 비교. threshold1..255, tolerance0..8픽셀. IoU=교집합/합집합; 빈합집합은None. 경계는 바깥0패딩 후3x3erosion 차이, tolerance의 정사각 팽창으로 경계precision/recall/F1. 둘다빈경계는None, 한쪽만빈경계는F1=0. 값·정의·조건을 함께기록하며학술벤치마크동일성주장없음.
- `MaskMetrics` 필드: iou, boundary_precision, boundary_recall, boundary_f1(float|None), threshold, tolerance. 불투명 픽셀은 기존 inspect_composite로 비교하되0개면검증불가다.
- `evaluate_manifest(path) -> dict` in evaluation.py, CLI `python -m beauty_ai.evaluation <manifest.json> --output outputs/evaluation-report.json`. manifest최대2MiB, cases1..200. 입력이미지당20MiB/16Mpixel한도. 파일path는manifest폴더아래상대경로만, escape/symlink외부/URL거절, 네트워크호출없음.
- manifest: dataset_id, version, cases[{case_id, split(dev|holdout), product_type, rights_status(unknown|declared_authorized|synthetic_fixture), rights_source, product_rgba, composed_preview, predicted_mask(optional), reference_mask(optional), human_review(not_assessed|pass|fail, default not_assessed)}]. ID중복거절, 미확인권리는파일을측정하지않고NOT_ASSESSED. rights/human은제공자의선언이고법적권리·실제사람신원확인아님.
- output: dataset/version, evaluation_environment, model_invoked=false, quality_approval=NOT_ASSESSED, cases의 MEASURED/NOT_ASSESSED/ERROR, 입력파일sha256·크기, opaque/pixel/partialalpha계수, maskmetrics또는None/사유, 선언된human_review. 정답mask가없으면GTmetric은None. 실제모델품질·VRAM·가격은None/NOT_MEASURED. 민감경로/원본텍스트를예외로그에출력하지않음. 예제manifest는가상경로/권리unknown, 실제이미지와보고서는Git제외outputs/data에둔다.
- `JobLedger.timing(job_id, project_id) -> JobTiming`: 저장된created_at/updated_at와terminalstatus로ledger_elapsed_ms를제공. queued/running 또는음수clock차이는None. 이는서버wallclock의접수기록→terminal기록간경과이며대기/처리/저장을포함, GPU연산시간·VRAM·비용과같지않다. 과거상태에서시작phase를추정하지않는다.
- API GET `/internal/v1/jobs/{job_id}/timing`, CLI `examples.run_image get-timing <job_id>`. project인증유지. 품질case와job의결합을자동추정하지않고명시적동일이미지증거가없는경우별도기록으로취급한다.

## 배치 6 · 로컬 실누끼 모델과 이식 경계

- 사용자 방향: 로컬에서 먼저 실행하고 이후 GCP로 옮긴다. 현재 Mac의 MPS를 검증하고 CUDA는 설정 경계만 준비한다. CUDA 실행 성공이나 두 장치의 픽셀 일치를 추정하지 않는다.
- 고정 후보: `ZhengPeng7/BiRefNet_lite`, revision `aa62cd87eafb9cc43056d08ef3615a14628b831d`, MIT 모델 카드. upstream source/config를 검토하고 개별 SHA256·가중치 LFS SHA256/크기를 고정한다. 준비 명령만 다운로드하며 API 요청에서 다운로드하거나 임의 repo/path/code를 지정할 수 없다.
- `beauty_ai.model_store`: 고정 allowlist 파일의 준비·검증. 모델 루트는 서버 설정이며 Git에서 제외하는 models/ 사용. 파일은 크기·SHA 검증 뒤 원자적으로 반영, 부분 다운로드를 정상 모델로 인식하지 않는다. 런타임은 검증된 로컬 파일만 로딩한다. `python -m beauty_ai.model_store prepare --root <models>`와 `verify` 지원, 비밀값/임의 예외문자열 출력 없음.
- `BiRefNetCutoutProcessor(assets, model_directory, *, device='mps')`: profile `birefnet-lite`, revision `aa62cd87eafb9cc43056d08ef3615a14628b831d`. validate_request와 __call__(request,project_id) 지원. 명시된 cpu/mps/cuda 장치를 가용성 확인 후 사용하고 자동 CPU 대체로 실패를 숨기지 않는다. 모델·라이브러리는 선택 의존성이다.
- 초기 추론은 단일 이미지, FP32, 1024×1024 정규화 입력, eval/inference_mode. 모델 alpha를 원본 자산 크기로 되돌려 입력 RGB와 합친 RGBA를 저장한다. binary preserve_mask는 alpha>0, needs_manual_check=true와 MODEL_SEGMENTATION_REQUIRES_REVIEW 경고. 빈/불량 출력은 오류이며 제품 식별·사람 품질 합격으로 간주하지 않는다. 다제품/반사/투명 경계는 수동 검수.
- 런타임은 `BEAUTY_CUTOUT_BACKEND=birefnet-lite`, `BEAUTY_MODEL_ROOT`, `BEAUTY_MODEL_DEVICE` 서버 환경으로 선택. 기본 uploaded-alpha/bright-template 동작을 유지한다. 신규 processor만 실누끼를 처리하고 template 배경은 기존 processor로 위임한다. 기존 자산/작업/승인 wire 계약 유지, CLI prepare에 서버가 허용한 cutout profile 선택을 추가한다.
- 검증: 저장파일 변조/누락/허용목록·모델어댑터 출력·기존회귀, 실제 MPS 합성제품 fixture 1개 이상 실행과 actual model/환경/시간 기록. fixture는 제품 사진 품질 평가가 아니다. 로컬 자산·가중치·결과 이미지는 공개 Git 제외. 라이선스 출처와 버전은 공개 문서에 기록.

## 배치 7 · 로컬 SDXL 배경 모델

- 검증 수리: finite·shape·range가 맞더라도 원출력 전체가 정확히0이면 IMAGE_RESULT_INVALID로 거절한다. 임의 밝기·광고 품질 점수 기준을 만들지 않으며 새 자산·성공 결과를 저장하지 않는다.

- 고정 후보 `stabilityai/stable-diffusion-xl-base-1.0`, revision `462165984030d82259a11f4367a4eed129e94a7b`, Diffusers0.41.0의 StableDiffusionXLInpaintPipeline. base4채널UNet 지원을공식소스확인. fp16runtime18파일약6.94GB만준비하고모델카드/라이선스도고정해보관. 추가refiner/전체76.9GBrepo/원격코드없음.
- `sdxl_model_store.py`: MODEL_ID, MODEL_REVISION, model_directory(root), verify_model_directory(path), prepare_model(root), CLI prepare/verify --root models. <root>/sdxl-base/<revision>의고정중첩파일/디렉터리만허용, 경로·symlink·size/SHA검증, 원자적완성폴더반영. 재실행은검증된기존가중치를사용, 요청중다운로드없음.
- `SDXLBackgroundProcessor(assets, approvals, model_directory, *, device='mps')`, validate_request 및call. profile `sdxl-inpaint`, pipeline_revision `sdxl-base-fp16-v1`. cpu/mps/cuda명시, mpsfallback금지. cpu는float32, mps/cuda는float16가중치, VAEforce_upcast=true유지. local_files_only/use_safetensors/variantfp16, custom remote code미사용. CPUgenerator를요청seed로매번생성하고GPU완료동기화후반환.
- 서버profile: bright-ad/1·product-right/1, 각축512/768/1024, 최대1024², EulerDiscreteScheduler고정파일, steps20/guidance7.5/strength0.99/batch1, VAEtiling. 효과적인설정을임의요청으로바꾸지못함. 장치/라이브러리간같은픽셀보장없음.
- ImagePrompt필수. positive/negative둘다두CLIPtokenizer로truncation=False확인, specialtoken포함77이하만통과. 과도한입력은본문echo없이INVALID_IMAGE_REQUEST. 프롬프트는텍스트담당또는명시적호스트입력이며이미지모듈이카피생성/번역하지않음.
- 제품승인/파일/geometry재검증후승인foreground를밝은중립canvas에합성해초기image생성. binary보호마스크반전(흰=재생성)을인페인트에전달. 텍스트SafeZone은mask에서보존처리하고VAE복원후에도같은중립색사각형을다시보장한다. 최종텍스트사용성/그림자품질은사람확인. 모델은제품없는cleanplate를보장하지않음.
- pipeline output_type=np의float배열을finite/shape/range검사하고정규RGB로변환. 생성씬위에승인제품RGBA재합성, opaque픽셀일치확인후3layer저장. 부분투명/생성안전검사없음/품질미승인경고항상반환. SDXL기본pipeline에NSFWchecker가없으므로안전검사완료로표시하지않음.
- 런타임 BEAUTY_BACKGROUND_BACKEND=sdxl-inpaint로선택, BEAUTY_MODEL_ROOT/DEVICE공유. 기본bright-template유지. CLI render/select에 --background-profile과 --prompt <ImagePromptJSON> 추가. sampleprompt는밝은꽃소품장면을위한명시적예제이며LLM생성으로표시하지않음.
- OpenRAIL++-M모델출처/라이선스를보존하고배포자가용도제한·고지를검토하도록기록. Git에는코드/manifest/문서만, 가중치/이미지/팀정보없음. 실제로컬실행·경과시간·메모리관찰을기록하되실제품사진품질·GCP CUDA·팀인수는별도다.

## 배치 8 · 제출 응답 유실 조회·조정

- `HTTPImageGateway.submit_and_wait(request, *, job_id, deadline_seconds=120, poll_seconds=1) -> ImageJob`를 추가한다. 기존 submit/get/wait 의미와 wire/hash는 바꾸지 않는다. 전체 작업을 시작할 때 하나의 monotonic 기한을 만들고 PUT은 한 번만 보낸다.
- PUT의 전송 오류·5xx·408·425·429 또는 응답 대기 초과는 같은 job ID로 GET을 반복해 조정한다. 새 ID 생성·자동 PUT 재전송·취소를 수행하지 않는다. 조회404만으로 미접수를 확정하지 않으며 기한 내 조회한다.
- 응답을 얻으면 기대 입력hash와job ID·result kind를 검사한다. succeeded만결과반환, failed/interrupted는기존오류코드반환. 입력불일치/프로토콜오류/인증거절은기다리며숨기지않는다.
- 기한까지 올바른 job 레코드를 한 번도 확인하지 못하면 SubmissionUncertain을 유지한다. 접수된 job을 확인했으나 완료를 확인하지 못하면 JobStillPending이다. 타임아웃·클라이언트종료를GPU실패나취소로기록하지않는다.
- CLI prepare/render는 출력한 ID를 유지하며 새 함수를 사용한다. 사용자가 계속 조회할 수 있도록 ID와 안전한오류코드를남기고prompt/토큰/서버원문은출력하지않는다.
- 검증 예: 서버가 PUT을commit한뒤클라이언트가응답을잃어도같은ID조회→같은결과, PUT1회·processor1회. GET404/일시오류가계속되면재생성없이불확실상태로종료. 실제GPU/운영프록시장애검증은별도다.

## 배치 9 · 작업 실행 관찰

- `execution_trace.py`는 worker thread의 ContextVar recorder와 유한 단계명을 제공한다. 관찰이 없는 직접 processor 호출은 기존처럼 동작한다. 입력 prompt·자산경로/URL·토큰·원문예외를 수집하지 않는다.
- 단계명은 model_load, preprocess, model_inference, composite, asset_store, result_validation. 각 elapsed_ms는 worker monotonic wall-clock이고 outcome은 completed/failed다. processor 전체 경과와 단계값을 구분하며 순수 GPU 시간·합산 가능한 독립 시간을 자동 주장하지 않는다.
- 서버 지정 요청 장치, 모델 재사용 여부·고정 모델 revision, 관찰 가능한 Python/패키지 버전, adapter 장치 동기화 결과를 기록한다. 설정 장치와 실제 호출/검증 완료를 혼동하지 않는다. MPS allocated/driver 및 CUDA allocated/reserved는 작업 후 process snapshot이며 peak·요청별 증분·비용이 아니다. 없는 값은 null과 사유로 남긴다.
- `JobLedger.finish(..., execution=...)`는 성공/실패 상태와 관찰을 같은 DB transaction에 저장한다. 기존 jobs 테이블/요청hash/이미지결과는 유지하고 별도 job_executions 테이블을 만든다. 기존/강제중단 작업에 없는 관찰을 소급 생성하지 않는다. 프로젝트·요청hash·현재 job상태와 함께 검증해 읽는다.
- `GET /internal/v1/jobs/{job_id}/execution`과 CLI get-execution을 추가한다. 인증/프로젝트 범위는 기존job조회와 같다. 미기록은 NOT_RECORDED와execution=null이며0초나성공으로표시하지않는다. 손상된 관찰은 저장 오류이며 원문을 노출하지 않는다. 기존 timing endpoint는 그대로다.
- 큐 경과는 실제 claim 시각과 created 시각을 얻을 수 있을 때만 server wall-clock으로 기록하고, 역행/기존자료부족이면 null로 둔다. 단계측정에 UTC시각차를 섞지 않는다.
- 실제 검증은 권리 있는 합성 fixture로 동일512입력/seed 반복과768/1024·직사각 일부를 제한적으로 수행한다. 새job ID마다 실제실행과입력hash/결과hash/phase/경고를연결한다. 허용크기전체의광고품질·메모리상한·다른장치픽셀일치를주장하지않는다.
