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
