# B2 검증 · PASS_WITH_RISKS

2026-10-22 KST. 범위: 영속 job ledger·인증된 internal API·단일 호스트 worker. [구현 계약](../architecture/contract/image-runtime.md).

## 실행한 검증

- `.venv/bin/python -m pytest -q -W error`: **112 passed in 8.52s**. 메인 에이전트가 전체 회귀 결과를 확인했다.
- 인증된 health 추가 후 API 회귀: `.venv/bin/python -m pytest -q -W error tests/test_inference_api.py`: **8 passed in 2.31s**.
- SQLite 임시 DB와 실제 여러 프로세스: 동일 ID/hash 1회 등록·claim, 다른 hash 거절, project 범위·owner 확인, terminal 불변, 손상 snapshot 차단.
- worker: 파일 잠금 경쟁, 실행 중 stop timeout의 잠금 유지, 프로세스 강제 종료 후 running interrupted 및 남은 queued 실행, commit 실패 시 중복 재실행 방지, 결과 파일/소유권/종류 검증.
- 실제 loopback TCP: Uvicorn → HTTPImageGateway 제출·대기 → fixture processor → SQLite/파일 성공 → 동일 ID 재전송·결과 파일 hash 조회. 테스트 processor는 실제 누끼·배경 모델이 아니다.
- API: Bearer→project 매핑, 자산/작업 교차 접근 차단, 제한된 body 읽기, 파일 검증, 입력/키를 반사하지 않는 validation 오류, 인증된 health. fatal worker는 503, model_readiness는 not_assessed.

## 검토와 수정

- arbitrary validation 오류 경로(loc)에 사용자 입력 키가 포함될 수 있는 문제를 수정했다. 외부 응답에는 오류 type만 공개한다. 회귀로 원본 키/값 노출 차단을 확인했다.
- 응답 유실 후 이미 commit된 작업의 재전송은 현재 파일 상태를 다시 검사하기 전에 저장된 ID/hash로 조정한다. 파일이 사라져도 기존 작업을 새 작업으로 바꾸지 않는다.
- processor를 지정하지 않은 API는 queued 상태를 영속 보관한다. GPU가 준비됐다고 표시하지 않는다.

## 시각 보고서

[목표/현재 HTML](../architecture/spec/image-runtime-flow.html)에서 A→B와 로컬 worker 실행 기반을 MATCHED로 표시하고 실제 GPU·승인·revision·렌더러는 GAP/PARTIAL로 유지했다.
PNG 2개 decode, 필수 heading 7개·범례 5개·GAP 11행·우선순위 6행, 가로 overflow 없음 확인. [B2 스크린샷](playwright-screenshots/image-runtime-flow-b2.png). B1 스크린샷은 보존했다.

## 판정과 남은 조건

**PASS_WITH_RISKS — 로컬 단일 호스트 실행 기반. 전체 제품 흐름은 GAP/PARTIAL.**

- 운영 GPU 모델·driver/CUDA·모델 품질·실제 OOM/VRAM·외부 배포·팀 인수는 NOT_RUN.
- POSIX 잠금과 공유 로컬 파일시스템을 전제로 한다. 다중 VM, 별도 비동기 GPU subprocess의 실행 종료는 이 잠금으로 보장하지 않는다.
- 입력 후보의 제품 승인, profile registry/runtime adapter, 편집 revision·캐시·렌더러가 남았다. 다음은 B3 profile·승인·adapter다.
- 환경 접근의 인증 갱신이 필요해 실환경 조회/검증은 보류됐다. 계정·프로젝트 ID·비밀값을 이 기록에 포함하지 않는다.
- 이번 산출물은 Python 라이브러리/API factory와 loopback 검증이다. 운영 배포를 수행하지 않았으며 운영 CD는 이 배치에서 NOT_APPLICABLE. Git 반영은 별도 [반영 기록](../work/integrate-log.md)에 실제 SHA/readback으로 남긴다.
