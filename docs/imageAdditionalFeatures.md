# 이미지 모듈 추가 기능 소개 · 2026-11-12

**기본 범위: 14/15개·93.3% / 추가 기능: 모듈 구현5/6개·83.3%. 두 퍼센트는 합산하지 않는다.** 기본 범위는11/02 논의한 MVP+고도화7개 중 이미지 담당 작업이다. 아래는 그 밖의 보강·실험을 소개한 별도 목록이다.

추가 완료율은 현재 소개6개를 한 번씩 센 것이다. 실제 성과·품질 점수·팀 배포율이 아니다. 라벨 합성1개는 로컬 실험이 있으나 공개 모듈 완료로 세지 않는다. 기존 A14 통합 소개와 아래 X01~X04를 중복으로 더하지 않는다.

| ID | 추가 기능 | 확인 상태 |
|---|---|---|

| X01 | 한국어 공통 고정 프롬프트 보강 | 모듈 구현 |

| X02 | 14디멘션 연출 선택·보강 | 모듈 구현 |

| X03 | 합성기 글자색·대비 자동 조절 | 모듈 구현 |

| X04 | 합성기 글자 위치·경계 회피 조절 | 모듈 구현 |

| X05 | 자유형 프롬프트·생성 방식 | 모듈 구현 |

| X06 | 원본 사진 라벨 합성 | 로컬 실험 · 공개 모듈 미포함 |


## X01 한국어 공통 고정 프롬프트 보강

**상태:** 모듈 구현

제품 원형·실제 로고/라벨 보존, 추가 워터마크 제외, 제품 사진과 참고 사진의 역할을 공통 지시로 붙인다. 기존 요청은 유지하고 버전별로 분기한다.

**선택·사용:** korean_image_policy.py의 버전 정책을 요청에 연결한다. text_generation의 한국어 원문은 번역하지 않는다.

**한계:** 생성 모델이 지시를 정확히 지켰다는 자동 인증은 아니다. 기본 M03 수신 기능과 구분한 정책 보강 소개이며 기본 완료 수에 중복 가산하지 않는다.

**코드·검증 근거:** [src/image_generation/korean_image_policy.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/korean_image_policy.py) · [tests/image_generation/test_composed_dimensions.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/tests/image_generation/test_composed_dimensions.py)


## X02 14디멘션 연출 선택·보강

**상태:** 모듈 구현

콘셉트, 배경색, 제품 고유색, 톤, 조명, 재질, 광택, 형태, 소품, 장소/사실, 구도, 시선, 글자 영역, 모바일 가독성을 한 규칙으로 정리한다. 사용자가 준 명시값을 우선하고 빈 부분만 조건부 기본값으로 보완한다.

**선택·사용:** scene_mode="standard" 또는 "dimensions14"로 선택한다. 현재14D 버전은 기존4:5 A합성 전용이다.

**한계:** 14개 항목을 하나씩 켜고 끄는 UI가 아니다. 3:4·9:16이나 자유형에14D를 그대로 함께 적용하지 않는다. 추가 LLM 호출로 정책을 고르는 방식도 아니다.

**코드·검증 근거:** [src/image_generation/composed_dimensions.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/composed_dimensions.py) · [src/image_generation/DIMENSIONS14.md](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/DIMENSIONS14.md) · [tests/image_generation/test_composed_dimensions.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/tests/image_generation/test_composed_dimensions.py)


## X03 합성기 글자색·대비 자동 조절

**상태:** 모듈 구현

실제 글자 아래 배경을 읽어 장면과 어울리는 글자색을 고르고 대비를 기록한다. 일반 자동 색 경로는 필요할 때 글자 영역 안에 얇은 배경막을 더한다.

**선택·사용:** compose_ad_png의 color_mode="auto" 또는 "fixed". 지정색 유지와 자동 조절을 구분한다.

**한계:** 최신 A 적응형 경로는 사각 박스를 추가하지 않는 별도 탐색을 사용한다. 모든 배경에서 대비 합격을 보장하지 않으며 실패 수치도 receipt에 남긴다.

**코드·검증 근거:** [src/image_generation/typography_color.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/typography_color.py) · [src/image_generation/adaptive_typography.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/adaptive_typography.py) · [src/image_generation/composition.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/composition.py)


## X04 합성기 글자 위치·경계 회피 조절

**상태:** 모듈 구현

