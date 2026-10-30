# R3 원장·재조회·제품 검토 · 2026-10-30

판정: PASS_WITH_RISKS(이미지 모듈). 제품 보존 REVIEW_REQUIRED, 실제 서빙 인수 NOT_RUN.

## 확인 및 보완

기존 사용량·attempt ID·request hash·시간·예약금액·미정산 상태·중복 방지 원장이 이미 있었다.
reservation_usd는 실행 허용량이며 추정 가격/실제 청구액이 아니다. actual_cost_usd=None, billing_status=UNRECONCILED를 유지한다.

추가로 정상 저장된 성공 결과가 live_calls=False에서 PROVIDER_CALLS_DISABLED로 막히는 문제를 오프라인 재현했다.
같은 순수 snapshot 검증 → project/job/hash/결과/asset 검사 → 성공 결과 재사용 후,
새 실행에만 기존 제공자 설정·예산 예약·POST 경계를 적용하도록 수정했다.
입력·project·mock/live 구분과 failed/unknown/submitting 재호출 차단은 유지한다.

74 targeted PASS(1.38s), 최종 전체348 PASS(4.34s).
비활성화/예산0 성공 조회, 요청/결과 변조, 누락/손상 파일, 잘못된 snapshot, 다른project, mock/live 구분을 포함한다.
실제 제공자 호출0. 테스트 transport/fixture execute만 사용했다.
기존 동시 예산 예약·중복 제출·응답 소실·저장 실패·원장 commit 실패 회귀도 전체 suite에 포함된다.

## 수집·전달 경계

get_receipt는 provider 호출 없는 읽기다. 수집 재전송에 새 job을 만들지 않는다.
외부 Langfuse/W&B worker 실제 재처리는 NOT_RUN이며 서빙 필수 의존성으로 추가하지 않았다.
합성 PNG1080×1350·UTF-8 캡션·copy hash/revision 전달은 R2와 기존 통합 테스트에서 검증했다.
인증·승인 snapshot 저장·실제 UI 다운로드는 서빙 인수 대상이다.

## 보관된 실제 후보 수동 검토

로컬 제품 MPO2개와 팀원 결과를 EXIF/ICC 적용 프리뷰로 직접 비교했다.
이 사진들이 팀원이 생성에 쓴 바로 그 입력쌍인지는 미확인이다.

| 항목 | 결과 | 근거 / 다음 조치 |
|---|---|---|
| 제품 대응·각도·비율 | REVIEW_REQUIRED | 전체 형태는 유사하나 입력쌍·시점 정합 확인 없음. 정량 비율 PASS 아님 |
| 색·라벨 | REVIEW_REQUIRED | 장면 조명 차이가 크며 작은 라벨의 전체 일치 확인 필요 |
| 접지·반투명 경계 | REVIEW_REQUIRED | 수면 반사/투명 캡 하이라이트가 있으나 원본 해상도 비교·사람 승인 필요 |
| A 상단 카피 | 고정 A 배치 거절 | 제품이 약 y12%에서 시작해 상단25%와 겹침 |
| CTA·대비 | REVIEW_REQUIRED | 하단 수면 반사·무늬가 강하고 실제 카피 JSON이 없음 |

수동 보수적 bbox (0.40,0.11,0.60,0.84)를 기존 보호 영역 검사에 전달해 A 겹침 거절을 확인했다.
이 bbox는 자동 검출/정량 품질 점수가 아니다. API 성공을 제품 보존 합격으로 바꾸지 않았다.
새 이미지 생성이나 원본 픽셀 고정 합성을 수행하지 않았다.

[전체 영수증·파일 해시](image-mvp-relay-20261030-r3.json).

R3 코드 게시: b0cf72e889aa0b975021bccdd6d5eaca6934bc44, feat/image-generation 원격 SHA 일치.
