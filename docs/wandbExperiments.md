# W&B 이미지 실험 기록

Langfuse는 프롬프트 이름·버전·실행 관찰을 관리한다. W&B는 원본/결과 이미지, 당시 프롬프트, 시간, 비용, 평가 상태를 비교한다. W&B 업로드는 이미지 모델을 다시 호출하지 않는다.

## 현재 상태

2026-10-23 기준이다. 기존 실행 20건을 비공개 W&B 프로젝트에 올렸다. API는 7건이고 로컬 모델은 13건이다. 성공 18건과 실패 2건을 모두 기록했다. 저장된 파일과 비교표를 다시 읽어 원본과 같은지 확인했다.

[W&B 비교 화면 열기](https://forge.coreweave.com/wandb/uyt5041-alphaflower/beautyai-image-experiments/runs/overview-ce25166dc920554527ea). 프로젝트 접근 권한이 있는 계정으로 로그인한다.

API 비용의 추정 합계는 **$0.150340**이다. 원본에 있던 추정치 4건과 기록된 사용량으로 다시 계산한 3건을 합한 값이다. 실제 청구액과 팀 전체 잔액은 미확인이다. 이 금액을 확정 지출이나 팀 총사용액으로 쓰지 않는다.

처음 사용하는 사람은 [쉬운 한글 작업 예시](wandbFinopsWorkExamples.md)부터 본다. 완료한 일과 남은 일은 [이미지 TODO](imageTodo.md#단계-3-c-wb-실험-기록과-finops-평가)에 있다.

| 용어 | 쉬운 뜻 |
| --- | --- |
| FinOps | 비용을 확인하고 예산을 지키는 작업 |
| run | W&B에 저장한 실행 기록 한 건 |
| artifact | 이미지·프롬프트·설정을 묶은 저장 자료 |
| hash | 파일이 바뀌었는지 비교하는 값 |
| `null` | 아직 알 수 없는 값. 0과 다름 |
| `BLOCKED` | 필요한 근거가 없어 판정을 보류한 상태 |

## 설치와 실행

저장된 실험 파일이 있는 `beautyAi` 폴더에서 실행한다. Git에서 받은 코드만 있고 `outputs/`가 비어 있으면 옮길 실험 기록도 없다. 비용 없는 연습은 [가상 작업 예시](wandbFinopsWorkExamples.md)를 사용한다.

```sh
python -m pip install -e '.[test,observability,experiments]'
wandb login
python -m beauty_ai.wandb_experiments \
  --project-root . \
  --output outputs/wandb-experiments/2026-10-23
```

기본은 오프라인 준비다. `prepared.json`에서 건수·비용 출처를 확인한다. 업로드는 같은 명령에 `--publish`를 붙인다. 다른 계정/프로젝트를 사용할 때는 `--entity TEAM --project PROJECT`를 명시한다. 기본 entity는 로그인 계정의 default entity이고 기본 프로젝트는 `beautyai-image-experiments`다. 실제 키를 명령 인자나 Git에 넣지 않는다.

이 패키지는 `.env`를 자동으로 읽지 않는다. W&B SDK의 기존 인증을 사용하며 Langfuse 설정을 변경하지 않는다. 프로젝트가 없으면 비공개로 만들고, 같은 이름의 공개 프로젝트가 있으면 자동 변경하지 않고 중단한다.

## 어디서 비교하는가

업로드가 끝나면 `upload-receipt.json`의 `overview_url`을 연다. 비교 run에는 다음 Table이 있다.

| Table | 내용 |
| --- | --- |
| `all_experiments` | API·로컬 모델 성공과 실패 전체 |
| `api_comparison` | 이미지 API, 입력/출력, 저장된 프롬프트·버전, 시간, 비용 |
| `local_model_comparison` | 보존한 BiRefNet/SDXL 실험; 과거 실험이며 현재 MVP 필수 아님 |
| `finops` | 비용 출처·확인율·예산 상태·미확인 항목·FinOps40% 비중 |

각 행의 `run_url`에서 개별 실행을 열고, `langfuse_url`에서 기존 프롬프트 관찰로 이동한다. Table은 첫 입력/출력을 미리 보여준다. 관련 이미지와 당시 저장된 프롬프트는 `image-experiment` artifact에 있다. `evaluation-plan` artifact는 [평가 계획](imageEvaluationPlan.md)과 기계 판독 정책·빈 실제품 manifest를 보관한다.

프롬프트가 남아 있는 범위는 실행마다 다르다. B10의 초기 생성·편집은 긍정 지시문만 남아 있다. 일부 누끼 실행은 문장과 버전이 미기록이다. 이 값은 `null`로 두고, 현재 템플릿으로 과거 문장을 만들지 않는다. `record.json`의 `data_provenance.prompt_scope`에서 전체 문장·긍정 지시문만 저장·미기록을 구분한다.

비교할 때 모델·operation·크기·quality·원본 해시·측정 구간을 함께 본다. 예를 들어 B12는 내부 job/대기/다운로드 구간, B13은 직접 processor 구간이어서 19.258초와19.517초를 같은 조건의 성능 우열로 해석하지 않는다. W&B run 생성 시각은 이번 가져오기 시각이고 `original_started_at`이 당시 실행 시각이다.

## FinOps는 종합 평가의40%

잠정 종합 비중은 **이미지40 / FinOps40 / 서비스20**이다. 비용의 내부 비중은 광고당 총원가30 / 생산 낭비20 / 공유 예산25 / 청구 대조25다. 임시 기준과 계산 입력은 [평가 계획](imageEvaluationPlan.md)을 따른다. 팀이 확정한 기준은 아니다.

- `estimated_cost_usd`: 저장된 추정값 또는 당시 사용량과 명시한 역사적 단가로 재구성한 값. `estimate_origin`과 `historical_rate_basis`가 근거다.
- `actual_cost_usd`: 확인된 실제 청구액. 미확인은 `null`이다. 확정 실비0과 다르다.
- 가져오기 요약의 청구 대조율은 API 기록 전체 중 확정 청구를 연결한 비율이다. 평가 계획의 정산 유예24시간이 지난 영수증 대조율과 분모가 다르므로, 이 값만으로 정산 지연을 판정하지 않는다.
- 성공 광고당 원가: 사람이 인수한 최종 광고를 분모로 하고 LLM·이미지·그 밖의 가변비를 포함한다. 기술적 PNG 생성 성공을 최종 광고 인수로 세지 않는다.
- 팀 예산: 안내상 OpenAI $30. 팀 계정·공유 사용량·잔액이 확인돼야 소진율을 계산한다. 이미지 실험비만으로 팀 잔액을 추정하지 않는다.
- 연구 실험과 생산 낭비를 분리한다. 계획된 프리셋 비교나 반복 실험을 자동으로 실패/재시도 낭비라고 부르지 않는다.
- 청구·잔액·최종 광고 근거가 없으면 FinOps 점수와 종합 점수는 미확인이고 출시 조건은 `BLOCKED`다.

## 범위·검증·재실행

수집 대상은 알려진 `openai-live-smoke`, `cosmetics-comparison`, `cosmetics-matte-refinement`의 run 폴더와 B6/B7/B9 로컬 실행이다. 새로운 같은 형식의 run은 다시 수집할 수 있다. 임의 PNG·백업·mock·프롬프트 미리보기는 실제 실행으로 올리지 않는다. 다른 형식의 실행기는 collector 연결이 필요하다. 이번 연결은 명시적인 사후 업로드 명령이며 서비스의 매 요청 자동 전송은 아니다.

입력은 읽기 전용이다. 경로·해시·과거 요청을 검증하고, 정규화 JSON·명시한 이미지·프롬프트만 보낸다. 키·env·원본 DB·전체 소스·팀 Notion 원문은 보내지 않는다. W&B run의 Git/코드/시스템 수집을 끈다.

안정적인 실행 ID를 사용한다. 같은 ID와 내용은 기존 run을 다시 검증하며 중복 업로드하지 않는다. 같은 ID의 증거가 바뀌었으면 `WANDB_EXISTING_RUN_CONTENT_CONFLICT`로 멈춘다. 업로드 중 연결이 끊긴 run은 성공으로 표시하지 않는다. 부분 run을 임의 삭제하거나 새 ID로 숨기지 말고 receipt를 확인한 뒤 복구한다.

완료 조건은 원격 run summary 확인, artifact 재다운로드 후 모든 이미지·프롬프트·record 해시 일치, 비교표 행/열 확인, 평가 계획의 바이트 일치다. `READBACK_VERIFIED`만 업로드 완료이며 사람의 이미지 품질 승인이나 팀 인수는 별도다.

[W&B Tables 공식 문서](https://docs.coreweave.com/products/wandb/track/log/log-tables) · [Public API](https://docs.coreweave.com/products/wandb/ref/python/public-api/api) · [SDK 설정](https://docs.coreweave.com/products/wandb/ref/python/experiments/settings)
