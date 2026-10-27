# 2026-10-27 검증 근거

기준 커밋: `a062c19b5bd16a77a9330f84cbbd2dd321f1b86d`. 아래 링크는 변경되지 않는 커밋에 고정했다.

- [체크리스트 원본](https://github.com/uyt5041-lab/beauty-ai-models/blob/a062c19b5bd16a77a9330f84cbbd2dd321f1b86d/docs/imageTodo.md): 전체 193개. 공유용 보고일 체크는 48개(24.9%).
- [K1~K3 시험 기록](https://github.com/uyt5041-lab/beauty-ai-models/blob/a062c19b5bd16a77a9330f84cbbd2dd321f1b86d/fivecircles/test/backend-adapter-20261027.md): 최종 1,363 PASS, 23.80초. 단계별 시험을 중복 합산하지 않음.
- [릴레이 단계 기록](https://github.com/uyt5041-lab/beauty-ai-models/blob/a062c19b5bd16a77a9330f84cbbd2dd321f1b86d/fivecircles/work/relay-korean-adapter-resize.md).
- [연결 어댑터 사용법](https://github.com/uyt5041-lab/beauty-ai-models/blob/a062c19b5bd16a77a9330f84cbbd2dd321f1b86d/docs/backendModelAdapter.md).
- [목표 흐름](https://github.com/uyt5041-lab/beauty-ai-models/blob/a062c19b5bd16a77a9330f84cbbd2dd321f1b86d/fivecircles/architecture/spec/backend-adapter-target.mmd) · [현재 흐름](https://github.com/uyt5041-lab/beauty-ai-models/blob/a062c19b5bd16a77a9330f84cbbd2dd321f1b86d/fivecircles/architecture/spec/backend-adapter-current.mmd).
- [워터마크 정책 시험](https://github.com/uyt5041-lab/beauty-ai-models/blob/a062c19b5bd16a77a9330f84cbbd2dd321f1b86d/fivecircles/test/watermark-policy-20261027.md) · [Langfuse 재조회](https://github.com/uyt5041-lab/beauty-ai-models/blob/a062c19b5bd16a77a9330f84cbbd2dd321f1b86d/prompts/cosmetics/watermark-20261027-readback.json).

워터마크 새 버전은 clean-studio 2, premium-minimal 2, vivid-pop 4다. 기존 비율 실험 버전과 과거 요청 해시는 유지했다. 새 실제 이미지 생성 및 워터마크 제거 품질 시험은 하지 않았다.

현재 완료 범위는 모델 내부 구현·모의 연결·문서 게시다. 서비스 HTTP 연결·서버 승인·팀 예산 기록·최종 한글 합성·팀 인수·실제 품질 평가는 별도다. 오늘 새 유료 API 호출은 0회다. 실제 비용 증빙이 없는 값을 청구액으로 기록하지 않았다.

[전체 목록](full-todo.md) · [집계 데이터](progress.json) · [계획일 보정표](schedule-map.json)
