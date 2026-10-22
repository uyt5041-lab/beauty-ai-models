# 화장품 광고 AI 래퍼 참고 구현

소상공인 화장품 광고 제작 MVP에 연결할 Python 참고 패키지입니다. 텍스트·VLM은 OpenAI API로, 이미지 작업은 별도의 private inference 서버로 분리합니다. **웹 UI·FastAPI 서버·GPU 모델을 포함한 완성 서비스는 아닙니다.**

## 1. 쓰는 법

Python 3.11 이상에서 프로젝트 루트 기준으로 설치합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q -W error
```

설정 예시는 [.env.example](.env.example)에 있습니다. 파일에는 비밀값을 넣지 마세요. 실제 값은 로컬 환경변수나 별도의 비밀 관리 수단으로 주입합니다. **패키지는 `.env`를 자동으로 읽지 않습니다.**

| 환경변수 | 용도 |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API 키 |
| `OPENAI_TEXT_MODEL` | 기획·카피 생성 모델 |
| `OPENAI_VISION_MODEL` | 제품 이미지 관찰 모델 |
| `OPENAI_REVIEW_MODEL` | 문맥 검수 모델 |
| `INFERENCE_BASE_URL` | 직접 구현할 이미지 추론 서버의 내부 주소 |
| `INFERENCE_INTERNAL_TOKEN` | 내부 서버 인증 토큰 |

모델은 계정에서 사용할 수 있고 해당 입력·구조화 출력을 지원하는지 확인한 뒤 설정합니다. 동시성·시간 제한의 초기값은 `.env.example`과 [클라이언트 조립 예제](examples/wiring.py)를 참고하세요.

호스트 애플리케이션에서 `OpenAITextGateway`와 `HTTPImageGateway`를 한 번 생성해 재사용하고 종료 시 SDK·HTTP 클라이언트를 닫습니다. 실행 순서는 **검증된 제품 이미지 관찰 → 사용자가 제품 사실 확인 → 카피 후보 생성 → 후보 전체 검수 → 선택·수정 시 새 revision으로 재검수 → 이미지 작업 제출·상태 조회**입니다. `examples/wiring.py`는 이 연결을 위한 예시이며 `uvicorn`으로 실행하는 API 서버가 아닙니다.

상세 호출 코드와 기존 안내는 [README2.md](README2.md), 현재 테스트 범위는 [TEST_REPORT.md](TEST_REPORT.md)에 정리했습니다.

## 2. 기능

| 구현 요소 | 현재 하는 일 |
| --- | --- |
| `OpenAITextGateway` | 제품 이미지 관찰, 광고 기획·카피 후보 생성, 문맥 검수, 수정안 생성 |
| 구조화 계약 | Pydantic 입력·출력 검증과 [JSON Schema](schemas/) 제공 |
| `ReviewService` | 전체 카피의 규칙 검사와 LLM 검사 병합, 검토된 규칙집 확인, revision·해시로 검수 대상 식별 |
| `HTTPImageGateway` | 내부 이미지 작업의 제출·조회·폴링, 작업 ID·입력 해시 확인, 불확실한 제출과 대기 상태 구분 |
| `LocalAssetStore` | 파일 형식·크기·회전·색 공간 정규화, 프로젝트별 파일/SQLite 저장과 hash 검증 |
| `image_preparation.py` | 제품 후보 캔버스·보호 마스크 생성, Safe Zone 검사와 불투명 픽셀 보존 검사 |
| `image_integrity.py` | 제품 알파를 인페인트 마스크로 변환하고 원본 RGBA를 장면 위에 재합성 |
| 공통 오류·메타데이터 | 거절·미완료·타임아웃·프로토콜 오류 분리, 요청 ID·사용량·소요 시간 기록 |

검수 상태는 `no_flags`·`warnings`·`blocked`·`unavailable`입니다. `no_flags`는 설정된 자동 검사에서 표시된 이슈가 없다는 뜻이며 법적 승인이나 광고 효과의 보장이 아닙니다. 규칙집이 준비되지 않았거나 검수가 실패하면 `unavailable`로 남깁니다.

## 3. 아키텍처

```text
[미구현: UI / FastAPI routes / CampaignService / DB / SSE / Export]
                 │ 호스트 애플리케이션에서 연결
                 ├─ TextGateway (Protocol)
                 │    └─ OpenAITextGateway ── OpenAI API
                 ├─ ReviewService ── 규칙 검사 + TextGateway 문맥 검사
                 └─ HTTPImageGateway ── private inference HTTP 계약
                                           └─ [미구현: GPU 서버·모델·영속 작업 저장소]

