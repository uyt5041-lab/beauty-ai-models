# 2026-11-02 이미지 검토·실험 근거

작업 기준일: 2026-11-02 KST. 기록·원격 확인일: 2026-11-03 KST. 코드 수정이나 배포 인수 기록이 아니라 기존 코드 검토·오프라인 합성·사용자가 요청한 이미지 생성 실험 기록이다.

## Git 기준

- 팀 이미지 코드: `8bd495470dff177754147d46fece44af12a05034`.
- 당일 검토한 통합 코드: `92c51a7cbb717b8406eb25f344fef2590f05d793`.
- 11/03 원격 조회에서 확인한 11/02 후속 커밋: [0eef75d](https://github.com/taemin050-sys/project-it-tem/commit/0eef75d22bc63749a6af2519027e0fccad260593), [3ddbf73](https://github.com/taemin050-sys/project-it-tem/commit/3ddbf732b5816cfeba147cce53a0dfa43cfccca2). A 설정 고정·합성 receipt·복구·manifest 관련 파일과 테스트 변경이 있다. 커밋 존재와 변경 파일을 확인했으며 이 버전의 테스트 실행·실제 배포·팀 인수를 재검증한 것은 아니다. 앞선 누락 판정을 최신 코드에도 그대로 적용하지 않는다.

## 오프라인 검증

- 이전 통합 스냅샷에서 이미지 경계 관련 31 tests PASS, 3.15초. MockTransport 및 합성 fixture이며 유료 API 호출 0회. 대상: `tests/demo_live/test_pipeline.py::test_official_image_adapter_mocktransport_and_replay_once`, `tests/image_generation/test_backend_adapter.py`, `tests/image_generation/test_composition_integration.py`.
- 기존 A helper의 `tests/image_generation/test_a_template.py`: 15 tests PASS, 0.89초. 두 시험 묶음을 서비스 전체 테스트 개수로 합산하지 않는다.
- 저장된 피치 글로우 사진과 실제 팀원 카피로 합성. 헤드/서브/CTA 64/48/54px, 모두 1줄. 360px 축소본 육안 확인. 기기 사용자 시험이나 정량 대비 기준 통과는 아니다.
- 제품 수동 보호 사각형 y=405~1155와 CTA 영역 시작 약 y=1148이 겹쳐 검증 거절. 실제 글자 시작 y=1164여서 이번 예시의 직접 가림과 사각형 검증 실패를 구분했다. 진단본은 보호 영역 없이 렌더링한 것으로 `PRODUCT_REGION_NOT_PROVIDED`, `needs_manual_check=true`다. 인수 결과로 사용하지 않는다.
- 기존 A helper는 과거 Fabric 시안의 좌표·폰트와 동일하지 않다. 최종 CTA 위치와 보호 조건 확정은 미완료다.

## 실제 생성 실험

| 구분 | 이미지 모듈 핑크 젤 | 이미지 API 직접 인물·한글 |
|---|---|---|
| 모델 | gpt-image-2.5-sunburst-2026-09-08 | gpt-image-2 |
| 설정 | medium, 1152×1440 | medium, 1152×1440 |
| 소요 시간 | 33.265초 | CLI 전체 46.118초 |
| 실행 | 이미지 모듈 1회 성공, mock=false | CLI 1회 실행, 결과 1장 |
| 입력 토큰 | 4,566: 이미지 3,480 + 텍스트 1,086 | CLI 미저장, 미확인 |
| 출력 토큰 | 418 | 미확인 |
| 실제 청구 | 미확인 | 미확인 |
| 한계 | 제품이 요청한 높이 약 65%보다 크게 보임. 원형·라벨 정량 검증 미수행 | 브러시체 자형이 자유롭게 표현됨. OCR·제품 보존 검증 미수행 |

직접 API의 SDK 내부 HTTP 시도 수는 미확인이다. CLI 실행 1회를 HTTP 요청 1회로 단정하지 않는다. 두 실험은 모델·콘셉트·프롬프트도 달라 래퍼 유무만의 효과 비교가 아니다. 로컬 결과 기록이며 Langfuse/W&B 업로드나 서비스 인수를 뜻하지 않는다. 예약 $1은 실제 비용·가격 추정이 아니다. 서비스의 기존 무문구 생성→승인→합성 계약은 변경하지 않았다.

## 협업 구분

사용자가 전달한 프론트 담당 문의를 바탕으로 A 좌표와 합성만 재시도하는 동작을 설명하고 전달용 MD를 작성했다. 팀원은 두 작업으로 이해했다는 사용자 인용이 있으며, 에이전트의 직접 메시지 전송·팀 인수 승인은 없다. 서빙은 기존 승인 이미지·카피를 재사용하고 합성 receipt·경고를 보존하는 방향으로 요청을 정리했다. Text의 한국어 concept/prompt/mood와 카피를 임의 재작성하지 않았다.

## 완료 판정

11/02까지 일별 마일스톤 10개: 완전 완료 3개(10/20, 10/22, 10/23), 부분 완료 7개. M1은 미달 항목이 남아 미완료. 11/03 이후 일정과 M2는 미체크. 개별 실험 성공을 개발·검증 20결과나 최종 품질·FinOps 합격으로 바꾸지 않는다.

## 로컬 근거 식별값

생성 이미지·원본 사진·인물 참고 광고·비밀값·로컬 절대 경로는 공개하지 않는다. 아래는 로컬 기록의 상대 식별자와 SHA-256이다. 공개 경로 링크는 아니다.

- `fivecircles/work/review/review-image-live-boundary-2026-11-02.md`: `a9abd7d8db554e61f44573899840d91368460011ebb55c9bbef9cd611843f3cb`
- `reports/a-template-handoff-20261102/validation.json`: `1cd543457bc676508cff838426c0e94f2abdb3fa0a35cdb27fcff3c8e07cc94b`
- `reports/romand-pink-gel-live-20261102/generation-result.json`: `1c189a03d78c5bb64d7a30225c1a8aa88a8e7a05be1dfd3470e60dd43e9e6751`
- `output/imagegen/romand-direct-api-20261102/execution.json`: `2c1a83d9f589a34b13185a6c9cbdaa9472abe9072902014f3db3df188e61610a`
