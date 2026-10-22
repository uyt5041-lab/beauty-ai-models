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
