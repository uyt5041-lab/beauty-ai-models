# 릴레이 B10 검증 · OpenAI 이미지 래퍼

2026-10-22 · plan/models · 오프라인 래퍼 PASS / 실제 API·최종 서비스 NOT_RUN·GAP

## 구현과 경계

- 생성·복수 제품/참고 사진 편집·생성형 투명 PNG 누끼를 `api_image` 단일 결과 계약으로 구현했다. 기본 모델은 `gpt-image-2.5-sunburst-2026-09-08`, 허용 대안은 `gpt-image-2-2026-04-21`이다. 실제 계정 모델 접근은 검증하지 않았다.
- 명시 MockTransport 또는 승인 후 켠 서버 설정만 전송 경로에 들어갈 수 있다. 기본 `live_calls=False`·이미지 예산0이다. 이번 작업에서 실제 API 호출·모델 목록 조회·실제 키 조회·로컬 모델 실행은 하지 않았다.
- 고정 OpenAI endpoint, 환경 proxy/redirect/POST 자동 retry 미사용. 제품·참고 순서를 유지하고 negative는 명시 Avoid 문장으로 전달한다. URL-only 응답을 거절하고 제한한 base64만 검증한다.
- 프로젝트 자산 권한/hash·입력 상한 검사 후 SQLite write transaction으로 시도와 예약액을 먼저 저장한다. 같은 job의 동시 시작·재시작은 새 POST를 만들지 않는다. 새 job은 명시적 새 작업이며 동일 hash만으로 전역 차단하지 않는다.
- timeout/응답 유실은 UNKNOWN, 실패/저장 오류도 예약을 자동 환급하지 않는다. 사용량은 관찰값, 없는 값은 null, 청구액은 UNKNOWN이다. 예약액은 실제 가격 상한이나 LLM을 포함한 공유 계정 전체 예산 보장이 아니다.
- PNG·실제 해상도·비애니메이션·누끼 alpha의 비어 있지 않은 전경/투명 배경을 검사한다. 제품 형태·라벨·색 보존의 품질 증명은 아니며 모든 결과는 수동 확인 대상이다. 외부 단일 PNG를 기존 승인된 3레이어 결과로 포장하지 않는다.
- provider receipt 조회는 내부 job 프로젝트 권한을 먼저 확인한다. 팀원에게 주는 현재 계약은 `schemas/ExternalImageRequest.schema.json`·`ExternalImageResult.schema.json`이다. 이전 `docs/schema-draft-2026-10-22/`는 당시 스냅샷으로 보존했다.

## 검증

- 전체 `.venv/bin/python -m pytest -q -W error`: **806 passed in 51.05s**. B9 기준624개에서182개 추가. 실제 자산/SQLite·내부 API·프로세스 복구와 외부 모의 응답 시험이며 실제 OpenAI/운영 인수 결과가 아니다.
- 어댑터 모의 시험85개: 모델/옵션·최대 입력·기본 호출 차단·설정·요청변환·401/429/쿼터/정책·timeout/5xx·응답상한/URL/base64/usage·비밀값 비노출.
- processor/attempt 시험30개: generate/edit/cutout, 동일 job 결과 재사용, 타 프로젝트 거절, 예산 경쟁·0한도·실패 후 보류, alpha/PNG/크기 오류, 자산 저장/receipt commit 실패와 재시작.
- demo/runtime 시험6개: 내부 upload→job→provider receipt→asset download, 실제 호출 기본 차단, unknown 뒤 재시작 및 다음 정상 작업, running/submitting 강제 중단 복구와 자동 재전송0회.
- 별도 검토: 동일 job에 store16개/8스레드 begin 경합에서1개 예약·15개 conflict. [출력 경고 분류 문제](errorlogs/2026-10-22-relay-b10.md)를 재현·수리하고 회귀에 포함했다.
- mock CLI 직접 실행: `python -m beauty_ai.openai_images_demo --output outputs/openai-wrapper-demo`. actual_provider_calls=0, mock_provider_calls=3, quality_approval=NOT_ASSESSED. 합성 PNG에는 MOCK ONLY - NOT AI OUTPUT 표시가 있고 실제 AI 결과가 아니다. 결과는 공개 Git 제외 경로에 보존한다.
- `pip check` PASS. wheel build PASS, SHA-256 `1a8ba1af24ae58752fe06b6a93470c2e511271423fae2d204515d7d2a4d6093f`. 격리 wheel import에서 torch/diffusers 미로딩과 기본 호출 차단을 확인했다. 현재 스키마4개·FiveCircles 설정·README5절·diff·공개 파일 제외/비밀패턴 검사 PASS.
- [목표/현재 흐름](../architecture/spec/image-runtime-flow.md) MD·MMD·PNG·HTML 갱신. Mermaid CLI11.12.0, PNG2개 직접 확인. HTML 참조/제목/표는 정적 검사. 최종 HTML 브라우저 검증은 이전 자동승인 거절 경계를 유지해 BLOCKED, 우회 없음. 과거 screenshot은 새 최종판 검증에 사용하지 않는다.

- 로컬 beautyAi에 기존 공개 파일26개를 백업하고66개 파일을 갱신했다. 공개 대상192파일 byte parity PASS. 로컬 독립 가상환경의 신규182 tests PASS(3.68s), 설정 검사와 mock CLI3건도 PASS/실호출0회. 이 동기화는 기존 비밀값·가중치·출력물을 수정하지 않았다.

## 남은 작업

API-01의 실제 계정/사용조건, API-03의 운영 예산/쿼터, API-04의 실제 응답·청구 확인, API-06의 실제 제품 품질·비용·지연 인수는 미완료다. 사용자가 실제 호출을 금지했으므로 이번 릴레이에서 실행하지 않는다. 서비스의 카피/이미지 검토 UI·최종 한글 렌더러·다혜 LLM 연결·팀 인수도 별도다.

운영 CD NOT_APPLICABLE: 호출이 기본 비활성인 Python 참조 소스·문서 전달이며 운영 배포 대상/요청이 없다. Git 게시·로컬 동기화의 실제 SHA/readback은 [게시 기록](../work/integrate-log.md)을 따른다.
