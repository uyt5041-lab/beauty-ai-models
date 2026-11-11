# A 합성용 14디멘션 선택 모드

`BackendImageAdapter`를 만들 때 `scene_mode="dimensions14"`를 지정하면 적용한다.
생략하거나 `"standard"`를 쓰면 기존 이미지 프롬프트를 그대로 사용한다.
프론트·서빙·Text 입력 DTO·A 합성기·글꼴은 변경하지 않는다.

```python
from src.image_generation import BackendImageAdapter, make_a_template

# processor와 binding_path는 기존 구성값을 사용한다.
image_adapter = BackendImageAdapter(
    processor,
    binding_path,
    model="gpt-image-2.6-sunburst",
    complexity="high",
    quality="medium",  # complexity와 quality는 별도 설정이다.
    scene_mode="dimensions14",
)

safe_zone, composition_layout = make_a_template()
# body는 기존 BackendImageInput이다. safe_zone은 생략하거나 위 값을 사용한다.
candidate = image_adapter.generate(body, project_id=project_id)
# 사용자 승인 후 기존 합성 경로에서 composition_layout으로 카피를 합성한다.
```

이 설정은 이미지 모듈의 Python 옵션이다. 기존 HTTP 입력에 자동으로 선택 UI가 생기거나
운영 서버가 이 모드로 바뀌지는 않는다. 기존 작업의 모드 변경에는 새 job_id가 필요하다.
같은 job_id로 모드를 바꾸면 중복 생성 대신 `BACKEND_INPUT_CONFLICT`로 거절한다.

## 고정 규칙과 제품별 입력

고정 규칙은 [composed_dimensions.py](composed_dimensions.py)의
`render_dimensions14_policy()`에 있다. 버전은 `backend-composed-dimensions14-ko-v1`이다.
기존 보존 규칙 뒤에 중복으로 붙이지 않고 이 버전의 14개 규칙으로 교체한다.
워터마크 제외와 입력 사진의 역할은 기존 정책을 한 번만 붙인다.

| 디멘션 | 고정하는 처리 | 입력에 따라 달라지는 내용 |
|---|---|---|
| concept | 제품 단독, 한 공간에서 촬영 | 콘셉트·장소·무드 |
| palette | 제품을 배경색으로 재도색하지 않음 | 배경·소품의 색 |
| product_color | 원본 고유색·라벨·부품색 보존 | 실제 제품 사진 |
| tone | 같은 노출·화이트밸런스·명암으로 연결 | 입력의 밝기·톤 |
| lighting | 주광원·그림자·반사를 한 묶음으로 처리 | 명시한 주광원의 방향과 성질 |
| material | 부품별 재질 유지 | 사진에서 확인되는 재질과 배경 질감 |
| gloss | 원래 광택 등급 유지, 반사 위치는 재조명 | 무광·유광·투명 부품 구분 |
| geometry | 원본 비율·부품·시점 보존, 균일 확대·축소 | 제품의 실제 형태 |
| props | 지정한 소품만, 카피 영역 회피 | 소품 종류 또는 소품 없음 |
| place_truth | 장소·소품으로 원료·효능을 지어내지 않음 | 확인된 장면 입력 |
| composition | 기존 A 좌표, 상단 카피와 하단 CTA 여백 | 제품의 가로세로비에 맞는 균일 크기 조절 |
| attention | 상단 카피 → 제품 → 하단 CTA | 제품 고유색·로고·형태의 강조 |
| typography | 광고 글자는 그리지 않고 A 합성기에 맡김 | 승인된 카피와 기존 합성 글꼴 |
| mobile | 동일 초점면의 선명도·입자감, 가독성 여백 | 실제 제품의 주요 부품 |

## 충돌 처리

우선순위는 다음과 같다.

1. 원본 제품 정체성·형태·고유색·라벨과 사실 보존.
2. A배치, 인물 제외, 광고 글자 미생성.
3. 전달받은 한국어 장면·콘셉트·무드의 명시값.
4. 세부 지시가 없을 때만 쓰는 기본값.