공통 데이터: contracts.py / schemas/     공통 오류: errors.py
이미지 유틸리티: image_integrity.py
```

이미지 클라이언트는 자체 프로토콜인 `PUT /internal/v1/jobs/{job_id}`, `GET /internal/v1/jobs/{job_id}`를 기대합니다. 이 패키지에는 해당 엔드포인트를 제공하는 서버가 없습니다. 서버가 작업 ID와 입력 해시를 원자적으로 영속 저장하고, 동일 ID·동일 입력은 기존 작업을 반환하며, 동일 ID·다른 입력은 `409`를 반환해야 합니다.

검수 snapshot은 제품 정보·전체 카피·광고 설정·규칙집·검수 모델·프롬프트 버전에 묶입니다. 이미지 캐시는 시각 입력과 파이프라인 설정을 기준으로 분리해 문구만 수정할 때 GPU를 다시 실행하지 않는 구성을 지향합니다. DB 트랜잭션·워크플로·실제 캐시 저장소는 호스트 서비스에서 구현해야 합니다. 상세 설계는 [DESIGN.md](DESIGN.md)를 참고하세요.

## 4. 미완성으로서 보완해야 할 점

- **서비스 통합:** UI, FastAPI 라우트, 사용자 인증, SQLite 작업 기록, 재시작 복구, SSE, 결과 내보내기 구현. 로컬 파일 저장소의 프로젝트 경계는 구현했으며 인증된 프로젝트 컨텍스트 연결은 필요합니다.
- **이미지 서버:** 누끼·배경 생성 모델과 GPU 작업 실행기, 영속 멱등성, 내부 인증, 재시작·타임아웃 시 복구 구현. SDXL·LCM·IP-Adapter·BiRefNet은 이 패키지에서 실행되지 않습니다.
- **검수 데이터:** 출처·버전·검토 이력이 있는 규칙집, 미확인 제품 사실의 사용자 확인, 카피 수정 후 재검수와 저장 시점의 revision 경쟁 상태 검사.
- **출력 품질:** 투명 용기·반투명 경계·그림자·제품 외형·로고 보존 평가. 단순 알파 합성은 실제 광고 품질을 보장하지 않습니다. 한글 텍스트 합성·템플릿 렌더러도 별도로 필요합니다.
- **실연동 검증:** 실제 계정의 모델 지원, Responses API 호출, 이미지 서버 통신, 한국어 품질, 비용·지연·VRAM·동시 요청을 검증하고 SDK·모델·설정을 고정.
- **운영 보완:** 로컬 업로드 검증을 API에 연결하고 비밀값 관리, 예산·재시도 상한, 관측과 실패 복구를 구현해야 합니다. 단위 테스트 통과와 실서비스 사용 가능 여부를 구분합니다.

## 5. 하지 말 것

- API 키, 실제 `.env`, 로컬 설정·DB·로그·테스트 캐시, 업로드·생성 이미지, 모델 가중치를 커밋하지 않습니다. `.env.example`에는 빈 값이나 명백한 예시만 둡니다.
- VLM이 읽은 라벨·효능 문구를 확인된 제품 사실이나 기능성 분류로 자동 승격하지 않습니다. 업로드 이미지·사용자 텍스트를 시스템 지시로 취급하지 않습니다.
- 검수 실패·규칙집 부재·오래된 snapshot을 통과로 처리하지 않습니다. LLM 결과로 규칙 검출을 지우거나 `no_flags`를 법적 적합 판정으로 설명하지 않습니다.
- 사용자 제공 URL을 검증 없이 서버에서 가져오지 않습니다. 이미지 디코딩·용량·픽셀 수·메타데이터와 자산 접근 권한을 별도로 검증합니다.
- 제출 응답 유실이나 폴링 시간 초과 뒤 새 작업 ID로 무조건 재생성하지 않습니다. 기존 ID를 조회·복구합니다. 클라이언트 semaphore를 서버 전체 GPU 동시성이나 영속 멱등성으로 간주하지 않습니다.
- SDK 재시도 위에 무제한 재시도를 덧씌우지 않습니다. 카피 수정 때문에 배경을 재생성하거나, 참고 문서의 속도·메모리·보존율 수치를 실측 결과처럼 쓰지 않습니다.
- 공개 저장소는 `plan/models`에서 모델 코드를, `박지수_협업일지`에서 날짜별 작업 기록을 관리합니다. 협업일지는 PR·병합 없이 별도로 관리합니다. 자세한 운영 방식은 [README2.md](README2.md)를 따릅니다.
