# 2트랙 이미지 모듈

2026-11-05 · 이미지 Python 경계 구현. 이미지 브랜치에 반영하는 기능이다. 외부 HTTP·서빙 배치·팀 합의·배포/인수 완료를 뜻하지 않는다. 기존 단일 합성 경로를 유지한다.

## 완료 범위

- [x] 모드·승인 전체 카피 결속·분석 실패 선차단·legacy 입력/hash 호환.
- [x] 자유형/합성형 프롬프트 분리. 후보당 n=1·기존 호출 원장/예약·성공 재사용·미확인 재호출 금지 유지.
- [x] 자유형 resize-only와 합성형 한글 폰트 export. 네트워크 차단 전체432개 시험 통과(기존395+신규37).

## 호출 방법

기존 BackendImageAdapter와 BackendImageInput을 그대로 쓴다. `generation_mode`를 생략하면 기존 무문구 후보 생성 경로다. 신규 명시값은 `composed`/`freeform` 두 가지다. `copy_output`은 전체 AdCopyOutput(plan/ad_copy/caption/hashtags 포함)이며 `content_hash(copy_output)`이 `copy_hash`와 같아야 한다. 카피/본문/태그 필드도 전달값과 일치해야 한다. 실제 승인 여부와 소유 프로젝트는 서빙에서 먼저 확인한다.

```python
from src.image_generation import BackendImageInput, export_candidate, make_placement_layout
from src.image_generation.contracts import content_hash

# adapter, project_id, analysis, product_photos, approved_output은 서버가 제공한다.
# 모든 후보에 동일한 승인 snapshot을 사용하며 job_id만 후보별로 다르게 고정한다.
body = BackendImageInput(
    job_id="batch42-free-1", copy_revision="copy-v3",
    generation_mode="freeform", copy_output=approved_output,
    copy_hash=content_hash(approved_output),
    ad_copy=approved_output["ad_copy"], caption=approved_output["caption"],
    hashtags=approved_output["hashtags"], product_analysis=analysis,
    image_prompt={"prompt": "사용자 무드를 반영한 한국어 연출", "mood": ["밝음"]},
    product_images=product_photos,
    layout_selection={"aspect_ratio": "9:16", "placement": "story"},
)
# 이 메서드는 live/예산을 명시적으로 허용한 서버에서 실제 유료 호출할 수 있다.
candidate = adapter.generate(body, project_id=project_id)
# 아래 export는 API 호출/저장/승인 없이 후보 bytes를 최종 크기로 변환한다.
exported = export_candidate(candidate, copy_output=approved_output)
```

합성형은 `generation_mode="composed"`로 생성한다. `export_candidate`에는 `layout=make_placement_layout("9:16", "story")`와 신뢰된 서버 폰트 경로 `font_path`를 함께 제공한다. legacy 후보 receipt에 모드가 없으면 composed로 처리한다. 자유형에는 layout/font를 제공하지 않는다. 자유형은 모델이 이미 글자를 그리므로 로컬에서 글자를 또 얹지 않는다.

## 1~4장 구성과 담당 경계

| 사용자 구성 | 서빙이 고정할 후보 | 이미지 호출 |
|---|---|---|
| 단독1장 | free-1 또는 composed-1 | 선택 방식1회 |
| 1:1 / 총2장 | free-1, composed-1 | 후보별 n=1 |
| 2:1 / 총3장 | free-1, free-2, composed-1 | 후보별 n=1 |
| 3:1 / 총4장 | free-1, free-2, free-3, composed-1 | 후보별 n=1 |

이미지 모듈은 총 장수·서빙 batch 상태·병렬 스케줄러를 새로 만들지 않는다. 위 표는 호출 예이고 프론트 요청 필드명/HTTP 계약은 서빙이 확정해야 한다. 서로 다른 후보에 같은 job_id를 재사용하면 안 된다. 같은 job_id·다른 방식/카피/사진/비율은 충돌이며 성공 결과 재조회에는 새 POST가 없다. 텍스트 모델을 후보 수만큼 다시 부르지 않는다. 공유 계정 예산·동시성·후보별 승인·부분 실패 집계는 서빙 소유다. 이미지에 두 번째 팀 잔액 차감 장치를 추가하지 않았다.

