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

## 최신 모듈 호환 설정

패치 적용 전에 이 브랜치의 src/image_generation을 dev의 같은 패키지 경로에 연결한다. 최신 이미지 모듈과 dev 연결의 통합 시험 567개 PASS. dev의 기존 비용 모니터링 옵션은 명시적으로 켠 경우에만 허용하며 기본 예산 차단은 유지한다.

IT_TEM_IMAGE_MODEL=gpt-image-2.6-sunburst. IT_TEM_IMAGE_SCENE_MODE=dimensions14를 웹·워커에 함께 설정하면 4:5 A배치에 14디멘션 생성·중앙 고정 적응형 합성을 적용한다. 다른 비율은 기존 standard 프로필을 유지한다. 설정은 호출 승인·키·actor 허용 목록을 대신하지 않는다. 모든 실 API 호출은 기존 유료 승인 경로를 통과한다.

## 실제 E2E 확인 · 2026-11-12

위 최신 모듈 설정으로 실제 사진 두 장을 업로드하여 카피 생성·검수·승인·이미지 생성·이미지 승인·한국어 최종 합성·완료 화면·PNG 다운로드까지 진행했다. COMPLETED/LIVE를 확인했다. 이미지 생성은 1회이며 이미지 단계 약 34초, 생성 1152×1440, 최종 1080×1350이다. 다운로드와 서버 파일 SHA가 일치했다. 실제 청구액은 미확인이다.

모듈 교체 때 발생한 dev monitoring_only 인자 차이를 반영한 후 567개 통합 시험을 통과했다. 새 호환 검증 5개는 별도 통과했다. 기본 예산 차단은 유지한다. 모니터링 모드의 0 예약은 0 비용이나 공급자 과금 상한을 의미하지 않는다.

사진·생성 PNG·로컬 DB·계정 키·초대코드는 공개하지 않는다. GCP 배포와 팀 인수는 별도다.
