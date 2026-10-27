# 2026-10-26 협업일지 검증 근거

보고일: 2026-10-26(KST). 소급 작성일: 2026-10-27. 당일 작업과 다음 날 변경을 분리한다.

## 코드·문서 게시

| 근거 | 확인 범위 |
| --- | --- |
| [2dd0d9a](https://github.com/uyt5041-lab/beauty-ai-models/commit/2dd0d9a55eb87b9418b5f6944b3dea5707450000) | 이미지 구현 통합과 구조 안내 |
| [09226a6](https://github.com/uyt5041-lab/beauty-ai-models/commit/09226a6d6713a5e99593a27a4da7df086b3badfc) | feature/image-model 브랜치 명칭 반영 |
| [a8d464a](https://github.com/uyt5041-lab/beauty-ai-models/commit/a8d464a906407f5875b171d99778c2d70f3beeaa) | README의 고정 프롬프트 위치·사용법 |
| [이미지 구조](https://github.com/uyt5041-lab/beauty-ai-models/blob/a8d464a906407f5875b171d99778c2d70f3beeaa/docs/imageArchitecture.md) | 폴더 트리·실행 그래프·계약 경계 |

README의 기본 5개 절, 상대 링크, 로컬 미리보기 명령과 문서 미러 일치를 확인했다. 이미지 호출 없는 프롬프트 미리보기였으며 실제 이미지 품질 검증은 아니다.

## 백엔드 연동 리뷰

기준: [본레포 f979331](https://github.com/taemin050-sys/project-it-tem/tree/f9793315b3db9d142958237494e199e56196fbd2). 단계별 브랜치 일곱 개가 선형으로 이어지는 것을 확인했다. 검토 당시 dev는 d5d07aa, main은 05ed2b0이었다. 이후 원격 상태는 이 기록으로 단정하지 않는다.

| 확인 사항 | 코드 근거 | 검증 범위 |
| --- | --- | --- |
| 실제 이미지 HTTP 경로 미연결 | [main.py](https://github.com/taemin050-sys/project-it-tem/blob/f9793315b3db9d142958237494e199e56196fbd2/src/serving/main.py#L19) | 로컬 ai/image 404, capabilities의 image_provider_ready=false |
| 텍스트 실패 뒤 재요청 | [text_workflow.py](https://github.com/taemin050-sys/project-it-tem/blob/f9793315b3db9d142958237494e199e56196fbd2/src/serving/text_workflow.py#L34) | 모의 제공자 502 두 번, 호출 두 번. 실제 과금 없음 |
| 이미지 계측 전달 필드 부족 | [p4_image_adapter.py](https://github.com/taemin050-sys/project-it-tem/blob/f9793315b3db9d142958237494e199e56196fbd2/src/serving/p4_image_adapter.py#L23) | mock handoff의 모델·시간 null, 사용량/프롬프트 버전 전달 규격 없음 |
| 기존 합성기 재사용 시 크기 차이 | [mock_workflow.py](https://github.com/taemin050-sys/project-it-tem/blob/f9793315b3db9d142958237494e199e56196fbd2/src/serving/mock_workflow.py#L94) | 1152×1440 모의 입력은 그대로 출력되지만 응답은 1080×1350. 기존 정상 mock 입력은 1080×1350 |
| 동일 작업의 새 후보 차단 | [p4_cost_guard.py](https://github.com/taemin050-sys/project-it-tem/blob/f9793315b3db9d142958237494e199e56196fbd2/src/serving/p4_cost_guard.py#L65) | 같은 job/phase에 새 operation key를 넣으면 409 |
| 인증 경계 | [job_api.py](https://github.com/taemin050-sys/project-it-tem/blob/f9793315b3db9d142958237494e199e56196fbd2/src/serving/job_api.py#L71) | 인증 없이 로컬 업로드 201. 외부 배포 상태는 확인하지 않음 |

전체 테스트는 한글 폰트 설정 후 **118 passed / 1.26초**였다. Python 3.11.15, FastAPI 0.159.1, Pydantic 2.14.1, httpx 0.28.1, Pillow 13.0.0, pytest 9.1.1 환경이다. 백엔드 lock의 python-multipart 0.0.32를 별도 검토 경로에 설치했다. 전체 GCP lock 재현은 아니며 React 브라우저·GCP 배포·실제 통합 호출은 검증하지 않았다. 소스 수정이나 원격 push도 하지 않았다.

## 타이포그래피 조사

- [OpenAI 이미지 제한사항](https://developers.openai.com/api/docs/guides/image-generation#limitations): 텍스트 배치·선명도에 한계가 있다. 한글 화장품 광고에 대한 성공률은 이번 공식 자료 조사에서 확인하지 못했다.
- [이미지 프롬프트 가이드](https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide): 정확한 문구·서체 스타일·색·위치를 요청할 수 있으나 정확한 폰트 파일 렌더링과 동일하지 않다.
- [Pillow 텍스트 렌더링](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html): 실제 폰트로 글자 레이어를 만들고 합성하는 경로를 제안했다. 신규 구현·시연 성공을 주장하지 않는다.
- [Sunburst 지원표](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst): 조사 당시 이미지 생성 모델 파인튜닝 미지원.
- [Vision fine-tuning](https://developers.openai.com/api/docs/guides/vision-fine-tuning): 이미지 이해 학습과 이미지 생성 학습을 구분했다.
- [fal FLUX LoRA 학습 API](https://fal.ai/models/fal-ai/flux-lora-fast-training/api): 다른 플랫폼의 학습 경로를 확인했지만 도입·호출·학습은 하지 않았다.

## 진행률 산정

- 원본: [a8d464a의 docs/imageTodo.md](https://github.com/uyt5041-lab/beauty-ai-models/blob/a8d464a906407f5875b171d99778c2d70f3beeaa/docs/imageTodo.md).
- 결과: **44/186개, 23.7%**. 원본 한 곳의 전체 체크를 분모로 사용했다. 원본의 완료 체크와 보고일 계획 범위가 함께 충족되는 항목만 분자에 넣었다.
- 원본 일정에 여러 날짜가 있으면 단계 시작일을 사용한다. 날짜 불명과 이후 계획 항목은 일지에서 완료로 세지 않는다. GPU 재선택 조건부 항목을 현재 API MVP 완료로 합산하지 않는다.
- [schedule-map.json](schedule-map.json)은 W&B 단계와 명시적 10/23 후속 검증 제목을 날짜에 연결한다. 항목이 재구성되어 이전 T번호 기반 매핑은 재사용하지 않았다. 실제품 실행기 단계는 원문의 10/23 계획 시작을 사용하지만 체크가 미완료여서 완료 수를 늘리지 않는다.
- 과거 42/150개, 28.0% 기록은 당시 원본 기준으로 보존한다. 분모·구성이 바뀌었으므로 새 비율을 작업 후퇴나 제품 완성률로 해석하지 않는다.
- 외부 API·SSOT 연결 묶음은 전체 TODO에 별도로 표시하며 전체 수에 중복 합산하지 않는다. 날짜 불명으로 일지에서 체크되지 않은 것은 원본 구현 실패 판정이 아니다.
- 진행률 생성 도구 정책 회귀 7개 통과. Markdown·HTML·SVG·JSON 수치를 함께 검증한다. 생성 도구 검증을 실제 모델 품질 평가로 보고하지 않는다.

## 다음 날 작업 제외

2026-10-27의 워터마크 정책 변경, Langfuse 새 버전 등록, 해당 코드 테스트는 별도 날짜의 작업이다. 이 일지의 10/26 완료 성과나 진행률 분자에 넣지 않는다.
