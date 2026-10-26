# Git 운영

- 이미지 전체 통합 기준: `feature/image-model`. 기존 계획·문서 이력: `plan/models`. 원격: `https://github.com/uyt5041-lab/beauty-ai-models.git`.
- 협업일지: `박지수_협업일지`, 날짜별 MD/HTML을 독립 관리한다. 코드 브랜치와 PR/merge로 합치지 않는다.
- 원본 CONTRIBUTING의 dev/PM/PR 예시는 현재 공개 저장소 규칙이 아니다.
- 작업 전 `git status --short --branch`, `git remote -v`를 확인한다. 기존 변경을 초기화/삭제/자동 stash하지 않는다.
- 반영이 요청되면 `git fetch origin` 후 게시 대상 브랜치와 비교한다. 이미지 통합은 `git rev-list --left-right --count HEAD...origin/feature/image-model`을 사용한다. 원격 브랜치를 처음 만들 때는 분기 기준인 `origin/plan/models`와 관계를 확인한다.
- 빠진 원격 커밋은 작업을 보존할 수 있을 때 해당 대상에 `git merge --ff-only`로 반영한다. 양쪽이 갈라졌다면 임의 강제 반영을 하지 말고 상황을 보고한다.
- 해당 검증과 `git diff --check`, 비밀값·생성물 제외 확인 후 작업 파일만 선택 커밋한다.
- 현재 이미지 통합 브랜치는 `git push -u origin HEAD:feature/image-model`으로 일반 push한다. `git ls-remote --heads origin feature/image-model`과 `git rev-parse HEAD`를 비교한다. `plan/models` 게시를 별도 요청받은 경우에만 해당 브랜치를 대상으로 확인한다.
- 다른 사람의 커밋을 덮어쓰는 force push, hard reset, 무관한 대량 stage를 금지한다.
- 프레임워크 설치 자체는 커밋/푸시 완료의 증거가 아니다.
