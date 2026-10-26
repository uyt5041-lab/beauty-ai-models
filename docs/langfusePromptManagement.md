# Langfuse 이미지 프롬프트 관리

현재 실제품 실험은 [새 실행기 안내](experimentRunner.md)를 따른다. 아래 B12의 3종 비교 명령과 production 자동 조회·게시 설명은 과거 실행 기록이다. 새 실행기는 로컬에 저장한 정확한 버전으로 계획을 고정한다.

화장품 광고 프리셋 3개의 영어 이미지 지시문을 Langfuse의 `text` 프롬프트로 관리한다.
사용하는 파이썬 패키지는 선택 의존성 `langfuse==5.0.4`다. LLM 상품 분석과 광고 문구 작성은 김다혜 담당으로 유지한다.

## 관리 대상과 계약

| 프리셋 | Langfuse 이름 |
| --- | --- |
| 클린 스튜디오 | `beautyai/cosmetics/clean-studio` |
| 고급 미니멀 | `beautyai/cosmetics/premium-minimal` |
| 밝은 컬러 광고 | `beautyai/cosmetics/vivid-pop` |

템플릿의 변수는 `product_description`, `palette_section`, `creative_direction_section`, `composition`, `reference_instructions`다.
조건부 구간과 입력 이미지 순서 설명은 기존 로컬 컴파일러가 만든다. Langfuse에서 새 버전을 편집해도 상품 설명과 레이아웃을 요청마다 전달할 수 있다.
초기 버전은 기존 `cosmetics-v1`의 실제 제공자 프롬프트와 바이트 단위로 같다.
기본 꽃 추가 지시는 없으며, 팀이 명시한 꽃 연출은 그대로 전달한다.

호출 순서는 `브리프 → production 또는 지정 버전 조회 → 변수 치환 → 스냅샷 고정 → 요청 해시 → 작업 큐 → OpenAI`다.
`ExternalImageRequest.managed_prompt`에 이름·정수 버전·완성된 본문·본문 SHA-256을 저장한다.
작업자는 Langfuse를 다시 조회하지 않는다. 대기 중 라벨이 바뀌어도 같은 작업의 본문은 바뀌지 않는다.
새 본문이나 새 버전은 다른 요청 해시를 만든다. 관리 필드가 없는 기존 요청은 직렬화와 해시가 그대로다.

이 경로는 revision 2의 생성·편집만 지원한다. 누끼의 고정 지시문과 기존 revision 1은 기존 경로를 사용한다.
첫 버전을 `production`으로 등록하는 것은 현재 컴파일러를 옮기는 작업이다. 새 연출 버전은 별도 라벨에서 검수한 후 `production`을 옮긴다.
원격 본문을 수정할 권한이 있는 사람은 제품 보존·카피 여백 지시도 수정할 수 있으므로, 버전 게시 권한과 이미지 검수를 함께 관리한다.

## 설치와 설정

프로젝트 루트에서 실행한다.

```sh
python -m pip install -e '.[observability]'
```

로컬 `.env`에는 `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`을 둔다.
`.env`를 프로그램 실행 환경으로 읽는 것은 호출자의 책임이다. 패키지 import는 파일·키·네트워크를 읽지 않는다.
CLI는 `LANGFUSE_HOST`도 사용하므로 실행 환경에서 `LANGFUSE_BASE_URL`과 같은 값을 전달한다.
키나 `.env` 전체를 출력하지 않는다. 이 프로젝트의 추적 환경 이름은 `beautyai-preset-comparison`이다.

## 프롬프트 등록과 버전 고정

`cosmetics_prompt_definition(preset_id)`는 Langfuse 생성 요청 JSON을 반환한다. 그 자체로 외부 호출을 하지 않는다.
이 JSON을 파일로 저장하고 CLI로 등록한다. 같은 이름에 다시 등록하면 새 버전이 생기므로, 먼저 조회해 기존 내용을 확인한다.

```sh
npx langfuse-cli@latest api prompts create --body-file outputs/prompt-definition.json --json
npx langfuse-cli@latest api prompts get beautyai/cosmetics/clean-studio --version 1 --json
```

CLI의 응답 포장 객체를 벗긴 실제 프롬프트 객체를 아래 함수에 전달한다.

```python
from beauty_ai.langfuse_prompts import cosmetics_prompt_variables, pin_cosmetics_prompt

variables = cosmetics_prompt_variables(brief, request)
pinned_request = pin_cosmetics_prompt(request, fetched_prompt, variables)
# 이후 content_hash(pinned_request)를 계산하고 큐에 저장한다.
```

함수는 프리셋 이름·유형·버전·본문 길이·변수 이름·본문 해시를 검사한다.
변수의 값은 한 번만 치환한다. 사용자 값 속 `{{...}}`를 다시 템플릿으로 실행하지 않는다.
조회 실패나 잘못된 응답은 이미지 호출 전에 중단한다. 원격 조회 실패를 숨기고 다른 프롬프트로 생성하지 않는다.

## 실행 기록

