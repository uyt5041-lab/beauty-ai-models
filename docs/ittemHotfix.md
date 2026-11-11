# 잇템핫픽스 · 이미지 모듈 묶음

작성일: 2026-11-12. 브랜치: `잇템핫픽스`.

승인한 14디멘션 선택 생성과 중앙 고정·상하 조절 A 합성을
`src.image_generation` 패키지에 모았다. 프론트·서빙·Text 코드는 변경하지 않는다.
기존 `beauty_ai` 실험 래퍼는 보존한다. 잇템 적용용 소스는 `src/image_generation/`이다.

## 1. 설치와 테스트

```bash
python -m pip install -e '.[test]'
python -m pytest tests/image_generation -q -W error
python3 scripts/check_fivecircles.py
```

패키지 설치 시 `src.image_generation`도 포함된다. 합성 의존성 `fonttools`를 명시했다.
서버에는 사용 허가된 한글 폰트 경로를 설정한다. 테스트용 OFL 폰트와 출처는
`tests/image_generation/fixtures/fonts/`에 있고 운영 폰트 설정을 대체하지 않는다.

## 2. 이미지 생성과 승인 후 합성

```python
from src.image_generation import (
    CompositionInput,
    build_ittem_image_adapter,
    compose_ittem_ad,
    make_a_template,
)

# 기존 OpenAIImagesAdapter·LocalAssetStore·ProviderAttemptStore로 구성한
# processor를 사용한다. 키·호출 허용·예산은 기존 서버 설정이 관리한다.
image_adapter = build_ittem_image_adapter(processor, binding_path)

# 기존 BackendImageInput: 사진 2~5장, 한국어 prompt 또는 concept/prompt/mood,
# ProductAnalysisResult, 승인된 카피 revision/hash를 그대로 전달한다.
# layout_selection은 생략하고 safe_zone은 생략하거나 make_a_template()[0]을 사용한다.
candidate = image_adapter.generate(backend_image_input, project_id=project_id)

# 사용자 이미지 승인 및 카피 snapshot 일치 확인 후 아래 로컬 합성을 호출한다.
_, layout = make_a_template()
composition = CompositionInput(
    job_id=candidate.job_id,
    image_bytes=candidate.image_bytes,
    image_sha256=candidate.sha256,
    copy_revision=candidate.copy_revision,
    copy_hash=candidate.copy_hash,
    copy_output=approved_full_copy_output,
    layout=layout,
)
final_ad = compose_ittem_ad(composition, font_path=licensed_font_path)
```

`build_ittem_image_adapter()`는 **2.6 / complexity high / quality medium /
scene_mode=dimensions14**를 선택한다. 조립할 때 호출하지 않고 `generate()`에서만 생성한다.
기존 설정을 직접 사용할 때는 `BackendImageAdapter(..., scene_mode="dimensions14")`도 가능하다.

`compose_ittem_ad()`는 저장된 생성 원본에 글자만 합성한다. 이미지 API·텍스트 LLM 호출은 없다.
생성과 합성을 한 번에 자동 승인하지 않는다. 입력 DTO, 승인·인증·HTTP·배포는 기존 담당 영역이다.
수정 합성도 같은 원본과 승인된 카피를 사용해 이 함수만 다시 호출한다.

## 3. 묶음에 적용한 규칙

| 항목 | 동작 |
|---|---|
| 14디멘션 | 콘셉트·배색·고유색·톤·광원·재질·광택·형태·소품·사실관계·배치·시선·글자·모바일 |
| 충돌 우선순위 | 제품 정체성/사실 → A배치·인물 제외·광고 글자 미생성 → 명시한 장면/무드 → 기본값 |
| 제품과 배경 | 제품 고유색·비율·광택 등급은 유지하고 같은 장면광으로 재조명 |
| A합성 | 기존 좌표·카피·글꼴·크기 유지, 가로 중앙 고정 |
| 자동색 | 배경 관련 색·인접색·보색·중성색 후보에서 실제 글자 내부 대비 검사 |
| 위치 | A 문구 영역 안에서 세로로만 탐색, 좌우 이동 0px |
| 경계 | 평활 RGB 변화·영역 RGB 중앙값과의 차이를 탐색, 작은 반짝임/질감 제외 |
| 거리 | 12px로 탐색, 자리가 없으면 4px로 재탐색 |
| 마지막 대체 배치 | 다시 실패하면 중앙 고정·아래 48px, A영역 안으로 제한 |
| 네모 배경 | 이 묶음에서는 추가하지 않음. 명시적 배경판 설정도 거절 |
| 결과 기록 | 선택색·대비·이동량·사용한 이격 거리·강제 배치·미달 경고 |

## 4. 조절할 설정

```python
final_ad = compose_ittem_ad(
    composition,
    font_path=licensed_font_path,
    clearance_px=12,
    relaxed_clearance_px=4,
    fallback_lower=True,
    fallback_shift_px=48,
)
```

엄격하게 중단하려면 `fallback_lower=False`로 준다. 강제 배치 시에도 실패한 검사를 통과로
바꾸지 않는다. `text_contrast_passed`, `FORCED_LOWER_PLACEMENT`,
`TEXT_CONTRAST_BELOW_TARGET`, `PLACEMENT_CLEARANCE_BELOW_TARGET`을 확인한다.

대비 목표는 4.5:1이며 글자 중심부(불투명도 192/255 이상)를 검사한다.
픽셀 경계 회피는 제품의 의미를 이해하는 객체 탐지가 아니다. 작은 물체나 배경과 비슷한 제품은
빠질 수 있다. 14디멘션 역시 자연어 지시이며 모델의 정확한 픽셀 배치·라벨 보존을 보장하지 않는다.
이 브랜치의 업로드는 실서버/GCP 적용을 뜻하지 않는다.

## 5. 이번 묶음 검증

- 이미지 모듈 테스트 **475개 PASS**, 뷰티 레포 전체 회귀 **2,810개 PASS** (`-q -W error`).
- FiveCircles 설정 검사 PASS. wheel 빌드·독립 설치 후 패키지/진입점 import PASS.
- 같은 패키지에서 14디멘션 생성 요청 → 저장된 생성 결과 → 로컬 합성 연결을 MockTransport로 테스트.
- 기존 5장 생성 원본을 재사용해 `compose_ittem_ad()`를 실행하고 **승인한 최종 PNG와 5장 모두 바이트 일치** 확인.
- 5장 모두 가로 이동 0px·배경판 없음. 추가 이미지 API 호출 0회.
- P1/P3 CTA는 아래 48px로 강제 배치. P1은 경계 이격 미달, P3은 대비 약 4.29:1 미달을 그대로 기록.
- 실제 원본 사진·생성 PNG·키·로컬 경로가 담긴 실행 로그는 Git에 넣지 않았다.
- 수치 기록: [ittemHotfixValidation.json](ittemHotfixValidation.json).

세부 고정 프롬프트: [DIMENSIONS14.md](../src/image_generation/DIMENSIONS14.md).
위치/색 옵션: [ADAPTIVE_TEXT.md](../src/image_generation/ADAPTIVE_TEXT.md).
묶음 진입점: [ittem_hotfix.py](../src/image_generation/ittem_hotfix.py).
