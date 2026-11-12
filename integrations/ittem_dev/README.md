# dev 비율·Sunburst 2.6 연결 수정

적용 기준: 잇템 dev `c6820f07ec093afcbf9a1007f20e76aebd4883ba`.
이 뷰티 저장소는 이미지 패키지를 관리한다. dev 전용 서빙·React 변경은 불완전한 모듈로 복사하지 않고 재현 가능한 패치로 보존한다.

별도 dev 체크아웃에서 `git apply --check /path/to/ratio-sunburst.patch`로 확인한 뒤 `git apply /path/to/ratio-sunburst.patch`로 적용한다. 기존 수정 파일과 충돌하면 덮어쓰지 않는다.

- image_size 3:4·4:5·9:16 수신/영속화/생성·합성 연결. 기존 4:5 입력 해시는 유지한다.
- 9:16은 기존 Story B를 사용한다. 릴스·스토리 선택 UI 추가는 포함하지 않는다.
- Sunburst 2.6을 워커 기본값과 허용 목록에 반영한다. 모델별 옵션은 기존 이미지 모듈이 결정한다.
- Python: `python -m pytest tests/demo_live tests/image_generation -q`. 한국어 폰트는 `IT_TEM_TEST_FONT`로 지정한다.
- React: app에서 `npm test`, `npm run lint`, `npm run build`.

검증: 비율 수정 후 Python 480개·프론트 39개, lint/build PASS. 2.6 변경 후 관련 Python 144개 PASS(전체 회귀 건수에 합산하지 않음). 두 Python 시험은 외부 소켓 차단 상태였다.
브라우저 폼의 세 비율 모두 업로드 201·입력 저장 200. 초기 조회가 입력 저장보다 먼저 실행되는 LIVE_JOB_NOT_FOUND 422는 별도 발견사항이다. 실제 생성 E2E는 이 게시 시점 미완료다.

API 키·환경 파일·초대코드·사진·DB·생성 결과·개인 경로는 포함하지 않는다. 로컬 시험용 승인·키 설정은 패치에 넣지 않았다. 푸시는 GCP 배포나 팀 인수를 뜻하지 않는다.