실제 이미지 호출 직전에 `begin_image_generation()`을 시작하고, 결과가 나온 뒤 `finish()`를 호출한다.
하나의 이미지 작업은 하나의 `generation`이며, 같은 비교 실행의 `run_id`로 세션을 묶는다.
Langfuse SDK 5의 OpenTelemetry 전송을 사용한다. 폐기 예정인 legacy ingestion으로 추적 이벤트를 쓰지 않는다.

기록하는 값은 모델, 크기, 품질, 실제 프롬프트와 Langfuse 버전, 요청 해시, 입력·출력 이미지 해시, 제공자 요청 ID, 토큰 사용량이다.
입력 텍스트·입력 이미지·출력 이미지 토큰을 구별한다. 계산한 비용은 `estimated_cost_usd`이고, 결제 내역 대조 전에는 `actual_cost_usd=null`, `billing_status=UNRECONCILED`로 둔다.
SDK의 자동 비용 계산이 보이더라도 팀 예산 잔액이나 실제 결제 금액을 확인한 것으로 해석하지 않는다.

원본 이미지·결과 이미지의 바이트, 로컬 절대 경로, API 키, HTTP 원문은 업로드하지 않는다.
이미지 확인은 로컬 비교 보고서에서 한다. 해시로 Langfuse 기록과 로컬 파일을 연결한다.

`finish()`는 한 번만 전송을 요청한다. 추적 실패가 성공한 이미지 작업을 실패 처리하거나 이미지 API를 재호출하게 만들지 않는다.
`FLUSH_REQUESTED`는 SDK 전송 요청일 뿐, 서버 저장 확인이 아니다. CLI의 v2 observations 조회로 해당 ID와 프롬프트 버전을 읽어 확인해야 한다.
자동 검수 승인도 하지 않는다. 초기 값은 `human_review=PENDING`, `quality_approved=false`다.

## 검증과 현재 한계

2026-10-23 밝은 컬러 프리셋의 v2 저장본은 [vivid-pop-v2.json](../prompts/cosmetics/vivid-pop-v2.json)이다. 원본 라벤더 병의 무광과 펌프의 원래 반사 수준을 보존하도록 배경/제품 조명을 분리했다. 비교 실행기의 `--publish-prompts`는 B11 최초 템플릿을 재등록하는 초기화 옵션이다. 이미 운영 중인 v2를 사용할 때는 이 옵션을 빼고 production을 조회한다. 초기화 옵션으로 v2를 의도치 않게 이전 지시로 덮지 않는다.

오프라인 테스트는 3개 프리셋과 레이아웃의 원문 동일성, 선택 연출, 원격 버전 변경에 따른 해시 변경, 잘못된 스냅샷의 호출 전 거절, 전송 본문, 추적 실패 격리를 확인한다.
실제 서버 등록·실제 이미지 생성·Langfuse 읽기 검증은 별도 실행 기록에서 확인한다. 코드와 테스트만으로 연결 완료를 주장하지 않는다.
기존 일반 이미지 서버의 모든 호출을 자동 추적하는 기능은 아니다. 관리 프롬프트를 고정하고 추적 함수를 사용하는 호출 경로에 적용된다.

공식 문서 확인 기준: 2026-10-23.
[프롬프트 생성·조회](https://langfuse.com/docs/prompt-management/get-started),
[버전과 라벨](https://langfuse.com/docs/prompt-management/data-model),
[프롬프트와 실행 연결](https://langfuse.com/docs/prompt-management/features/link-to-traces),
[현재 SDK](https://langfuse.com/docs/observability/sdk/overview),
[추적 검수 기준](https://langfuse.com/docs/observability/best-practices).

### 2026-10-23 추가 비교 — 비율 보존 프롬프트 v3

- `beautyai/cosmetics/vivid-pop` v1과 v3를 비교했다. 같은 입력 3장·모델·설정에서 제품 폭·길이·캡 비율 보존 문구만 추가했다. 정면 사진의 시점과 형태를 기준으로 위치 이동·등방 확대/축소를 요구한다.
- v3 프롬프트와 [새 trace](https://jp.cloud.langfuse.com/project/cmt966agt0007ad0jn1c3s4up/traces/debacc2ea0d95ae326fde1ec3168aff4)를 원격 조회했다. 두 실행의 평가 점수 16개(각 8개)는 trace·observation 연결과 값도 확인했다. 배포용 프롬프트 원본은 `prompts/cosmetics/vivid-pop-v3.json`이다. [W&B 비교](https://forge.coreweave.com/wandb/uyt5041-alphaflower/beautyai-image-experiments/runs/cmp-86cd9d87740e2962079b)는 `READBACK_VERIFIED`이며 파일 12개 해시·표 2행·관련 실행 3개의 현재 정책 링크를 확인했다.
- v3 실제 생성 1회: 25.674823초·추정 $0.050838. 이전은 27.393523초·추정 $0.050008. 두 AI 점수는 81/100이며 개선은 미확정이다.
- 비율 차이는 **사용자선택**으로 표시한다. 자동 탈락·자동 재생성은 하지 않는다. 과거 형태 3/5 기록을 보존하며 사람 검수·출시 승인·청구 검증은 별도로 남긴다.
