# B12 · 화장품 프리셋 실비교와 Langfuse 관리 검증

검증일: 2026-10-23. 사용자 요청으로 실제 편집 3회를 수행했다. 결과는 가상 무상표 제품 샘플이며 실제품·팀 인수 완료가 아니다.

## 실제 실행

- 동일 원본: 앞선 생성 결과의 라벤더 세럼 병·코럴 펌프·빈 크림색 라벨. 원본 SHA-256: `db8f65e86b2d59b1f48cf0c13fd36cf87d88200fa6111872a4f79f1edf72bbba`.
- 모델: `gpt-image-2.5-sunburst-2026-09-08`, 편집, medium, 1152×1440, 1장씩, product_right.
- API POST 총 3회, 성공 PNG 3장. 별도 생성·누끼·재시도는 하지 않았다. 결과 해시와 저장 파일이 일치한다.
- 실행 폴더: 로컬 `beautyAi/outputs/cosmetics-comparison/run-20261023T011517Z-2721464a4524`. `index.html`, `report.json`, 각 프리셋 폴더의 `image.png`, `provider-prompt.txt`, `request.json`, `langfuse-readback.json`을 보관한다. 생성 자산과 런타임 DB는 Git에서 제외한다.

| 프리셋 | 소요 시간 | 입력 텍스트 / 이미지 토큰 | 출력 이미지 토큰 | 추정 USD |
| --- | ---: | ---: | ---: | ---: |
| clean_studio | 14.298초 | 409 / 1505 | 418 | 0.026625 |
| premium_minimal | 16.194초 | 423 / 1505 | 418 | 0.026695 |
| vivid_pop | 19.258초 | 419 / 1505 | 418 | 0.026675 |
| 합계 | 순차 작업 49.750초 | 1251 / 4515 | 1254 | **0.079995** |

합계 토큰 7020. 시간에는 내부 작업 제출·조회 대기가 포함되므로 순수 모델 추론 시간과 다르다. [공식 모델 단가](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst)의 입력 텍스트 $5/M, 입력 이미지 $8/M, 출력 이미지 $30/M으로 계산한 추정이다. 실제 청구액은 null / `UNRECONCILED`이며 예약 한도 $3은 지출액이 아니다. 사용한 키는 기존 MidProject에서 가져온 키이며 현재 팀 지급 키 여부는 여전히 미확인이다.

## Langfuse 실제 확인

- 기존 프로젝트의 `beautyai/cosmetics/clean-studio`, `beautyai/cosmetics/premium-minimal`, `beautyai/cosmetics/vivid-pop`을 각각 text 버전 1, production으로 등록했다.
- 원격 버전 1을 다시 읽고 변수를 채운 최종 문장을 작업 스냅샷과 요청 해시에 포함했다. 초기 문장은 B11 로컬 프롬프트와 동일하다.
- SDK 5.0.4의 generation 기록 3개를 만든 뒤 CLI v2 observations로 다시 조회했다. 이름·버전·요청 해시·출력 이미지 해시·텍스트/이미지별 사용량이 로컬 영수증과 일치했다.
- 관찰 환경: `beautyai-preset-comparison`. 세 실행을 같은 run_id 세션으로 묶었다. SDK flush 요청만으로 성공 처리하지 않고 readback을 확인했다.
- [클린 실행](https://jp.cloud.langfuse.com/project/cmt966agt0007ad0jn1c3s4up/traces/53c0478bfd944cd020574f82b293260e), [미니멀 실행](https://jp.cloud.langfuse.com/project/cmt966agt0007ad0jn1c3s4up/traces/3b35dfb31997bf1b30678e343a1c812d), [밝은 컬러 실행](https://jp.cloud.langfuse.com/project/cmt966agt0007ad0jn1c3s4up/traces/a6e0efdb4924ace6f2f9f1de2c3278d6). 계정 접근 권한이 필요하다.
- API 비밀키·이미지 바이트·로컬 자산 경로는 직접 작성한 관찰 입력에 넣지 않았다. 로컬 readback은 필요한 필드만 남긴다.

## 시각 비교

AI가 원본과 결과 세 장을 직접 확인했다. 사람의 최종 승인은 `PENDING`이며 자동 품질 승인은 하지 않는다.

| 프리셋 | 관찰 | 용도 제안 / 보완 |
| --- | --- | --- |
| 클린 | 흰 배경과 부드러운 그림자. 원본의 차분한 무광 느낌에 가장 가까움 | 상품 소개·상세페이지. 장면 차별화는 약함 |
| 고급 미니멀 | 아이보리 받침대와 방향성 조명으로 고급 분위기가 분명함 | 브랜드 캠페인. 병이 조금 길어 보이고 그림자·배치가 재구성됨 |
| 밝은 컬러 | 코발트·코럴 대비가 강하고 왼쪽 카피 공간이 분명함 | SNS 광고 우선 추천. 병의 광택·밝은 반사가 원본보다 강함 |

세 장 모두 한 제품 전체를 오른쪽에 배치했고, 새 광고 문구나 꽃 장식은 생성되지 않았다. 기본 꽃 추가 요구를 제거한 결과이며 꽃 금지 정책은 없다. 빈 라벨 예제라 인쇄 글자·브랜드 로고 보존은 이번 실행으로 검증하지 못했다. 표본은 각 1장으로 일관성·우월성을 일반화할 수 없다.

## 코드 검증

- `.venv/bin/python -m pytest -q -W error`: **934 passed, 19.88s**.
- 로컬 beautyAi에서도 선택 의존성 설치 후 신규 68개 PASS(7.04s), pip check PASS. FiveCircles 설정·링크와 README 5절, diff 검사 PASS.
- 신규 Langfuse 57개: 모든 프리셋/배치의 기존 최종 문장 동일, 명시적 꽃 연출 유지, 변수 1회 치환, 이름/버전/해시 검증, 기본 요청 body/hash 보존, 실제 multipart 전송, SDK 실패 격리, 사용량 구분.
- 신규 비교 실행기 11개: 명시적 실행 플래그, 비밀값 미출력, CLI의 API 키 분리, 실패 종료 코드, 원자적 보고서 쓰기, 실제 내부 앱+모의 HTTP에서 정확히 3회 호출, 관찰 실패에도 성공 파일 보존.
- 선택 의존성 `observability`를 설치한 비교 실행 경로에 관리/관찰을 연결했다. 기존 모든 서버 요청에 원격 조회를 자동 추가한 것은 아니다. 호출자는 큐 제출 전에 원격 프롬프트를 고정해야 한다.
- [구현 계약](../architecture/contract/langfuse-prompts.md), [관리 안내](../../docs/langfusePromptManagement.md), [오류/수정](errorlogs/2026-10-23-langfuse-b12.md).
