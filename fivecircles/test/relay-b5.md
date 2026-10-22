# 릴레이 B5 검증 · 평가 도구와 작업 시간

2026-10-22 · plan/models · 전체 제품 판정 **GAP/PARTIAL**

- root 전체 회귀: `.venv/bin/python -m pytest -q -W error` → **280 passed in 4.06s**.
- 로컬 mask 지표 담당 검증: 40 tests PASS. 실제 모델·평가 데이터 검증은 아니다.
- 예제 manifest 실제 CLI PASS: case 1건, model_invoked=false. 권리가 unknown이므로 파일 측정을 건너뛴 도구 실행 증거이며 실제 모델·데이터 평가 성공이 아니다.
- 패키지 wheel build PASS. SHA-256: `79bff5bc59cc8bf1319edd0e5d0dd352b3b6180dca67424cf6e85c0679990b2c`. B3의 별도 artifact import 증거와 구분한다.
- 독립 검토에서 FIFO manifest 읽기 대기를 발견했다. nonblocking open·fstat로 수정하고 실제 FIFO 회귀 및 독립 재검증을 통과했다.
- timing 담당 회귀: ledger/API 31 tests PASS(B4 이후 7개 추가). 실제 loopback TCP runtime + 별도 CLI `get-timing` PASS. local uploaded-alpha 작업이 succeeded이며 ledger_elapsed_ms=70.61, clock_basis=server_wall_clock. 이는 접수→terminal 경과이며 GPU 연산 시간·AI 모델 실행이 아니다.

## 구현과 한계

- `evaluation_metrics.py`: white=foreground, threshold 128, 기본 tolerance 2px. mask IoU, 3×3 zero-padding erosion 경계와 정사각 tolerance의 precision/recall/F1. 빈 합집합·양쪽 빈 경계는 None. 학술 benchmark와 동일한 지표 구현이라고 주장하지 않는다.
- `evaluation.py`: 2MiB 이하 manifest, 1~200개 case, 파일당 20MiB/16M pixel 제한. manifest 폴더 아래 상대 경로만 받고 URL·외부 경로·외부 symlink를 거절한다. 파일 hash·크기·환경·mask 지표·불투명 픽셀 결과를 기록한다.
- 미확인 권리는 파일을 측정하지 않고 NOT_ASSESSED. 정답 mask 부재는 None/사유이며, 비교할 불투명 픽셀이 0개면 보존 합격으로 표시하지 않는다. 권리·사람 판정은 선언이며 실제 권리·사람 신원을 검증하지 않는다.
- `quality_approval=NOT_ASSESSED`, `model_invoked=false`. 실제 모델 품질·GPU 시간·VRAM·비용은 None/NOT_MEASURED. 실제 평가 이미지·원문 권리 자료·결과 보고서는 Git 제외 data/outputs에 보관한다.
- `JobLedger.timing`·인증 API·CLI: 저장된 created_at/updated_at의 terminal 경과만 제공. queued/running 또는 음수 시계 차이는 None. 대기·처리·저장을 포함하며 phase/GPU 시간과 다르다. 모델/품질 case와 job의 동일성을 추정하지 않는다.
- CLI는 비공개 출력 파일을 새로 생성하고 기존 보고서를 덮어쓰지 않는다. 오류는 안정적인 code로 반환하며 원본 경로·decoder 오류를 노출하지 않는다.

## 시각 보고서와 다음 단위

- [목표/현재 흐름](../architecture/spec/image-runtime-flow.html): **브라우저 검증 PASS** (Task Space 141). PNG 2개 decode(552×2540, 1568×2534), 제목 7개·범례 5개·GAP 15행·우선순위 7행, 가로 넘침 없음. [1492×4168 스크린샷](playwright-screenshots/image-runtime-flow-b5.png)을 직접 확인했다. 최종 280 tests 결과와 로컬 우선 지시를 반영한 화면을 재검증하고 확인했다.
- 다음 B6는 사용자 지시에 따른 로컬 실제 모델 검증이다. root가 확인한 M4 Max(40 GPU cores)·64GB·약 352GiB 여유 공간은 환경 근거이며 모델 실행 증거가 아니다. 모델 사용 조건/revision·CPU/MPS 호환성·작은 권리 확보 샘플을 확인하고 adapter를 검증한 뒤 같은 조건으로 GCP 이전을 준비한다.
- 실제 누끼·배경 모델·GPU 자원 측정·고정 평가셋·사람 품질·렌더러·팀 인수는 미완료다. 원본 TODO의 실제 검증 항목은 체크하지 않는다.
- Git publication은 root가 별도로 검증한다.
