# BeautyAI FiveCircles 도입 계약

- 작업 ID: BEAUTY-FC-001
- 요청: FiveCircles 공개 저장소에서 프레임워크를 받아 beautyAi용으로 설정한다.
- 완료 기준: 출처·버전을 기록하고, 로컬 시작 규칙·담당·TODO·검증·Git 정책을 연결한다. 두 작업 폴더의 공통 설정이 같고, 기존 코드·스키마·TODO가 보존되어야 한다.
- 범위: 프로젝트 내부 `fivecircles/`, 루트 `AGENTS.md`, 프로젝트 전용 `.agents/skills/fivecircles/SKILL.md`, 추가 안내 `README2.md`, 검증 도구 `scripts/check_fivecircles.py`.
- 원본: [uyt5041-lab/fivecircles](https://github.com/uyt5041-lab/fivecircles), CC BY 4.0. 선택한 공통 문서를 복사·수정하며 원본 SHA/해시를 남긴다.
- 적용 대상: 사용자 로컬 beautyAi와 공개 저장소의 plan/models 작업 폴더. 전역 에이전트 설정이나 다른 프로젝트 운영 상태를 바꾸지 않는다.
- 보존: 기존 모델 코드, JSON Schema, docs의 체크리스트와 체크 상태, README의 5개 절, 별도 협업일지 브랜치.
- 계약 영향: 모델 입력/출력·HTTP API는 변경하지 않는다. 문서와 프로젝트 시작 규칙만 추가한다.
- Git: 현재 plan/models에서 작업하고 PR을 만들지 않는다. 이번 도입 결과는 로컬 변경으로 남긴다. 이후 반영 요청 시 fetch → FF-only 최신화 → 선택 파일 커밋 → 일반 push → 원격 SHA 확인을 수행한다.
- 검증: 설정/참조 경로/출처 해시 검사, TODO·소스 보존 검사, 두 폴더 일치 검사, git diff --check. 모델 단위 테스트와 실모델 검증 상태는 별도 기록한다.
- 되돌리기: 이번에 추가한 프레임워크·진입 파일만 제거하고 README2의 이번 추가/수정 부분만 되돌린다. reset --hard나 기존 작업 삭제를 사용하지 않는다.

## 사용 예시

| 요청 | 작업 흐름 | 완료 증거 |
| --- | --- | --- |
| fivecircles 초기화 | README → 담당/계약 → TODO → 최신 작업 기록 확인 | 역할·선택 작업·미합의 항목 보고 |
| 이미지 TODO의 다음 항목 진행 | 원본 체크리스트에서 범위 선택 → 설계/영향 보고 → 구현 → 해당 검증 | 파일/명령/결과를 기록한 뒤 원본 항목 체크 |
| 영어 프롬프트 계약 바꿔줘 | 김다혜 산출물·pi 입력·API 조립 경계 확인 → 합의 여부 구분 → schema/adapter 영향 검토 | 정상·실패·긴 프롬프트·응답 유실 사례 |
| 완료분 푸시해 | 상태와 원격 확인 → 허용된 파일 커밋 → FF 가능한 일반 push | 로컬 HEAD와 원격 SHA 일치 |
