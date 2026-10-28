# VLM 결과 연결 릴레이 · 2026-10-28

0. native 직접 수행 / feature/image-model / PR 없음 / 실제 유료 호출0.
1. 목표: 다혜 ProductAnalysisResult → 성공·화장품 확인 → 사진/분석/한국어 프롬프트 결속 → 공통 한국어 지시 → 기존 영속 API 래퍼 → 원본/축소 PNG.
2. 현재 GAP: 분석 수신·차단 없음, 공통 지시는 영어. 순서 A1(4+3+2+2=11) → A2(4+3+2+2-1=10) → A3(3+2+1+2-1=7). 사용자 요청 순서 우선.
3. 계약: docs/textImageHandoff.md / docs/imageTodo.md 실행 원본. HTTP·외부 팀 합의는 미확정.
4. A1 스키마, A2 어댑터·실패 차단, A3 정책 버전·문서. 단계 시험 후 일반 push·원격 확인 뒤 다음 단계.
5. Validation: 각 단계 targeted 및 최종 전체 pytest -q -W error. 모델 품질 NOT_RUN.
6. Repair: 실패하면 같은 단계 수정 후 재시험.
7. Publication: 일반 push, 원격 SHA 검증. CD NOT_APPLICABLE — 라이브러리이며 서버 배포 범위 아님.
8. Report: 목표/현재 Mermaid·차이표·로컬 HTML. 서버 E2E PARTIAL 별도 표시.
9. STOP 조건: A1~A3와 10/27 마일스톤 노션 체크 반영 완료. 그 밖 한글 합성/유료 실험/서빙 수정은 이번 범위 밖.
10. 노션 체크: 10/27 고정 원본 a062c19와 마일스톤을 기준으로 하며 오늘 코드를 소급 체크하지 않는다. 협업 기록 별도 포함.

A1 PASS: tests/test_product_analysis.py 13 passed / 0.06초. 설정 검사 PASS. API 0회. 수신 타입만 추가하며 아직 실행 경로에 연결하지 않았다. A2로 이어간다.
정정: 노션 본문/링크는 수정하지 않는다. 연결된 plan/models/docs/imageTodo.md를 마일스톤 10/27 기준으로 갱신한다.