오른쪽 주광원을 주면 왼쪽 주광원을 추가하지 않는다. 그림자를 없애라고 주면 그림자를
추가하지 않는다. 주광원과 그림자 방향이 충돌하면 주광원에 맞춰 그림자를 정리하도록 지시한다.
재질과 고유색은 보존하지만 원래 촬영광·하이라이트 위치·화이트밸런스를 고정하지 않는다.
촬영자·휴대폰·카메라·삼각대의 우연한 반사상은 새 장면의 자연스러운 반사로 정리하도록 지시한다.

**자연어 누락을 코드로 판독하는 기능은 아니다.** 명시값 우선과 조건부 기본값을
이미지 모델에 지시한다. 조명 일치·라벨 정확도·제품 보존·좌표 준수는 결과 검수가 필요하다.

A 좌표는 `make_a_template()`에서 직접 읽는다. 하단 CTA 영역도 포함한다.
v1은 A 전용이므로 `layout_selection`이나 다른 `safe_zone`을 함께 주면
`DIMENSIONS_LAYOUT_CONFLICT`로 업로드·예약·호출 전에 차단한다.
`feed 4:5` 플랫폼 배치도 A와 좌표 및 CTA 정책이 다르므로 섞지 않는다.
합성할 때도 `make_a_template()`의 layout을 사용해야 한다.

## 아벤느 장면 입력 예시

아래는 실제 제품 사진을 함께 전달할 때의 예시다. 브랜드명이나 특정 재질을 고정 정책에
박아두지 않는다. 다른 제품에는 그 제품의 장면과 원본 사진을 전달한다.

```python
image_prompt = {
    "concept": "밝은 석재 공간의 단정한 루틴",
    "prompt": (
        "낮빛이 드는 밝은 세면 공간의 낮고 넓은 석재 선반에 제품 한 개가 서 있다. "
        "배경은 밝은 크림빛 석재와 옅은 샌드 벽이며 따뜻함은 석재 고유색으로 표현한다. "
        "왼쪽 위 큰 창의 중성 확산광을 제품·벽·바닥이 공유한다. "
        "소품은 낮고 넓은 석재 판 한 개만 사용한다. "
        "사진의 투명 유리·은색 목·흰 스포이드와 실제 라벨을 구분해 유지한다."
    ),
    "mood": ["밝음", "단정함"],
}
```

이 옵션은 좋게 평가된 아벤느 실험의 14축 구조와 재조명 원칙을 일반화한 것이다.
실험의 수동 제품별 2,362자 프롬프트와 바이트 단위로 같지 않고, 같은 품질을 보장하지 않는다.
새 장면·새 제품의 결과는 별도로 비교해야 한다. 텍스트 LLM 추가 호출은 하지 않는다.

Text의 `prompt` 입력 3,000자, 내부 `positive` 4,000자 제한은 그대로다.
고정 규칙은 최종 API 프롬프트를 만들 때 붙인다. 입력을 조용히 잘라내지 않는다.
영수증에 `scene_mode`, `scene_policy_version`을 남기며 비용은 기존 사용량 계산을 사용한다.
고정 정책 또는 A 좌표를 바꿀 때에는 정책 버전도 새로 만들어 기존 요청 재현성을 유지한다.

## 테스트

저장소 루트에서 기존 이미지 테스트 환경을 사용한다.

```bash
python -m pytest tests/image_generation/test_composed_dimensions.py -q
python -m pytest tests/image_generation -q
```

최종 전송 프롬프트, 기본 모드 호환, 14축 포함, A 좌표, 충돌 차단,
버전·job 재사용·중복 호출 방지를 검사한다. 테스트는 MockTransport로 실행하며
외부 네트워크를 차단한다. 실제 이미지 품질 검증이나 서버 배포를 뜻하지 않는다.
