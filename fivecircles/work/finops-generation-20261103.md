# 생성당 추정 비교 추가 · 2026-11-03

작업 FINOPS-GENERATION-20261103. 기존 미커밋 FinOps 변경을 보존한다.
계약: ../architecture/contract/finops-generation-estimate.md.
G0 EGM5단계 완료, 지표 wire/분모/제외 규칙 수리 확인. G0~3 완료. 실행 native, 무관한 기존 예산 검토를 다시 실행하지 않는다.

| 단계 | 목적 | 현재 판정 |
|---|---|---|
| G0 | 비교·제외·누적 여정 측정 계약/TODO | DESIGN_REVIEWED |
| G1 | 명시 pair의 생성당 추정 계산 | 220 related PASS / 0.16s |
| G2 | 화면·W&B summary/Table 호환 추가 | 321 related PASS / 1.34s |
| G3 | 실제20건/제외6건·브라우저·전체 회귀 | PASS · 전체2030 / W&B READBACK_VERIFIED |

생 프롬프트부터 완료까지의 수집 계측은 후속 TODO다. 기존26건으로 그 전체비용을 추정하지 않는다.
유료 이미지 호출/원격 게시/배포/푸시/PR은 이번 로컬 검증의 증거로 대체하지 않는다.

G1 LOCAL 수령: generation_costs.py와51신규테스트, accounting/savings 포함220 PASS. NoHTTP/SDK/원장변경. G2는 검토된 동일계약 재사용.

G3 실행 범위 명확화: 사용자 요청의 W&B 표시를 확인하기 위해 기존 SDK 계정(uyt5041, entity uyt5041-alphaflower) 로그인만 검증했다. G2 통과 후 기존 PRIVATE 프로젝트 beautyai-image-experiments에 저장 보고서만 명시 동기화한다. 이미지 API 호출/생성은 하지 않는다. 새 계산 지표와 별개인 기존 publisher 권한·PRIVATE/readback 경로를 재사용한다.

G2 LOCAL 수령: 화면의선택생성비교와 W&B20열표/summary/조건부스키마. 관련321 PASS. 독립구현검토필수지적없음. 기존report ID/JSON/HTML의선택입력없는경로회귀포함.

## G3 최종 수령
- 저장자료 새보고서: outputs/finops-generation-20261103/report/index.html, report.json. 원본/과거보고서 보존.
- 생성당추정감소1.216849052%, 비교20건/10쌍·제외6건/0쌍·관측률76.923076923%. 채택광고/제작전체비용 미확인유지.
- 첫 전체2021 PASS 후 실제W&B summary null생략 codec오류 발견. summary에만expected-null누락 허용, known값누락/null→0차단, config/artifact/Table엄격유지. TARGETED독립코드리뷰PASS.
- 수리후 전체2030 passed/32.80s(-W error), FiveCircles/diffcheckPASS.
- W&B실제 run compare-e63b12dafbb37a9bd386 finished/PRIVATE/readbackverified. 두번째동기화deduplicated=true, 보고서재업로드와이미지생성없음. remote-summary.json, report/cost-wandb-sync.json.
- 브라우저local390px/넘침없음·20/6표시, 원격W&B생성표/Finished/1.217표시확인. browser-check.json.
- 코드 feature/image-model 로컬미커밋. push/PR/GCP배포없음. FIN-L1~3은후속계측/협의로남김.
