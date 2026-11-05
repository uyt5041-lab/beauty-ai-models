# 비율·노출 위치 선택과 큰 글자 합성

2026-11-05 검증 · dev 6f7090c 기반 이미지 모듈 구현 · 실제 이미지 API 호출0회

## 사용

이미지 모듈의 선택 필드는 `BackendImageInput.layout_selection`이다.

```python
selection = {"aspect_ratio": "9:16", "placement": "story"}
# 기존 ProductAnalysisResult, 승인 카피, 정면 우선 사진 등은 그대로 전달한다.
body = BackendImageInput(**existing_fields, layout_selection=selection)
candidate = adapter.generate(body, project_id=server_resolved_project_id)
```

| aspect_ratio | placement | API 생성 크기 | 합성·다운로드 크기 |
|---|---|---|---|
| 3:4 | feed | 1152×1536 | 1080×1440 |
| 4:5 | feed | 1152×1440 | 1080×1350 |
| 9:16 | reels | 1152×2048 | 1080×1920 |
| 9:16 | story | 1152×2048 | 1080×1920 |

`9:16`에서는 `reels` 또는 `story`를 명시해야 한다. 잘못된 조합은 전송 전에 차단한다.
선택을 생략하면 기존4:5 요청·프롬프트·저장 결과 재사용을 유지한다. 예전 `safe_zone`은 카피를 위해 제품을 비우는 영역이라는 의미이므로 새 `layout_selection`과 동시에 받지 않는다.

이미지 어댑터가 생성 크기를 자동 설정한다. 한국어 공통 정책에는 버전이 있는 배치 지시를 자동 추가한다. 생성 픽셀 좌표와 정규화 좌표의 UI 회피 영역·제품 목표 영역·헤드/서브/CTA 공간이 모두 들어간다. `UI_SAFE` 전체를 빈 배경으로 만들지 않으며, 문구 공간만 제품·소품 없이 비운다. 광고 글자는 API가 그리지 않고 나중에 합성한다.

## 합성

서빙이 승인한 동일한 이미지·전체 카피 스냅샷·해시·리비전을 기존 계약대로 넘긴다.

```python
from src.image_generation import CompositionInput, compose_ad_png, make_placement_layout

selection = body.layout_selection
layout = make_placement_layout(
    selection.aspect_ratio, selection.placement,
    protected_regions=reviewed_actual_product_bounds,  # 정규화 CompositionRect 목록
)
result = compose_ad_png(
    CompositionInput(
        job_id=candidate.job_id, image_bytes=candidate.image_bytes,
        image_sha256=candidate.sha256, copy_revision=candidate.copy_revision,
        copy_hash=candidate.copy_hash, copy_output=approved_full_copy_output,
        layout=layout,
    ),
    font_path=trusted_server_font_path,
)
```

API 요청과 합성에 같은 선택값을 사용한다. 폰트 경로는 기존처럼 신뢰하는 서버 설정으로 받는다. 잘못된 비율의 이미지를 늘이거나 자르지 않으며, 선택한 최종 크기로 LANCZOS 축소 후 PNG로 저장한다. PNG 압축은 무손실이지만 축소 자체는 재표본화다. 원본 바이트는 별도 보존한다.

기본 제품 보호 영역은 레이아웃 목표이며 자동 검출값이 아니다. 실제 후보의 제품이 목표를 벗어날 수 있으므로 검수한 실제 보호 영역을 추가한다. 영역이 문구와 겹치면 합성을 차단한다. 승인·소유권·공유 예산·HTTP 라우트는 계속 서빙 담당이다.

## 세이프존과 문구 영역

좌상단 원점, 최종 이미지 px의 [left,top,right,bottom)이다. 생성 요청에는 같은 비율로 환산한 좌표를 넣는다. 공식 플랫폼 고정 규격이 아닌 내부 배치다.

| 배치 | UI 안전영역 | 제품 목표·보호 근사 영역 |
|---|---|---|
| feed 3:4 | [64,64,1016,1376] | [463,564,609,1262] |
| feed 4:5 | [64,64,1016,1286] | [466,418,611,1183] |
| reels 9:16 | [65,269,1015,1152] | [641,343,803,1139] |
| story 9:16 B | [65,269,1015,1420] | [629,493,813,1407] |

