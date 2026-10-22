# 최근 변경

## 2026-10-22 · BEAUTY-FC-001

- FiveCircles 원본을 받아 BeautyAI 운영 문서/설정/프로젝트 스킬을 추가했다.
- 기존 모델 코드와 TODO를 연결하고 담당·마일스톤·미합의 상태를 구분했다.
- 검증과 완료 상태: [setup-validation.md](../test/setup-validation.md).
- Git 반영 상태: [integrate-log.md](integrate-log.md).

## 2026-10-22 · 릴레이 B1

- 자산 저장/검증과 제품 캔버스/보존 검사를 추가. 원본 이미지 TODO 8개를 근거 범위로 체크.
- 72 tests 및 CLI 파일 저장, Mermaid 보고서 브라우저 검증 PASS. [상세](../test/relay-b1.md).
- 다음은 B2 영속 job/API. GCP 조회는 사용자 재인증 필요.

## 2026-10-22 · 릴레이 B2

- 인증 internal API·WAL ledger·단일 호스트 worker 구현. 원본 TODO의 로컬 신뢰성 7개만 추가 체크.
- 전체 112 tests, health API 8 tests 및 loopback·프로세스 복구·시각 비교 PASS. [상세](../test/relay-b2.md).
- 다음은 B3 profile·승인·adapter. 실제 GPU·운영 네트워크·팀 인수는 미검증.

## 2026-10-22 · 릴레이 B3

- 제품 후보/명시적 승인 저장·서버 고정 profile·로컬 template·실행 CLI를 연결했다. 원본 TODO 로컬 범위 4개만 추가 체크.
- 전체 148 tests·실제 TCP CLI·512×512 결과 파일 검증 PASS. [상세](../test/relay-b3.md).
- 다음 B4는 프롬프트 버전 수신·편집 revision·프로젝트 캐시. 실제 GPU/AI 모델·최종 광고 품질·팀 인수는 미검증.

## 2026-10-22 · 릴레이 B4

- 프롬프트 수신·legacy hash, 편집 revision CAS·프로젝트 성공 결과 캐시·늦은 attach 차단을 연결했다.
- 전체201 tests 및 실제 TCP CLI의 문구 수정 후 동일 job 재사용 PASS. TODO 로컬 검증3개 추가 체크. [상세](../test/relay-b4.md).
- 다음 B5는 고정 평가 manifest·증거 수집 도구. 실모델/평가셋 품질/사용자 UI/렌더러/팀 인수는 미검증.


## 2026-10-22 · 릴레이 B5

- 로컬 평가 manifest·mask/픽셀 지표·job timing API/CLI를 추가했다. 원본 TODO의 도구 하위 3개만 체크했다(누적 25개).
- 전체 280 tests PASS(4.06s), 예제 manifest CLI·실제 TCP timing·wheel build PASS. FIFO manifest 대기를 발견·수리하고 재검증했다. [상세](../test/relay-b5.md).
- 실제 모델·평가셋·사람 품질·GPU 시간·VRAM·비용은 미검증. 다음 B6는 로컬 Mac 실제 모델 검증 후 GCP 이전이다.
