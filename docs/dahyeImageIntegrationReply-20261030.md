# 다혜님 이미지 연동 회신 초안 · 2026-10-30

상태: **사용자 검토용 · 미전송**. 아래 본문은10/30 현재 반영된 이미지 모듈을 기준으로 작성했다.

## 회신 본문

다혜님, 어제 연결 시험 결과를 보고 후속 수정했습니다. 잇템 `feat/image-generation`에 반영했고 현재 커밋은 `b0cf72e`입니다.

MVP는 실제 제품 사진과 텍스트 결과를 받아, 검토·승인 후 한글 광고 PNG와 캡션을 다운로드하는 범위로 맞추겠습니다.

- **MPO:** 첫 프레임을 정규화 PNG로 저장하도록 처리했습니다. HEIC는 아직 지원하지 않습니다.
- **Windows 로컬 실행 호환:** 다혜님이 Python 이미지 모듈을 로컬에서 실행할 때 겪은 오류에 대응해 디렉터리 동기화만 분리했습니다. 파일 쓰기·교체·DB 오류는 그대로 확인합니다. 모의 테스트는 통과했으며, Windows 로컬 실행을 계속 사용하실 경우 실환경에서 오류 해소 여부를 확인하면 됩니다.
- **카피 영역:** `make_a_template()`으로 A 상단 카피 영역과 중앙 정렬 합성 레이아웃을 함께 제공합니다. 새 생성 요청에서 이 영역에 제품·소품·복잡한 무늬·강한 반사광을 피하도록 지시합니다. 실제 결과의 가독성은 별도로 확인해야 합니다.
- **결과 재사용:** 저장된 정상 결과는 API 호출을 꺼도 추가 생성 없이 재조회하도록 보완했습니다. 최종 **1080×1350 리사이즈는 기존부터 합성에 연결되어 있습니다.**

이미지 모듈 테스트는 **348개 통과**했습니다. 실제 서빙 화면의 승인·다운로드까지 확인한 상태는 아닙니다.

보내주신 이미지에서는 제품 위쪽이 상단 카피 영역과 겹칠 수 있어 실제 카피로 배치를 검토해야 합니다. 가능하실 때 **당시 사용한 원본 MPO와 입력 JSON**을 키·토큰 등 비밀값을 제외하고 공유 부탁드립니다. ProductAnalysisResult·ImagePromptOutput·AdCopyOutput이 있으면 확인할 수 있습니다. 당시 모델명·사용량·단가 근거도 있으면 함께 부탁드립니다. 약 $0.06은 추정치로 기록했고, 원장 예약금액과 실제 청구액은 구분하겠습니다.

추가 유료 생성 없이 서빙 담당자와 **실제 서비스 환경에서 기존 결과의 한글 합성·승인·PNG/캡션 다운로드**부터 확인하면 좋겠습니다. Windows 로컬 Python 실행을 계속 사용하실 경우에는 앞서 겪은 저장 오류가 해소됐는지도 별도로 확인 부탁드립니다. 오류가 남으면 발생 단계와 오류 로그를 알려주세요.

## 별도 텍스트 리뷰 전달 메모

이미지 연결 회신과 구분해 필요할 때 전달할 문안이다. 검토 기준은10/29에 확인한 고정 커밋이며 이후 텍스트 브랜치 변경은 이번 문서 작업에서 재확인하지 않았다.

텍스트 검수도 `c558539` 기준으로 오프라인 리뷰한 내용이 두 가지 있습니다.

1. `홍조를 줄여줘요`는 차단되지만 `홍조를 줄여주고 화사한 메이크업을 연출해요`는 문제 0개로 통과했습니다. 같은 문장의 ‘연출’이 앞의 별개 표현까지 예외 처리하는 것으로 보여, 허용 동작과 대상이 직접 연결된 경우로 예외를 좁히면 좋겠습니다. 법적 판단이 아니라 기존 검수 규칙의 동작 차이입니다.
2. `test_failures.py`와 `test_highlight.py`는 실패를 출력해도 실패 종료 코드를 강제하지 않습니다. 자동화에 붙일 때 실패가 종료 코드에도 반영되도록 보완하면 좋겠습니다.

문구 6개와 기존 하이라이트 10개 issue를 확인했고 외부 API 파이프라인은 실행하지 않았습니다. 이미지 MVP 연결과는 별도 리뷰 사항입니다.

## 근거와 남은 정렬

Windows 항목은 로컬 개발환경 호환 확인이다. 보관된 [프론트 계약](https://github.com/taemin050-sys/project-it-tem/blob/b0cf72e889aa0b975021bccdd6d5eaca6934bc44/docs/contracts/partners/FRONTEND.md)과 [서빙 계약](https://github.com/taemin050-sys/project-it-tem/blob/b0cf72e889aa0b975021bccdd6d5eaca6934bc44/docs/contracts/partners/SERVING.md)은 React 웹 화면과 FastAPI 서버 흐름을 설명한다. 웹서비스 MVP 검증은 실제 배포 환경과 브라우저 경로를 기준으로 하며, Windows 전용 앱 개발 범위를 추가하지 않는다.

- [잇템 현재 이미지 코드b0cf72e](https://github.com/taemin050-sys/project-it-tem/tree/b0cf72e889aa0b975021bccdd6d5eaca6934bc44/src/image_generation)
- [MPO·Windows 처리](https://github.com/taemin050-sys/project-it-tem/blob/b0cf72e889aa0b975021bccdd6d5eaca6934bc44/docs/imageAssetPortability.md)
- [A 템플릿과 safe_zone](https://github.com/taemin050-sys/project-it-tem/blob/b0cf72e889aa0b975021bccdd6d5eaca6934bc44/docs/imageATemplate.md)
- [저장 결과·원장·재조회](https://github.com/taemin050-sys/project-it-tem/blob/b0cf72e889aa0b975021bccdd6d5eaca6934bc44/docs/imageReceiptAndReplay.md)
- [전체348개 검증과 실제 인수 제한](../fivecircles/test/image-mvp-relay-20261030-r3.md)
- [텍스트 색조 예외c558539](https://github.com/taemin050-sys/project-it-tem/blob/c5585397533ab38c1fd08bb478e80c65d235b8e9/src/text_generation/compliance/rule_checker.py#L153)
- [텍스트 실패 종료 코드](https://github.com/taemin050-sys/project-it-tem/blob/c5585397533ab38c1fd08bb478e80c65d235b8e9/scripts/test_failures.py#L160)

‘제품 용기와 다른 배경색 강제’ 규칙과 유사색 팔레트의 관계, 확정 카피를 소품 선택에 넣는 위치는 [공유 TODO](imageTodo.md)의 별도 정렬 항목으로 유지한다. 약$0.06과 사진 비용 비중75%는 팀원 추정이며 정산·단가 미확인이므로 확정 비용이나 개선 효과로 답하지 않는다.

이 초안 작성·게시로 팀원에게 메시지를 보냈거나 실제 Windows·서비스·제품 품질 인수를 받았다는 의미는 아니다.
