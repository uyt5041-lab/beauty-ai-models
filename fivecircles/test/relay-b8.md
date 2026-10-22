# 릴레이 B8 검증 · 동일 작업 ID 제출 조회·조정

2026-10-22 · plan/models · 전체 제품 판정 **GAP/PARTIAL**

## 계약과 구현

- `HTTPImageGateway.submit_and_wait`는 기존 submit/get/wait와 wire/hash를 유지한 조정 메서드다. 요청 snapshot을 고정하고 하나의 monotonic deadline으로 PUT·GET·대기를 묶는다.
- PUT은 한 번만 보낸다. 제출 응답이 불명확하면 같은 ID GET으로 조회하며 새 ID 발급·자동 재제출·서버 취소를 하지 않는다.
- 404는 미접수 증거가 아니다. deadline까지 유효 job 미관찰은 SubmissionUncertain, 관찰 후 완료 미확인은 JobStillPending이다.
- 작업 ID·입력 hash·성공 결과 종류를 확인한다. 인증·프로토콜·입력 충돌과 terminal 실패는 오류로 유지한다.
- CLI prepare/render가 새 메서드를 사용한다. 기존 job ID 출력과 안전한 오류 코드를 유지한다.

## 실행 검증

- root 전체 회귀: `.venv/bin/python -m pytest -q -W error` → **521 passed in 15.01s**. 신규 84 unit + 4 실제 TCP 테스트.
- 실제 TCP 서비스에 PUT이 도달하고 SQLite commit된 것을 별도 connection으로 확인한 뒤 클라이언트 경계에서 응답 유실/503을 주입했다. 같은 ID GET으로 성공을 확인했으며 PUT 1회·processor 1회·DB job 1개였다. 결과 파일 다운로드/decode/hash·인증 누락401·다른 project404를 확인했다.
- 기한 시험: 유효 job을 볼 수 없으면 SubmissionUncertain, 관찰 뒤 미완료면 JobStillPending. 두 경우 모두 원래 worker가 계속 실행됐고 새 PUT 없이 기존 wait로 완료했다. 단위 검증은 하나의 시간 예산·일시 오류·404·hash/kind·외부 취소·요청 snapshot·정의된 거절을 포함한다.
- 검토 수리: failed.error_code에 담긴 임의 원문을 오류 코드로 노출할 수 있어 `_decode`가 첫 글자 ASCII 대문자·이후 대문자/숫자/_의 총 1–80자와 failed/interrupted 상태만 허용하도록 제한했다. malformed code·다른 상태의 code는 IMAGE_PROTOCOL_ERROR, 정상 코드는 유지한다. 기존 submit/get/wait 경로 포함 회귀·재검토 PASS.
- wheel build·격리 lazy import PASS. wheel SHA-256: `8b11c376e19eecdd4eb1dac8ffd4e69f8c0775abc1687f191a55b299ac1f4d43`.
- B8 검증에는 실제 AI 모델·운영 proxy·광고 품질·CUDA 검증이 포함되지 않는다. B6/B7의 실제 MPS 증거는 해당 배치 기록에 보존한다.

## 시각 검증과 후속

- [목표/현재 흐름](../architecture/spec/image-runtime-flow.html): **브라우저 검증 PASS** (Task Space 144). PNG 2개 decode(552×2540, 1568×3448), 제목 7개·범례 5개·GAP 17행·우선순위 7행, 문서/화면 폭 1492px로 가로 넘침 없음. 1492×4473 최종 스크린샷을 직접 확인했다.
- [스크린샷](playwright-screenshots/image-runtime-flow-b8.png).
- 다음 후보는 로컬 cold/warm·크기별 반복·단계 trace/메모리 관찰이다. ledger 경과를 순수 GPU 시간으로 해석하지 않는다.
- 실사진 품질·GCP CUDA·렌더러·사람 안전/품질·팀 인수는 별도다. Git publication은 root가 별도로 검증한다.

원본 TODO 4-A의 응답 조회·새 ID 억제 2개만 추가 체크했다(누적 31개). 재제출 정책은 자동 재전송 없이 기존 ID 조회를 유지하는 범위다.
