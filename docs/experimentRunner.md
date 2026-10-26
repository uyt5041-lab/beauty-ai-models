# 실험 호출 전 미리보기

제품 사진과 프롬프트를 먼저 확인한다. 실제 실행은 저장한 계획을 사용한다. 기본 준비 명령은 이미지 API를 호출하지 않는다.

## 1. 입력 준비

- 같은 제품 사진 2~5장을 준비한다. 첫 사진이 주 시점이다.
- 배경 참고 사진은 0~5장이다. 제품 사진과 역할을 나눠 넣는다.
- JPEG, PNG, WebP를 지원한다. 파일당 원본 20MiB, 16MP 제한을 유지한다.
- 방향과 색 프로필을 정규화한 뒤 PNG로 저장한다. 정규화 파일도 각각 20MiB 이하여야 한다. 합계는 100MiB 이하다.
- 같은 파일이나 정규화 후 같은 이미지가 중복되면 멈춘다. 원본 파일은 바꾸지 않는다.
- 파일 검증은 같은 제품인지 확인하거나 광고 사용을 승인하는 절차가 아니다. 잘림·손·가격표·스티커는 사람이 확인한다.

## 2. 호출 없이 준비

프로젝트 루트에서 실행한다. 아래 파일 이름은 예시다. 실제 파일 경로로 바꾼다.

```bash
.venv/bin/python -m beauty_ai.experiment_runner prepare \
  --product uploads/product/front.jpg \
  --product uploads/product/side.jpg \
  --reference uploads/reference/studio.jpg \
  --preset vivid_pop \
  --prompt-version cosmetics-v1 \
  --product-description "One cosmetic lotion bottle. Preserve its original packaging, material and printed label." \
  --quality medium \
  --max-calls 1 \
  --output outputs/experiment-trials/first-preview
```

참고 사진이 없으면 `--reference` 줄을 뺀다. 경로에 공백이 있으면 큰따옴표로 감싼다. 출력 폴더는 `outputs` 아래의 새 폴더여야 한다. 기존 결과를 덮어쓰지 않는다.

선택할 프리셋은 `clean_studio`, `premium_minimal`, `vivid_pop`이다. `--preset`을 여러 번 쓰면 선택 순서대로 계획한다. `--repeats 2`는 각 프리셋을 두 번 계획한다. **프리셋 수 × 반복 수**가 `--max-calls`보다 크면 준비 단계에서 멈춘다. 최대 호출 수의 기본값은 1이며 허용 범위는 1~30이다. 반복 수는 1~10이다.

출력:

| 파일 | 확인할 내용 |
| --- | --- |
| `index.html` | 정규화한 입력 사진, 제품/참고 순서, 최종 프롬프트, 버전, 옵션, 예정·최대 횟수 |
| `plan.json` | 전체 계획과 SHA-256. SHA-256은 내용이 같은지 확인하는 해시값이다 |
| `cases/case-001/request.json` | 고정한 요청과 자산 ID |
| `cases/case-001/provider-prompt.txt` | 실제 제공자에게 보낼 문장 |
| `inputs/` | 정규화한 입력 사본. 원본·정규화 해시는 계획에 기록한다 |

미리보기는 준비 시점의 기록이다. 이후 실제 실행 상태는 `report.json`에서 확인한다.

## 3. Langfuse 버전 선택

`cosmetics-v1`은 저장소의 로컬 템플릿이다. Langfuse의 숫자 버전 1과 구분한다. 기존 Langfuse를 유지하며, 정확한 버전을 저장한 JSON을 입력으로 사용한다. 준비·실행 명령은 `production`을 다시 읽거나 프롬프트를 게시하지 않는다.

```json
{
  "vivid_pop": {
    "name": "beautyai/cosmetics/vivid-pop",
    "type": "text",
    "version": 1,
    "prompt": "Langfuse에서 가져온 실제 전체 템플릿"
  }
}
```

위 JSON은 모양 설명이다. 예시 문장 그대로는 필수 변수가 없으므로 거부된다. 실제 템플릿에는 제품 설명·색·연출·배치·참고 지시의 기존 5개 변수가 있어야 한다. 선택한 프리셋의 스냅샷만 넣는다.

준비 명령의 옵션을 `--prompt-version 1 --prompt-snapshot outputs/prompt-snapshots.json`으로 바꾼다. 버전·이름·형식·변수가 맞지 않으면 멈춘다. 공급받은 JSON 자체가 원격 출처 증명은 아니다. 원격에서 버전을 읽은 기록과 함께 보관한다.

기존 밝은 광고 v2에는 특정 라벤더 병·코럴 펌프 묘사가 있다. 새 제품에 맞는지 최종 문장을 확인한다. 제품마다 원본 재질을 유지하며 모든 제품을 무광으로 바꾸지 않는다.

## 4. 검토한 계획 실행

