# 이미지·서빙 MVP 연결 요청

작성일: 2026-10-28. 실행 패키지는 양쪽 레포의 `feat/image-generation`에 있는 `src/image_generation/`입니다. 기존 `beauty_ai/` 실험 패키지와 구분합니다. 팀 레포에는 소스만 반영하고 이 문서는 개인 레포에서 관리합니다.

## 전달할 요청문

이미지 MVP를 서빙 코드 #4~9 기준으로 맞췄습니다. 기존 업로드·JobStore·승인·저장·다운로드 구조는 재사용하고, 아래 부분만 추가 연결 부탁드립니다.

**이미지 쪽 완료**

- 한국어 이미지 생성 어댑터, 제품 분석 실패·비화장품·성공/error 모순 응답 차단
- `image_prompt`에 `concept·prompt·mood` 객체 또는 한국어 프롬프트 문자열 수신. `{prompt: ...}` 객체도 가능
- 없는 콘셉트·무드는 만들지 않음. 한국어를 번역하지 않고 제품 보존 등 고정 지침을 추가
- 문자열 `copy_revision`을 생성·합성·반환에서 그대로 유지. 기존 양의 정수도 지원
- 이미지 바이트·크기·해시·실행 기록 반환, 한글 최종 합성으로 1080×1350 PNG와 캡션·해시태그 반환
- 기존 요청의 저장 기록 재사용 및 추가 호출 방지 호환성 검증

**서빙 쪽 추가 연결 요청**

1. 기존 모의 이미지·합성 처리에 `BackendImageAdapter.generate()`와 `compose_ad_png()`를 연결해 주세요. 반환된 실제 바이트·크기·해시·실행 기록을 기존 저장·다운로드 구조에 넣으면 됩니다. 실제 실행 경로와 mock 표시를 구분해 주세요.
2. 모의 `copy_candidate.text` 대신 다혜님 실제 결과인 `ProductAnalysisResult`, 이미지 프롬프트, 전체 `AdCopyOutput`을 전달해 주세요. #9의 Fake 카피에는 `plan`이 없습니다. 실제 `plan·ad_copy·caption·hashtags`를 서버에 저장하고 검수·승인·합성에서 같은 스냅샷과 해시를 사용해야 합니다. 승인 후 임의로 plan을 추가하거나 해시를 다시 붙이지 않습니다.
3. #9의 검수 기록·버전 확인 구조를 실제 텍스트 결과에 연결해 주세요. 이미지 승인 시에는 승인한 이미지 SHA-256과 카피 revision/hash도 함께 보관하고, 그 값을 합성에 전달해 주세요. 기존 승인 API를 새로 만들자는 요청은 아닙니다.
4. 고정된 프로젝트 UUID·작업 ID·영속 저장 경로·허용 예산을 연결해 주세요. 이미지 쪽 시도 기록과 중복 호출 차단을 재사용합니다. 서버 저장 실패나 응답 유실 때 새 ID로 자동 재호출하지 않고 기존 결과·실행 기록을 조회해 주세요. 이미지 예산 제한은 텍스트까지 포함한 팀 전체 예산 관리와 별개입니다.

**공동 확인**

기존 한글 폰트 설정을 재사용하고 `fonttools` 및 합성 배치 좌표를 연결하면 됩니다. 기본 배치 예시는 이미지 쪽에서 제공합니다. 실제품에 맞는 위치는 함께 확인합니다. 모의 전송으로 두 입력 형식·문자열 버전·분석 실패·중복 요청·합성 실패를 확인한 뒤, 승인된 실제품 1건으로 생성 → 승인 → 합성 → 다운로드를 검증하겠습니다. 실제 유료 호출은 이번 변경에서 실행하지 않았습니다.

## 이미 된 부분과 확인 범위