| 배치 | 헤드 | 서브 | CTA |
|---|---|---|---|
| feed-3:4 | (86, 86, 994, 236) | (86, 252, 994, 448) | (86, 1264, 994, 1368) |
| feed-4:5 | (86, 81, 994, 231) | (86, 247, 994, 410) | 플랫폼 버튼만 사용 |
| reels-9:16 | (86, 288, 610, 550) | (86, 580, 610, 830) | 플랫폼 버튼만 사용 |
| story-9:16 | (86, 288, 610, 550) | (86, 580, 610, 830) | 플랫폼 버튼만 사용 |


글자를 키우기 위해 문구 공간을 넓혔고 피드CTA는 기존 합성기의5% 여백 안에 맞췄다. 피드 헤드1줄·9:16 헤드최대2줄·서브최대2줄. 일반3:4만 이미지CTA1줄을 사용하며 최대48px로 제한한다. 광고피드·릴스·스토리는 플랫폼 버튼을 사용하고 이미지CTA를 생략한다. 전체 승인 카피·해시는 보존한다. 여백16px과 중앙 정렬을 지킨다. 36~128px 범위에서 해당 폰트·실제 문구가 들어가는 가장 큰 크기를 고른다. 부족하면 자르거나 최소 크기 아래로 줄이지 않고 `COMPOSITION_TEXT_OVERFLOW`를 반환한다. 긴 카피는 모든 배치에서 같은 크기를 보장하지 않는다.

스토리B 제품 위치·크기와 세이프존은 유지했다. 실험에서는 기존 생성 사진을 이동·확대했지만 이번 구현은 그 결과의 목표 영역을 생성 프롬프트에 전달한다. 새 API 결과가 B와 같은 위치에 정확히 놓이는 것은 보장하지 않으며 자동 누끼·재배치 기능을 추가한 것은 아니다.

## 테스트와 TODO

- [x] 비율·노출 위치 조합 및 생성 크기 검증
- [x] UI 안전영역·제품·문구 영역을 한국어 프롬프트에 자동 추가
- [x] 기존 입력 지문·요청 직렬화·성공 결과 재사용 유지
- [x] 선택별 합성 크기·리사이즈·최대 글꼴·줄 수·넘침 차단
- [x] 모의 전송→기존 어댑터→합성의4배치 통합 테스트
- [x] 이미지 전체 테스트378개 통과. 테스트 fixture가 외부 소켓 연결을 차단
- [x] 팀 계약의 전체 카피 해시를 고정 벡터로 검증. 별도 서빙 체크아웃이나 기기 경로에 의존하지 않음
- [x] 이미지에 생략한 플랫폼 CTA는 폰트 글리프 검사에서도 제외. 승인된 전체 카피와 해시는 보존하며, 실제 이미지에 넣는 CTA의 미지원 글자는 계속 차단
- [x] 저장된 실제 생성물로 만든 확대 시안8개 UI 조건 가림0건
- [ ] 서빙·프론트의 선택 UI에서 `layout_selection` 전달 및 승인된 합성 경로에 helper 연결
- [ ] 실제 API 결과의 제품 배치·보존·글자 대비 검수 (이번 요청에서는 API 호출하지 않음)
- [ ] 실제 Instagram 게시·실기기 확인

```bash
python -m pip install -r tests/image_generation/requirements.txt
python -m pytest -c tests/image_generation/pytest.ini tests/image_generation -q
python scripts/preview_image_layout.py --aspect-ratio 9:16 --placement story
```

미리보기 명령은 배치 지시와 크기만 출력하고 API를 호출하지 않는다. `scripts/preview_image_layout.py`와 `layout_profiles.py`는 환경키를 읽거나 네트워크 연결을 하지 않는다. 저장된 동일 작업에서 선택을 바꾸면 입력 충돌로 차단하므로 새 후보job_id를 사용한다. 완료 체크는 이미지 Python 모듈의 검증 상태다. 서빙·프론트 연결, 실제 생성 결과 및 배포·실기기 인수는 위 미완료 항목으로 구분한다.
