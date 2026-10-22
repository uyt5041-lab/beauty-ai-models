# 작업 기록

## 2026-10-22T08:48:02+09:00 · BEAUTY-FC-001 · Design / Implementation

- 요청: 공개 FiveCircles 프레임워크를 beautyAi용으로 도입.
- 선택: 공통 문서 선택 복사 후 프로젝트 내부 적용; 원본 commit `2c4cc6132d95c2355e58d76aafceb9c9e8c3b2e8`.
- Conflict: 원본 템플릿에는 이전 프로젝트 테스트/배포/담당/이력과 깨진 진입 경로가 있음.
- Winner: 현재 사용자 지시와 BeautyAI 소스/팀 TODO.
- Resolution: Python 테스트·pi/김다혜·독립 일지 브랜치로 수정, 이력/호스트 설정은 제외.
- Follow-up TODO: 10/26 공동 계약 합의는 기존 원본 TODO에서 별도 관리.
- 범위/되돌리기: [도입 계약](../architecture/contract/framework-setup.md).

## 2026-10-22T08:49:40+09:00 · BEAUTY-FC-001 · Test / Local completion

- 결과: 설정 검사·누락/변조/JSON 오류 탐지·원본 보존·양쪽 45개 일치 확인 PASS.
- 독립 검토 반영: workflow의 없는 implementation-log 경로를 worklog로 수정하고 스킬 경로를 루트 기준으로 명시했다.
- [검증 상세](../test/setup-validation.md). 모델 테스트·실모델·Git 반영·배포는 미실행.

## 2026-10-22 · B1 · 구현/검증

- 구현: assets.py, image_preparation.py, 실제 임시 파일/DB tests와 CLI.
- 검증: 72 tests + CLI/시각 보고서 PASS. 실제 GPU/팀 인수는 미검증.
- 계약/범위: [image-runtime](../architecture/contract/image-runtime.md). [B1 기록](../test/relay-b1.md).

## 2026-10-22T12:24:52+09:00 · B2 · 구현/검증

- 구현: job_ledger.py·image_worker.py·inference_api.py. 서버 소유 토큰/project 매핑, atomic ID/hash, POSIX 실행 잠금, durable queued 복구와 결과 검증.
- 검토 수정: validation 오류의 임의 loc/입력 키를 제거하고 type만 반환. processor 없음은 queued 유지. health는 모델 readiness를 주장하지 않음.
- 검증: 전체 112 tests PASS(8.52s), health API 8 tests PASS(2.31s). 실제 loopback TCP/SQLite/파일 및 다중 프로세스 종료/복구 검증.
- 범위: 단일 호스트 동기 테스트 processor. 실제 GPU·외부 배포·광고 품질·팀 인수는 미검증. [B2 기록](../test/relay-b2.md).
- 문서: README의 5개 절 유지, 이미지 TODO 로컬 신뢰성 7개 추가 체크, 목표/현재 흐름 갱신. 다음 B3 profile·승인·adapter.

## 2026-10-22 · B3 · 구현/검증

- 구현: product_approval.py·local_processor.py·inference_runtime.py, 승인 API 및 단계별 CLI. 기존 wire/hash 유지.
- 검증: root 전체 148 tests PASS(12.14s), 담당 관련 35 tests PASS(3.95s), 별도 서버 프로세스와 TCP CLI 6단계/실제PNG PASS.
- 검토/수리: 명시적 확인/hash/project/파일/geometry·등록/실행 재검증 확인. 변조 자산 테스트의 기대코드를 승인 경계에 맞춤. [B3 검증](../test/relay-b3.md).
- 범위: 기존 투명 RGBA·로컬 template이며 실제 AI 모델을 호출하지 않았다. 사람 신원/품질 인증·현재 편집 revision·GPU·렌더러·팀 인수는 남아 있다.
- 문서: README5절 유지, 이미지 TODO4개 추가 체크, 목표/현재 흐름 및 다음 B4 점수표 갱신.

## 2026-10-22 · B4 · 구현/검증

- 구현: ImagePrompt DTO/직렬화·schema, ImageEditStore의 CAS/project cache/현재 revision attach, 내부 API·CLI 연결.
- root 검증: 전체201 tests PASS(20.04s), 실제 TCP CLI prepare/approve/select/render/attach/copy-only select/get-edit에서 동일 job 재사용 PASS.
- 검토 반영: strict integer·64bit 범위·공백 prompt를 거절. 문구 내용·검수DB는 수정하지 않는 이미지 상태 저장 범위.
- 문서: README5절, 원본 TODO 로컬 항목3개 추가(누적22개), B4 흐름/다음 B5 후보 갱신. [검증](../test/relay-b4.md).
