# BeautyAI 연동 스키마 초안

**초안 · 팀 미합의 · 2026-10-22.** 다혜님 작성물이 아니라 현재 코드와 SSOT를 읽어 만든 비교용 자료다. 운영 API나 확정된 팀 계약으로 사용하지 않는다. 이 게시에서는 문서와 스키마 스냅샷만 추가한다. 실행 코드와 기존 계약은 바꾸지 않는다.

## 목적과 범위

김다혜(텍스트) → pi(이미지 외부 API 래퍼) → 서비스/최종 렌더러가 주고받을 내용을 정리한다. 외부 이미지 제공자와 모델은 아직 미정이다. 기존 로컬 누끼·SDXL 계약을 모든 외부 API에 강제하지 않는다.

| 자료 | 상태 | 핵심 내용 |
| --- | --- | --- |
| current/*.schema.json | 현재 Python 코드에서 추출한 스냅샷, 팀 합의 미확인 | 13종 기존 계약. 로컬 이미지 실험 계약 포함 |
| proposed/ServiceIntakeDraft.schema.json | 새 제안, 미구현 | 제품 정보, 제품 사진 2~5장, 배경 참고 0~5장, 광고 목적 |
| proposed/ImageHandoffDraft.schema.json | 새 제안, 미구현 | 현재 카피·영어 프롬프트·검토 상태·입력 사진·revision 전달 |
| examples/*.json | 가상 값으로 검증한 예제 | 실제 제품·실제 자산 ID·광고 승인·API 호출 결과 아님 |
| source-snapshot.json | 읽은 소스 식별 | Git HEAD와 작업 중인 원본 파일 해시. HEAD만으로 미커밋 문서까지 재현되지 않음 |

## 지금 주고받을 최소 항목

| 구간 | 계약과 필수 필드 | 담당/주의 |
| --- | --- | --- |
| 제품 분석 | ProductObservation: container_shape, visible_colors, label_text, uncertainties | nullable 필드도 현재 코드는 키 자체를 요구한다. 사진 관찰은 효능의 확인 근거가 아니다 |
| 확인된 제품 정보 | ProductFacts: product_id, revision, product_name, functional_status, functional_categories, confirmed_claims | 관찰·시장 조사와 사용자 확인 사실을 구분한다 |
| 카피 | CopyBundle: headline, subheadline, cta, caption, hashtags[], badges[] | 다혜 담당. 템플릿별 최대 길이는 미합의 |
| 자동 검수 | ReviewReport: status, snapshot_hash, copy_revision, product_revision, rule_pack_version, prompt_version, configured_review_model, issues[], uncertainties[], meta | 사용자 검토와 별개. legal_approval은 false |
| 영어 이미지 프롬프트 | ImagePrompt: positive, prompt_version, source_revision; negative 기본 빈 문자열, language 기본 en | positive 1~4000자, negative 최대 2000자. 공백만인 positive/version은 런타임에서 거절 |
| 이미지 의도 | VisualBrief: concept, 스타일/템플릿 ID·버전, 작업 크기, product_box, text_safe_zone | 기존 로컬 계약. 좌표 0~1, 영역 비중첩 및 작업 크기 배수 검사는 코드에 있음 |
| 기존 로컬 이미지 요청/응답 | CutoutRequest, BackgroundRequest, ImageJob | 외부 API의 신규 확정 계약이 아님. 모든 제공자가 마스크·세 레이어를 준다고 가정하지 않음 |

CampaignDraft의 variants는 현재 JSON Schema 자체에 1개 제한이 없다. MVP에서는 호출·응답 검증으로 한 개를 선택하는 계약을 추가 합의해야 한다. 제품 특징·광고 목적은 기존 ProductFacts에 모두 들어 있지 않아 ServiceIntakeDraft에서 제안했다.

## 새 제안 사용 조건

ServiceIntakeDraft의 최종 비율 4:5, 후보 수 1, 채널 instagram_feed는 MVP 초안이다. **최종 픽셀 크기는 미정**이며 작업용 캔버스와 구분한다. 제품 사진이 같은 제품인지, 파일 권한이 맞는지는 이 JSON만으로 검증되지 않는다. 기능성 제품의 기능성 종류 필수화는 제안 규칙이며 팀 확정 전이다.

ImageHandoffDraft에서 source_revision은 copy_revision을 뜻하도록 제안했다. 기존 코드의 의미가 팀 전체에서 확정됐다는 뜻은 아니다. copy_review의 revision 및 프롬프트 revision이 맞아야 하며, copy_hash는 현재 CopyBundle의 canonical hash와 별도 비교해야 한다. 예제는 pending이므로 생성 실행 승인 예제가 아니다. 실제 호출 전 현재 카피에 대한 사용자 검토·자동 검수 정책을 서비스가 확인해야 한다.

키·Bearer 토큰·제공자 서명 URL은 스키마나 공개 예제에 넣지 않는다. product_photos/reference_photos는 업로드 후 서비스가 돌려준 AssetRef로 연결한다. 예제의 자산은 존재하지 않는다.

## 기존 HTTP와 제안의 구분

현재 내부 API는 POST /internal/v1/assets → AssetRef, PUT /internal/v1/jobs/{job_id} → ImageJob, GET /internal/v1/jobs/{job_id} → ImageJob을 제공한다. PUT은 CutoutRequest 또는 BackgroundRequest를 받는다. ServiceIntakeDraft/ImageHandoffDraft를 이 경로로 보내면 현재 구현에서는 거절된다. 다혜님의 텍스트 모듈을 함수로 받을지 별도 API로 받을지와 SSE 이벤트는 아직 합의 대상이다.

기존 ImageJob 상태는 queued/running/succeeded/failed/interrupted다. 외부 제공자 접수 여부가 불확실한 상태·제공자 요청 ID·사용량·오류 계약은 아직 추가 설계가 필요하다. 기존 내부 PUT 재사용이 외부 API의 1회 과금을 보장하지 않는다.

## 다음 합의할 항목

아래 목록은 초안 검토 질문이다. 구현 진행 상태는 저장소의 `docs/imageTodo.md` 한 곳에서 관리한다.

- [ ] 다혜님 실제 스키마와 필드명·타입·enum·nullable/누락 차이 비교.
- [ ] source_revision 의미, copy/product/prompt 버전 및 hash 계산 방식 확정.
- [ ] 제품 사진 2~5장·참고 0~5장, 동일 제품 확인, 사진별 역할과 우선순위 결정.
- [ ] 카피 사용자 검토와 이미지 사용자 검토를 별도 계약으로 정의. 수정 시 과거 검토 무효화.
- [ ] 최종 4:5 픽셀 크기, Safe Zone, 템플릿별 글자 수·폰트·줄 수 결정.
- [ ] 외부 API 제공자 선정 후 입력 수·negative prompt·마스크·seed·결과 레이어 지원 확인.
- [ ] 외부 결과 자산, 제공자 ID, 불확실 제출·타임아웃·429·정책 거절·비용 구조 정의.
- [ ] 최종 이미지 + 동일 카피 revision의 caption/hashtags 다운로드 응답 합의.
- [ ] 서비스/API/렌더러 담당 및 교차 테스트 인수자 확정.

## 검증 범위

기존 Pydantic에서 JSON Schema 13종을 추출하고, 제안 2종과 예제 4종을 생성했다. 예제는 해당 Pydantic 모델로 통과했고 제품 사진 1장/6장, 카피 revision 불일치를 거절하는지 확인했다. Pydantic의 필드 간 검증은 JSON Schema만으로 모두 표현되지 않는다. 이 확인은 실제 팀 연동·외부 API·이미지 품질 테스트가 아니다.

## 다혜님 작업 확인 근거

[협업일지 (김다혜)](https://app.notion.com/p/3feb13f77dbd80bfb313fd9e82488278)의 10/20 기록에는 텍스트 체크리스트 수정, 제품 사진 촬영, 광고 문구 10개 수집을 했다고 적혀 있다. 스키마 초안(제품 분석/기획안·카피/규제 검수 리포트), text_module 디렉터리, OpenAI 연결 테스트는 다음 할 일로 기재돼 있다. 실행 성공이나 파일 제공을 확인한 것은 아니다.

[텍스트 체크리스트](https://app.notion.com/p/3ffb13f77dbd80e6a5eec41286b29aaa)는 스키마 10/23 초안·10/26 확정, 이미지에 제품 분석 JSON·영어 프롬프트 전달을 계획한다. 확인한 두 페이지와 팀 작업 링크에서는 실제 스키마/코드 첨부를 찾지 못했다. 공유 드라이브 내용은 미확인이다. 로컬 두 프로젝트 범위의 이름·스키마 검색에서도 다혜님 작성물로 식별되는 파일은 찾지 못했다. 기존 schemas/를 다혜님 산출물로 볼 근거는 없다.
