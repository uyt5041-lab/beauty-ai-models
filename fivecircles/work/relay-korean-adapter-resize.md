# 릴레이 K1~K3 · 2026-10-27

폼: native / feature/image-model / 유료 호출0 / 단계별 테스트·기록·일반 push 후 다음 단계.
설계: docs/backendModelAdapter.md. 원본 TODO: docs/imageTodo.md.
순서: 사용자 지정 K1→K2→K3. K1 점수 4+3+1+2=10, K2 3+3+2+2-1=9, K3 2+2+1+2=7.
목표: 한국어 수신 → 바이트/승인 메타데이터 변환 → 기존 영속 래퍼 → 결과 바이트 → 원본 보존 축소.
현재: 영어 선언만 지원 → 서비스 연결 GAP → 기존 래퍼 있음 → 최종 축소 GAP.
K1: PASS — targeted 268 tests, -W error. K2/K3: NOT_STARTED.
K1 gate: 영어 해시·한국어 multipart·정책 보존 확인. CONTINUE_WITH_NEXT_FORM: K2 입력 변환/영속 재사용.
범위 밖: 서버 인증·승인·팀 예산·HTTP / 실제 API·광고 품질 / 전체 한글 합성.
Doc: 작성. Validation/Push: 아직 안 함. CD: NOT_APPLICABLE (라이브러리 변경).
