# 원본과 변경 내역

- 출처: [Pio / uyt5041-lab FiveCircles](https://github.com/uyt5041-lab/fivecircles).
- 내려받은 ref: main, commit [`2c4cc6132d95c2355e58d76aafceb9c9e8c3b2e8`](https://github.com/uyt5041-lab/fivecircles/tree/2c4cc6132d95c2355e58d76aafceb9c9e8c3b2e8).
- 확인/복사 시각: 2026-10-22T08:48:02+09:00.
- 라이선스: © 2026 Pio, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), [원본 고지](LICENSE).
- 방식: Git clone으로 원본을 받은 뒤 공통 문서 11개를 선택 복사하고 BeautyAI에 맞게 수정했다. 중첩 .git/submodule은 포함하지 않는다.
- 원본/설치 파일별 해시와 수정 여부: [upstream-manifest.json](upstream-manifest.json).

## 적용한 변경

6단계 개발 흐름과 요구사항/설계/기록/검증 구조를 유지했다. Python 명령, 실제 담당, 마일스톤, FF Git/PR 없음, 근거 기반 완료 판정으로 수정했다.
루트 AGENTS, 프로젝트 전용 스킬, 설정 검사, 문서 인덱스와 도입 계약은 이 설치에서 추가했다.
기존 프로젝트 이력, 서비스 전용 코드/SQL/테스트, SSH·Docker·배포 스크립트, MCP 호스트 설정과 에이전트 자동 실행기는 가져오지 않았다.

## 갱신 방법

1. 별도 임시 폴더에 원본 저장소를 받는다. 현재 도입 commit과 비교한다.
2. 필요한 공통 문서만 검토하고 BeautyAI 담당/경로/계약을 유지한다. 폴더 전체를 덮어쓰지 않는다.
3. 변경 내용을 검토한 뒤 원본 SHA와 파일 해시, 이 변경 내역을 갱신한다.
4. 설정 검사와 영향 범위 검증을 수행한다. Git 반영은 사용자 요청 범위와 로컬 Git 정책을 따른다.

현재 설치 파일이 manifest 해시와 달라졌다면 로컬 수정이다. 해시를 무조건 덮어써 숨기지 말고 변경 내역을 검토한다.