| 근거 | 확인된 내용 | 추가 요청에서 제외한 부분 |
|---|---|---|
| [#4](https://github.com/taemin050-sys/project-it-tem/pull/4), merged, `4a45765` | 연동 계약 문서·검증 코드 | 계약 문서 새로 작성 |
| [#5](https://github.com/taemin050-sys/project-it-tem/pull/5), merged, `a693215` | 업로드 검증·파일 기반 JobStore·카피/이미지 승인·상태 전이·결과 저장·다운로드 | 저장소·승인·다운로드 전체 재구현 |
| [#6](https://github.com/taemin050-sys/project-it-tem/pull/6), open, `637cfae` | 기존 11개 API의 안내·검사 | 실제 모델 호출 구현으로 간주하지 않음 |
| [#7](https://github.com/taemin050-sys/project-it-tem/pull/7), open, `10b62a0` | 모의 브라우저 다운로드·재시작 검증 자료 | 모의 E2E 전체 재작성. 정식 UI나 실제 모델 인수로 간주하지 않음 |
| [#8](https://github.com/taemin050-sys/project-it-tem/issues/8), issue | 텍스트 v0.7 공식 소스 인계와 분석 실패 차단 추적 | 구현된 PR로 간주하지 않음 |
| [#9](https://github.com/taemin050-sys/project-it-tem/pull/9), open, `aac9556` | 기본 비활성인 Fake 검수 기록·해시·버전·변경 무효화 | 같은 검수 기록 구조의 중복 구현. 실제 텍스트/이미지 연결은 남음 |

위 상태는 2026-10-28 조회 기준입니다. 이 요청문은 새로운 HTTP 계약 확정이나 서빙 통합 완료 선언이 아닙니다. 이번 범위에 Langfuse/W&B의 서빙 연동, 고급 자동 배치, 다중 서버 전환은 포함하지 않습니다.

## 입력 형식과 버전 규칙

다음 세 값은 모두 `BackendImageInput.image_prompt`에 전달할 수 있습니다. 나머지 `job_id`, `product_analysis`, 사진, 승인 카피 관련 필드는 동일하게 필요합니다.

```python
structured = {
    "concept": "일상 루틴",
    "prompt": "밝은 분홍 배경. 제품의 무광과 원래 비율을 유지한다.",
    "mood": ["청량", "밝음"],
}
plain = "밝고 청량한 분홍 배경. 제품의 무광과 원래 비율을 유지한다."
prompt_only = {"prompt": plain}
```

- `plain`과 같은 내용의 `prompt_only`는 내부에서 동일한 입력으로 정리됩니다. 원문 공백과 줄바꿈도 보존합니다.
- `concept`는 생략 또는 `None`이 가능하고, `mood`는 생략 또는 빈 배열이 가능합니다. `concept=""`와 빈 프롬프트는 거절합니다.
- 전달된 광고 무드를 VLM의 제품 관찰 무드로 교체하지 않습니다. 프롬프트 단독이면 그 문장의 분위기 지시를 따릅니다.
- `ImagePrompt{positive,...}` 또는 `prompt_en`은 이 서비스 입력 형식이 아닙니다. 이 문서의 `image_prompt` 형식을 사용합니다.
- `copy_revision`은 제어문자 없는 1~128자 비공백 문자열 또는 양의 정수입니다. `mock-copy-r01`, `"1"`, `1`을 원문·원래 타입 그대로 보존합니다. `"1"`과 `1`은 다른 버전 식별값입니다.
- 생성·서버 승인 스냅샷·합성에 같은 revision 타입/값을 사용합니다. 서버의 `generation`을 카피 revision으로 대신 넣지 않습니다.
- 기존 구조형/정수 요청은 이전 프롬프트 버전과 입력 해시를 유지합니다. 새 형식은 `backend-v02-ko-v2`를 씁니다. 이는 저장된 요청을 다시 과금하지 않기 위한 호환 처리입니다.
- 합성 해시는 전체 `AdCopyOutput`의 canonical JSON SHA-256입니다. 해시 일치 자체는 사용자 승인이나 검수 완료 증거가 아닙니다.

## 최소 합성 연결 예시

아래 좌표는 **제품이 오른쪽에 있는 검증용 배치 예시**입니다. 제품 위치를 자동으로 찾지 않습니다. 실제품의 위치가 다르면 이미지 담당과 좌표를 조정합니다. 보호 영역과 텍스트 상자가 겹치면 합성을 거절합니다. 텍스트 상자는 최종 1080×1350 캔버스의 정규화 좌표입니다.

```python
import os
from src.image_generation import CompositionInput, compose_ad_png

layout_example = {
    "text_regions": {
        "headline": {"left": .06, "top": .06, "right": .94, "bottom": .23},
        "subline": {"left": .06, "top": .28, "right": .46, "bottom": .65,
                    "max_font_size": 40},
        "cta": {"left": .06, "top": .76, "right": .46, "bottom": .91,
                "max_font_size": 36},
    },
    "protected_regions": [{"left": .52, "top": .28, "right": .90, "bottom": .91}],
}

def compose_approved_snapshot(snapshot, layout):
    # snapshot은 서버가 소유권·검수·사용자 승인을 확인한 저장 데이터입니다.
    # 브라우저가 제출한 bytes/hash를 승인 증거로 사용하지 않습니다.
    result = compose_ad_png(
        CompositionInput(
            job_id=snapshot["job_id"],
            image_bytes=snapshot["image_bytes"],
            image_sha256=snapshot["image_sha256"],
            copy_revision=snapshot["copy_revision"],
            copy_hash=snapshot["copy_hash"],
            copy_output=snapshot["copy_output"],
            layout=layout,
        ),
        font_path=os.environ["IT_TEM_KOREAN_FONT"],
    )
    # 기존 transition_job 저장 경로에 image_bytes/caption_bytes를 연결합니다.
    # width/height/sha256/receipt도 함께 보관합니다. 여기서는 파일을 저장하지 않습니다.
    return result
```

Python 패키지는 `pydantic>=2.7,<3`, `httpx>=0.27,<1`, `Pillow>=10`, `fonttools>=4,<5`가 필요합니다. 폰트는 서버에 사용권이 있는 한글 폰트를 설치하고 기존 `IT_TEM_KOREAN_FONT`에 경로를 지정합니다. 실제 사용 문자 지원을 검사하므로 파일 이름만 한글 폰트처럼 보여서는 안 됩니다. 폰트 파일·환경파일·키는 저장소에 포함하지 않습니다.

서버의 기존 `copy_candidate.text` 말줄임 합성과 달리 새 합성은 headline/subline/CTA를 나눠 그립니다. 영역에 들어가지 않는 문구는 자르지 않고 `COMPOSITION_TEXT_OVERFLOW`로 거절합니다. 오류 응답과 재배치 안내는 서빙/UI에 연결해야 합니다.

## 검증 범위

- 기존 MVP·합성 263개와 추가 입력/버전 호환 38개를 포함해 양쪽 체크아웃에서 각각 **301개 통과**했습니다. 경고를 오류로 처리했습니다. 실제 모델 API 호출은 0회입니다.
- 이전 게시 커밋 `d99dee0` 코드가 만든 실제 로컬 DB 기록을 새 코드로 읽어, 추가 모의 provider 요청 없이 결과를 재사용하는지 확인합니다.
- 실제 서비스 승인·저장·다운로드 E2E와 실제품 광고 품질 검수는 아직 완료하지 않았습니다.