**아래 명령은 실제 비용이 발생할 수 있다.** 이번 구현 검증에서는 실행하지 않는다. 유료 실행 전에 팀 키·팀 잔액·사진 적합성을 확인한다. 예산 값은 운영상 예약액이며 실제 청구 상한이 아니다.

```bash
.venv/bin/python -m beauty_ai.experiment_runner execute \
  --plan outputs/experiment-trials/first-preview/plan.json \
  --expect-sha256 미리보기에_표시된_64자리_해시 \
  --env-file .env \
  --budget-usd 1 \
  --reservation-usd 1 \
  --execute
```

위 예약액은 사용법 예시이며 팀 예산 승인이 아니다. 전체 예약액은 `예약액 × 계획 횟수` 이상이어야 한다. 실제 비용은 영수증과 청구를 대조해야 한다. 모르는 비용은 `null`로 남긴다.

- 계획·사진 사본·요청·프롬프트가 달라지면 호출 전에 멈춘다.
- 시도 슬롯은 네트워크 전송 전에 저장한다. 실패와 응답 미확인도 슬롯을 사용한다.
- 같은 계획은 한 번만 실행권을 얻는다. 재시작·동시 실행으로 횟수를 초기화하지 않는다.
- 한 건이 실패하거나 미확인이면 후속 건은 실행하지 않는다. 자동 재시도하지 않는다.
- 오류 뒤 실행 원장이나 결과 폴더를 지워 재시도하지 않는다. 결과와 영수증부터 확인한다.
- Langfuse 또는 W&B 기록 실패는 이미지 재호출 사유가 아니다. 새 프롬프트는 자동 게시하지 않는다.

기존 `scripts/compare_cosmetics_presets.py`도 같은 `prepare`/`execute` 명령으로 연결된다. 과거 단일 `--source --execute` 명령은 새 계획 없이 실행되지 않는다.

## 5. 완료 판정의 범위

[단계별 기록](../fivecircles/work/experiment-runner-stages.md)에 테스트 결과를 남긴다. 모의 응답을 사용한 연결 성공은 실제품 광고 품질, 확정 청구액, 팀 서비스 인수를 뜻하지 않는다. 공유 사진은 로컬 입력 처리 시험에만 사용했다. 제품이 잘린 사진은 광고 품질 평가 표본으로 승인하지 않았다.

원본·미리보기·생성 이미지는 공개 Git에서 제외한다.

## 6. W&B에도 결과 저장

W&B는 실험 결과 비교용이다. Langfuse의 프롬프트 버전 관리는 유지한다.
실제 실행 명령에 아래 두 옵션을 함께 붙이면, 로컬 결과를 먼저 저장한 뒤 W&B에 등록한다.

```text
--wandb --wandb-entity uyt5041-alphaflower
```

프로젝트 기본값은 `beautyai-image-experiments`다. 다른 프로젝트는 `--wandb-project 이름`으로 지정한다.
W&B SDK의 기존 로그인을 사용한다. `.env`의 이미지 API 키를 W&B로 보내지 않는다.
W&B 옵션이 없으면 로컬에만 저장한다. `prepare`는 이 옵션을 받지 않으며 원격 업로드하지 않는다.

등록만 다시 하려면 아래 명령을 쓴다. 이미지 API를 호출하지 않는다.

```bash
.venv/bin/python -m beauty_ai.experiment_runner sync-wandb \
  --plan outputs/experiment-trials/first-preview/plan.json \
  --expect-sha256 계획에_표시된_64자리_해시 \
  --entity uyt5041-alphaflower
```

`--prepare-only`를 붙이면 업로드할 파일만 로컬에 묶는다. 실행 전 미리보기나 모의 실행은 실제 API 실험으로 올리지 않는다.

- 생성 결과: `report.json`, `cases/…/image.png`에 남는다.
- W&B 등록 결과: `wandb-sync.json`에 별도로 남는다. 실행 보고서를 바꾸지 않는다.
- `READBACK_VERIFIED`: 원격 이미지·프롬프트·메타데이터를 다시 읽고 일치했다.
- `INCOMPLETE`: 등록이나 확인이 끝나지 않았다. 원래 생성 결과를 유지하며 CLI는 실패 코드로 끝난다.
- `SKIPPED_NO_LIVE_RECORDS`: 실제 실행 기록이 없어 업로드하지 않았다.
- 같은 ID의 같은 결과는 중복 생성하지 않고 다시 확인한다. 내용이 다르면 멈춘다.
- 새 실행의 단가·청구가 미확인이면 비용은 `null`이다. 과거 B12 단가를 가져와 채우지 않는다.
- 시도 수는 실제 POST/과금 건수와 다르다. 확정 호출 수를 알 수 없으면 `null`이다.

현재 수집 검증은 저장된 계획과 현재 지원 템플릿의 일치도 확인한다. 템플릿 계약이 바뀐 과거 결과는 억지로 복원하지 않고 멈춘다.

[연결 검증 기록](../fivecircles/work/experiment-wandb-bridge-stages.md)