이미지 경계 필터로 복잡한 곳을 찾고 지정 문구 박스 안에서 대비와 간격이 맞는 위치를 탐색한다. 최신 A합성은 가로 중앙을 유지하며 세로 위치를 조절한다.

**선택·사용:** compose_ittem_ad의 clearance_px=12, relaxed_clearance_px=4, fallback_lower, fallback_shift_px 설정을 사용한다.

**한계:** 안전 위치를 못 찾으면 설정에 따른 하단 대체 위치와 실패 여부를 기록한다. 실제 제품 위치를 AI로 검출·검증하는 기능은 아니다. 재합성 자체는 이미지 API를 다시 호출하지 않는다.

**코드·검증 근거:** [src/image_generation/adaptive_typography.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/adaptive_typography.py) · [src/image_generation/ittem_hotfix.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/ittem_hotfix.py) · [src/image_generation/ADAPTIVE_TEXT.md](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/ADAPTIVE_TEXT.md) · [tests/image_generation/test_adaptive_typography.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/tests/image_generation/test_adaptive_typography.py)


## X05 자유형 프롬프트·생성 방식

**상태:** 모듈 구현

고정 A합성과 구분해 이미지 API가 장면과 승인 광고 문구를 함께 그리는 자유형 경로를 제공한다. 완전한 승인 카피 스냅샷을 요구하고 결과 내보내기는 별도 글자 합성 없이 리사이즈한다.

**선택·사용:** BackendImageInput.generation_mode="freeform". prompt 문자열/구조형 입력과 별도의 beauty_ai.freeform_image 실험 CLI도 있다.

**한계:** 자유형은 추가 이미지 호출이며 글자 정확성·제품 보존·가독성을 보장하지 않는다. 14D A 전용 정책과 섞지 않는다. 여러 후보 요청 정책과 서비스 화면 적용은 별도다.

**코드·검증 근거:** [src/image_generation/backend_adapter.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/backend_adapter.py) · [src/image_generation/candidate_export.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/candidate_export.py) · [src/image_generation/korean_image_policy.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/src/image_generation/korean_image_policy.py) · [tests/image_generation/test_two_track.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/tests/image_generation/test_two_track.py) · [beauty_ai/freeform_image.py](https://github.com/uyt5041-lab/beauty-ai-models/blob/6735e979c46626597034c99fa4aec8151517c9ee/beauty_ai/freeform_image.py)


## X06 원본 사진 라벨 합성

**상태:** 로컬 실험 · 공개 모듈 미포함

원본 사진의 보이는 인쇄 영역을 잘라 용기 곡면에 맞춰 옮기는 방식이다. AI가 라벨 글자를 새로 쓰는 방식과 비교했다.

**선택·사용:** 로컬 reports/original-label-pixel-composite-20261105/compose.py의 수동 경계·곡면 앵커를 사용한 단일 사례다.

**한계:** 추가 이미지 API0회, 라벨 영역 밖 동일 픽셀·동일 재실행 해시를 확인했다. 라벨 내부는 보간·색 보정으로 픽셀이 바뀌고 가려진 글자는 복원하지 않는다. 자동 라벨 검출·일반화·서비스 연결은 미구현/미확인이다. 별도 AI 라벨 편집4건은 정확한 문자 복구 기준을 통과하지 못했다.

**근거 위치:** 로컬 실험 `reports/original-label-pixel-composite-20261105/`와 `reports/label-composite-trial-20261105/`. 공개 브랜치에 합성 API가 있는 것으로 안내하지 않는다. 원본 사진·권리 자료는 게시하지 않는다.


## 집계·운영 경계

- 기본 진행률은 `imageTodoActive-20261112.json`의14/15·93.3%를 유지한다.
- 추가 소개 완료율은 [별도 데이터](imageAdditionalFeatures-20261112.json)의5/6·83.3%다. 기본과 추가를 합쳐176.6%로 표시하지 않는다.
- 공동 연동·품질·인수 J01~J09는 두 구현 완료율과 별도 상태로 남긴다.
- 기존 A14는 추가 소개의 묶음 설명이다. X01~X04와 중복 집계하지 않는다.
- 레포 코드 존재·단위 시험·로컬 실제 생성·팀 배포·인수는 서로 다른 단계다. 서빙 추가 연결 보류는 유지한다.
- 이번 갱신은 문서만 수정하며 유료 호출·라벨 재합성·배포는 하지 않는다.
