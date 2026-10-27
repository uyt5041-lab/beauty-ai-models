# 백엔드 연결 — 모델팀 구현 계약

2026-10-27. 기준: [10/26 회의](https://app.notion.com/p/2026-10-26-405b13f77dbd80eda3c8c2570a2cb82b), 본레포 계약 v0.2 / dev `0b5bb47`. 이 문서는 Python 연결 API다. 미확정 팀 HTTP를 대신 확정하지 않는다.

## 범위와 순서

1. 한국어 ImagePrompt 수신. 원문·개행·과거 영어 해시 보존.
2. BackendImageAdapter: 한국어 prompt/concept/mood, 구조화 카피, 승인 revision/hash, 사진 바이트 → 기존 ExternalImageProcessor → 이미지 바이트/실행 기록.
3. resize_ad_png: 원본 바이트 → 비율을 보존한 1080×1350 PNG. 새 이미지 API 호출 없음.

각 단계는 테스트·기록·일반 push 확인 후 다음 단계로 넘어간다. 실패는 해당 단계에서 수리하고 재시험한다. 이 범위가 끝나면 종료한다. 백엔드의 실제 라우트·승인·팀 예산 원장은 서빙 담당의 남은 일이다.

## 책임과 입출력

백엔드가 사용자/자산 소유권, 검수 통과, 현재 승인 revision/hash, 팀 예산을 확인한다. 모델팀은 전달받은 승인 메타데이터를 보존하며, 이 값 자체를 승인 증거라고 판정하지 않는다. 모델 API를 사용자에게 직접 공개하지 않는다.

이미지 입력 예: `job_id=service-job-001`, `copy_revision=1`, `copy_hash=<백엔드 승인 해시>`, `image_prompt={concept: 간결한 루틴, prompt: 밝은 배경에 원본 제품을 배치한다, mood: [청량]}`, `ad_copy={headline: 오늘의 루틴, subline: 나를 위한 시간, cta: 제품 살펴보기}`, 제품 바이트 2~5개, 참고 바이트 0~5개. 전체 스키마와 실행 예제는 구현 단계에서 확정한다.

Langfuse/W&B, 모델·품질·프롬프트 버전은 모델 내부 설정이다. 백엔드에 SDK나 계정을 요구하지 않는다. 반환하는 기본 실행 기록은 provider/model/attempt/request ID·사용량·소요 시간이다. 실제 청구액은 확인하지 못하면 null이다.

같은 project+job의 입력·설정이 같으면 고정된 내부 요청과 결과를 재사용한다. 입력이 달라지면 충돌로 거절한다. 서비스 job ID는 승인한 후보마다 고유해야 한다. 실패/응답 불명확 기록이 있으면 자동 재호출하지 않는다. 기존 ProviderAttemptStore를 우회하지 않는다.

## 리사이즈 품질 계약

축소는 픽셀 수를 줄이므로 정보 무손실이라고 부르지 않는다. 원본은 유지한다. 크롭·늘이기·자동 확대는 금지한다. 4:5 입력만 받으며 LANCZOS로 1회 축소하고 PNG로 저장한다. 같은 크기는 검증 후 원본 바이트를 반환한다. 색 프로파일·투명도·EXIF 방향을 처리한다. 모르는 색 형식이나 손상 파일은 조용히 변환하지 않고 거절한다.

1152×1440 → 1080×1350 축소는 0.9375배다. 최종 PNG의 실제 치수·MIME·SHA를 반환한다. 한글 합성·텍스트 실험·실서비스 E2E는 이번 리사이즈 완료와 별개다.

## 검증

한국어 multipart 원문, 과거 영어 해시, 정상 2+0/5+5, 사진 수·순서·손상, 재시작/동시 중복 호출, 변경 입력 충돌, timeout 재호출 방지, 기본 live 차단, 원본 보존·비율·색상·투명도·PNG·잘못된 프로파일·잘못된 크기를 오프라인에서 확인한다. 유료 호출과 외부 관측 업로드는 하지 않는다.


## K2 Python 사용 예

```python
from uuid import UUID
from beauty_ai.backend_adapter import BackendImageAdapter, BackendImageInput
from beauty_ai.assets import LocalAssetStore
from beauty_ai.api_image_processor import ExternalImageProcessor
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from beauty_ai.provider_attempts import ProviderAttemptStore

# 실 호출은 기본 차단. 서버가 팀 예산/승인을 확인한 뒤 별도 설정한다.
assets = LocalAssetStore("outputs/backend/assets")
processor = ExternalImageProcessor(
    assets, OpenAIImagesAdapter(OpenAIImagesConfig(timeout_seconds=300)),
    ProviderAttemptStore("outputs/backend/attempts.sqlite3"),
    budget_limit_usd="0", reservation_usd="1",
)
adapter = BackendImageAdapter(processor, "outputs/backend/bindings.sqlite3")
request = BackendImageInput(
    job_id="service-job-001", copy_revision=1, copy_hash=approved_copy_hash,
    image_prompt={"concept": "간결한 루틴", "prompt": "밝은 배경에 원본 제품을 배치한다.", "mood": ["청량"]},
    ad_copy={"headline": "오늘의 루틴", "subline": "나를 위한 시간", "cta": "제품 살펴보기"},
    product_images=[front_bytes, side_bytes], reference_images=[],
)
result = adapter.generate(request, project_id=server_project_uuid)
# result.image_bytes / width / height / sha256 / receipt
# 실패 후 조회는 호출하지 않는다.
receipt = adapter.get_receipt(request.job_id, project_id=server_project_uuid)
```

함수 인자는 Python bytes다. 이 스키마를 HTTP JSON의 확정 형식으로 쓰지 않는다. 옵션 `safe_zone`은 final_canvas_normalized의 left/top/right/bottom이며 5% 여백 안의 양수 영역이어야 한다. 이미지가 실제로 이 영역을 지켰는지는 사람 검토가 필요하다.

`elapsed_ms`는 영속 모델 시도 기록의 시작/종료 시각 차이다. 재호출 시 캐시 조회 시간을 생성 시간으로 덮어쓰지 않는다. 불명확/진행 중은 null이다. `reservation_usd`는 비용 예약이며 청구액이 아니다. 모델 사용량이 없으면 usage=null이다.

적용 제한: 기본 AssetStore는 20MiB/사진·16MP, 제공자 전송은 기본 100MiB 합계다. 백엔드가 큰 사진을 줄여 보내야 한다. project UUID는 인증된 서버가 결정한다. 같은 job을 바꿔 보내면 BACKEND_INPUT_CONFLICT, 이미 호출 중/실패한 시도는 PROVIDER_ATTEMPT_CONFLICT로 종료하며 자동 재전송하지 않는다.