## 프롬프트와 최종 크기

자유형은 승인 헤드/서브/허용된 CTA를 JSON 데이터로 넣고 제품·라벨·색·재질·무드·워터마크 제외 정책을 유지한다. 합성형의 글자 생성 금지/고정 문구 박스 지시는 붙이지 않는다. `layout_selection`은 자유형의 비율/플랫폼 용도로만 쓰며 제품/글자 고정 좌표를 강제하지 않는다. 명시 safe_zone과 자유형은 함께 쓸 수 없다. 3:4 feed는 이미지 CTA, 4:5 feed·릴스·스토리 프로필은 플랫폼 CTA를 사용하므로 자유형 이미지에 CTA를 다시 그리지 않는다. 본문/해시태그는 양쪽 모두 별도 출력이다.

| 선택 | 생성 | 최종 export |
|---|---|---|
| 3:4 feed | 1152×1536 | 1080×1440 |
| 4:5 feed | 1152×1440 | 1080×1350 |
| 9:16 reels/story | 1152×2048 | 1080×1920 |

자유형은 글자 박스/폰트와 독립적으로 같은 비율의 PNG를 축소한다. 확대·크롭·늘이기를 금지하고 원본/파생 hash를 반환한다. PNG 압축은 무손실이나 축소 픽셀 변화는 있다. 생성 원본과 최종 축소본의 문구·제품·모바일 가독성을 사람이 확인해야 한다. 최종 다운로드 승인은 export된 파일 hash에 묶어야 한다. `needs_manual_check=True`는 미검수 안내이며 승인 완료가 아니다. 자유형의 `protected_region_check=False`는 실제 제품 위치 검증을 했다는 뜻이 아니다.

## 기록·오류·복구

`receipt.generation_mode`는 저장 요청의 `text_rendering`으로 복원한다. 외부 request ID/usage/attempt/예약/실제 비용 null은 기존 receipt 계약을 유지한다. 새 원장·SDK·자동 유료 재시도는 없다. 자유형 글자/라벨의 정확성은 API 성공만으로 보장하지 않는다. 캡션이나 전체 기획을 이미지 프롬프트에 복사하지 않지만 binding fingerprint는 전체 승인 카피를 포함한다.

잘못된 전체 카피는 `BACKEND_COPY_INVALID`, 같은 후보 입력 변경은 `BACKEND_INPUT_CONFLICT`다. 합성 배치/폰트 누락은 `CANDIDATE_COMPOSITION_REQUIRED`, 자유형에 합성 설정 전달은 `CANDIDATE_FREEFORM_CONFIG_INVALID`다. 불명 상태/모드·이미지 hash/메타데이터 불일치도 export를 거절한다. 폰트/합성 실패는 생성 bytes를 보존해 `export_candidate`만 다시 호출한다. `export_candidate`는 원장을 만들거나 이미지를 재생성하지 않는다.

## 재현과 남은 연결

저장소 루트, Python3.11 이상 환경에서 `tests/image_generation/requirements.txt`를 설치한다.

```bash
python -m pytest tests/image_generation/test_two_track.py -q -W error -k contract
python -m pytest tests/image_generation/test_two_track.py -q -W error -k transport
python -m pytest tests/image_generation/test_two_track.py -q -W error -k export
python -m pytest tests/image_generation -q -W error
```

테스트는 로컬 fixture/MockTransport/실제 SQLite와 폰트를 쓰며 소켓 연결은 fixture에서 차단한다. 실제품·유료 API·공급자 과금·실제 Instagram·프론트/서빙 연결·배포 시험은 하지 않았다. 실서비스 구현 완료로 세지 않는다. 서빙은 승인 snapshot/고유 후보ID·계정 예산 허가를 이미지 경계에 연결하고, 결과 후보/최종 파일 hash를 저장·승인·다운로드하는 기존 흐름을 확장해야 한다. 텍스트는 자유 광고용/배경 전용 프롬프트 문안을 공급한다. 과거 고정 정책과 모순되는 배경 전용 지시를 그대로 자유형에 넣으면 품질에 영향을 줄 수 있다.


[담당별 연동 방법·남은 일·PM 제안](imageTwoTrackHandoff.md).
