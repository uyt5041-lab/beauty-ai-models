# 11월 3일 기록 근거

기준 시각: 2026-11-03 19:28 KST. 로컬 원본의 SHA-256은 [evidence-manifest.json](evidence-manifest.json)에 있다. 원본·생성 이미지·요청 본문은 공개하지 않는다.

| 근거 ID | 확인한 내용 | 범위·제한 |
| --- | --- | --- |
| `r-measured-wandb-20261103/summary.json`, `validation.json` | 실측6장·6회·동시6·재시도0·52.908초·45,851토큰·추정$0.555493, 6건 측정 검증 | 실제 청구null/UNRECONCILED. 과거24건 비용미상 |
| `r-measured-wandb-20261103/wandb/history-and-measured/upload-receipt.json` | W&B 이력30건 READBACK_VERIFIED | 전체 프로젝트 호출 수가 아님 |
| `r-measured-wandb-20261103/comparison-view/upload-receipt.json` | 이력30행·테마6행·기계 리뷰24행·인간 피드백3행, 이미지32개 검증 | PRIVATE, 팀원 열람 권한·품질 인수는 별도 |
| `r2-preset-library-20261103/implementation-receipt.json`, `preparation-receipts.json` | 프리셋6종·관련294시험·미리보기6·추가API0, 코드 독립 리뷰 지적0 | committed=false/pushed=false. 새 프리셋 실생성·다른 제품 NOT_RUN |
| `finops-generation-20261103/delivery-receipt.json` 및 W&B verified receipt | FinOps 당시 전체2,030시험 PASS, 비교20건/10쌍·제외6건·추정 생성당1.216849052% 감소, 두 번째 동기화 중복 제거 | 로컬 코드 게시 전, 실제 절감/청구/전체 흐름 원가 미확인 |
| 공개 MVP `096053e`의 입력·합성·크기·제공자 경계 | 카피600/2000/600자 제한, 영역/글리프 차단,4:5/1080×1350,n=1, 분석실패·비화장품 차단 | 새 전체 회귀나 최신 UI/서빙/GCP 실행을 하지 않음 |
| [11/02 팀 회의록](https://app.notion.com/p/2026-11-02-40cb13f77dbd80d3be9cf3219d4bf590) | 우선7개·추가 프리셋·미래 계획3개 | 로그인된 Ego에서11/03 직접 읽음. 참고 회의록은 덮어쓰지 않음 |

[이력30건](https://forge.coreweave.com/wandb/uyt5041-alphaflower/beautyai-image-experiments/runs/overview-73c99e92cd91d0b0d154) · [테마별 비교](https://forge.coreweave.com/wandb/uyt5041-alphaflower/beautyai-image-experiments/runs/theme-view-1fa2862e06e1d604c0cd) · [FinOps 비교](https://forge.coreweave.com/wandb/uyt5041-alphaflower/beautyai-image-experiments/runs/compare-e63b12dafbb37a9bd386)

실제 구현 TODO 원본: [고정 커밋096053e](https://github.com/uyt5041-lab/beauty-ai-models/blob/096053eafacb6a77796126364f37fba3f2b8e593/docs/imageTodo.md). 공유 마일스톤: [plan/models](https://github.com/uyt5041-lab/beauty-ai-models/blob/plan/models/docs/imageTodo.md). 이전11/02 기록은 보존한다. 신규 문서 게시의 원격 SHA·링크 검증은 별도 validation.json에서 확인한다.
