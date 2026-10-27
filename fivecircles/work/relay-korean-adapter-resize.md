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

K1 delivery: 2c0a4846618bc51636d721269dea19975bdab4b8 — 원격 동일 SHA 확인.
K2: PASS — 123 tests (-W error), 실제 multipart Fake transport·SQLite 재시작/동시 요청/변경 입력/불명확 응답 시험. CD NOT_APPLICABLE (라이브러리, 서버 미배포). CONTINUE_WITH_NEXT_FORM: K3 원본 보존 리사이즈.

K2 delivery: 9b36dc5cb007bb947deb43250c82937004e10e1e — 원격 동일 SHA 확인.
K3: PASS — 연결/리사이즈 35 tests. 예제 fake1회·재전송1회 결과 재사용, paid0, 최종1080×1350. 전체 1363 passed / 23.80초 / -W error.
Repair: K2 조회 전용 영수증에도 성공·실패의 model/request SHA가 들어가도록 보완. 동시 재전송 경과 시간을 덮어쓰지 않고 원장의 시각 차이를 사용.
CD: NOT_APPLICABLE — 모델 라이브러리이며 팀 서버 배포 권한/작업은 범위 밖.
STOP_WITH_REASON: 사용자 지정 K1~K3 구현 완료. 다음 전체 한글 합성·10장 유료 실험·백엔드 HTTP/팀 예산 연결은 별도 범위. 실제 서비스 전체 도달은 PARTIAL.
