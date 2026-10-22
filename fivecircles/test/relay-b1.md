# B1 검증 · PASS_WITH_RISKS

2026-10-22 KST. 계약: [image-runtime](../architecture/contract/image-runtime.md).

- `.venv/bin/python -m pytest -q -W error`: **72 passed** (기존 36 + assets 19 + canvas 17).
- 실제 임시 파일/SQLite: project 경계, decode/정규화, hash/metadata 위조, symlink, 재시작 조회, 동시 저장을 검증.
- 제품 alpha crop/fit/좌표/실제 alpha Safe Zone, mask, 불투명/부분 투명 검사 검증.
- 저장소 루트에서 `python -m examples.prepare_product`: 합성용 fixture 입력 → 정상화 source + candidate RGBA + mask + preview 4개 파일, needs_approval 유지, opaque_preserved 확인.
- 직접 파일 실행은 이 로컬 editable 설치의 경로 미반영으로 import 실패. 루트 모듈 실행 방법을 README2에 명시하고 재현 PASS. import 경로를 제품 코드에서 강제로 주입하지 않음.
- [오류·수정 기록](errorlogs/2026-10-22-relay-b1.md): Pillow 테스트 deprecation 수정 및 GCP 인증 blocker.
- [목표/현재 HTML](../architecture/spec/image-runtime-flow.html): Mermaid PNG 2개·필수 제목 7개·범례 5개·GAP/점수 표 렌더 확인, 가로 overflow 없음. [스크린샷](playwright-screenshots/image-runtime-flow-b1.png).
- 전체 목표는 **GAP/PARTIAL**. API·worker·실모델·제품 승인·렌더러·사람 품질 평가는 미구현/미검증. B2가 다음 연결 작업.
- B1은 로컬 Python 라이브러리/CLI이므로 운영 CD는 NOT_APPLICABLE. Git publication은 push와 원격 SHA readback으로 별도 기록한다.
