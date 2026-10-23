# 10/23 협업일지의 근거와 집계 기준

- 기록 기준: 2026-10-23 19:02 KST.
- 코드 원격 고정 커밋: `79ad0b1e47270b3038e1e084aa5f0b0b6d16834b`. 최신 게시 시각은 2026-10-23 13:51 KST다.
- 수정 전 협업일지 원격: `7fc7426f050b6b074f94202ed399af1a02fe03b3`. 이 커밋의 자식으로 일반 push한다.
- 원본 TODO SHA256: `c3bc2c7962c302799a26cf78ee17d333b05c8e55d05290a053957bdf38010404`.
- 전체 150개, 완료 42개, 나머지 108개. 일지 체크 진행률은 28.0%다.
- 이전 10/22 기록은 130개 중 26개였다. 오늘은 항목 추가와 날짜 범위가 모두 달라졌으므로 같은 분모의 코드 완성률 변화로 해석하지 않는다.
- 코드 checkout과 실제 구현 TODO는 수정하지 않는다. 이후 일정을 먼저 끝낸 실제 체크도 유지한다. 공유용 문서만 보고일 기준으로 필터링한다.
- 진행 중인 미게시 WIP는 당일 완료 수와 성과에서 제외했다. 본문은 게시된 B11~B14 기록 범위다. 뒤이은 실제품 실험이나 실행기 작업은 이 스냅샷의 완료로 합산하지 않는다.

## 날짜 매핑

기본은 원본 단계의 첫 계획일이다. ISO 날짜로 적힌 단계 3-C는 원본에 `기준일: 2026-10-23`과 완료 항목 7개의 날짜·검증 링크가 있어 같은 날로 매핑했다. 이 절의 M1 항목은 10/30, M2 항목은 11/06으로 별도 매핑했다. FIN-03의 ‘유료 실행 전’은 날짜가 없어 일정 미정이다. [매핑 JSON](schedule-map.json)을 보존했다.

이후 일정 20개와 일정 미정 41개는 전부 미체크다. 날짜를 앞당겨 분자를 늘리지 않았다. 일정 범위 안이지만 원본이 미체크인 항목도 미체크로 남겼다.

## 원격 게시된 검증 기록

- [B11 화장품 프롬프트](https://github.com/uyt5041-lab/beauty-ai-models/blob/79ad0b1e47270b3038e1e084aa5f0b0b6d16834b/fivecircles/test/cosmetics-prompts-b11.md): 프리셋·배치·꽃 기본 지시 제거. 866개 테스트 기록.
- [B12 Langfuse·3종 실제 비교](https://github.com/uyt5041-lab/beauty-ai-models/blob/79ad0b1e47270b3038e1e084aa5f0b0b6d16834b/fivecircles/test/cosmetics-langfuse-b12.md): 실제 편집 3회, 원격 관찰 3건 재조회. 전체 934개 테스트 기록.
- [B13 무광 보강 v2](https://github.com/uyt5041-lab/beauty-ai-models/blob/79ad0b1e47270b3038e1e084aa5f0b0b6d16834b/fivecircles/test/cosmetics-matte-b13.md): 같은 가상 제품으로 1회 추가 생성. 추정 $0.027410, 실제 청구액 미확인.
- [B14 W&B·FinOps](https://github.com/uyt5041-lab/beauty-ai-models/blob/79ad0b1e47270b3038e1e084aa5f0b0b6d16834b/fivecircles/test/wandb-finops-b14.md): 기존 20건 업로드·원격 artifact와 표 검증, 수집 중 신규 모델 호출 0회. 마지막 전체 1,078개 테스트 기록.
- [평가 계획](https://github.com/uyt5041-lab/beauty-ai-models/blob/79ad0b1e47270b3038e1e084aa5f0b0b6d16834b/docs/imageEvaluationPlan.md): 이미지40%·FinOps40%·서비스20%의 팀 미확정 기준.
- [한글 예시](https://github.com/uyt5041-lab/beauty-ai-models/blob/79ad0b1e47270b3038e1e084aa5f0b0b6d16834b/docs/wandbFinopsWorkExamples.md): 가상 판정 5개. 실제 평가 표본과 구분한다.
- [Git 전달 근거](https://github.com/uyt5041-lab/beauty-ai-models/blob/79ad0b1e47270b3038e1e084aa5f0b0b6d16834b/fivecircles/work/integrate-log.md): 일반 push와 로컬 동기화 기록.

이 테스트 개수는 같은 테스트 집합의 단계별 실행이다. 합산하지 않았으며 이번 일지 작성에서 모델 테스트를 다시 실행하지 않았다. 문서 생성기의 정책 회귀 7개와 구조 검사는 별도로 수행했다.

## 현재 남은 것

실제 키·공유 잔액·제공자 청구 대조, 실제품 평가셋과 사람 검수, LLM/이미지/렌더러의 전체 서비스 인수는 미완료다. 외부 API 상위 6개와 그 하위 체크를 합쳐 12개이며, 공유 완료는 0/12다. SSOT 연결은 0/12다. 이 두 묶음은 전체 150개에 이미 포함된다.
