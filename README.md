# 화장품 광고 AI 래퍼 참고 구현

소상공인 화장품 광고 제작 MVP에 연결할 Python 참고 패키지입니다. 텍스트·VLM은 OpenAI API로, 이미지 작업은 별도의 private inference 서버로 분리합니다. 내부 이미지 FastAPI 앱·SQLite 작업 ledger·단일 호스트 worker를 포함합니다. **실제 GPU 모델·광고 UI·제품 승인까지 연결된 완성 서비스는 아닙니다.**

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
| `INFERENCE_BASE_URL` | 직접 기동한 내부 이미지 API 주소 |
| `INFERENCE_INTERNAL_TOKEN` | 내부 서버 인증 토큰 |

모델은 계정에서 사용할 수 있고 해당 입력·구조화 출력을 지원하는지 확인한 뒤 설정합니다. 동시성·시간 제한의 초기값은 `.env.example`과 [클라이언트 조립 예제](examples/wiring.py)를 참고하세요.

호스트 애플리케이션에서 `OpenAITextGateway`와 `HTTPImageGateway`를 한 번 생성해 재사용하고 종료 시 SDK·HTTP 클라이언트를 닫습니다. 실행 순서는 **검증된 제품 이미지 관찰 → 사용자가 제품 사실 확인 → 카피 후보 생성 → 후보 전체 검수 → 선택·수정 시 새 revision으로 재검수 → 이미지 작업 제출·상태 조회**입니다. `examples/wiring.py`는 이 연결을 위한 예시이며 `uvicorn`으로 실행하는 API 서버가 아닙니다.

내부 이미지 API는 `python -m pip install -e '.[inference]'`로 의존성을 설치한 뒤 호스트에서 `create_app(ledger, assets, credentials, processor=None)`로 조립합니다. `ledger`는 `JobLedger`, `assets`는 `LocalAssetStore`, `credentials`는 **서버 소유 32자 이상 Bearer 토큰 → 프로젝트 UUID** 매핑입니다. 사용자가 보낸 project 헤더를 권한으로 사용하지 않습니다. 토큰은 환경변수나 비밀 저장소에서 주입합니다.

`processor`를 제공하지 않으면 작업은 영속 `queued` 상태로 남습니다. 같은 ledger와 자산 저장소를 쓰는 worker에 동기 processor를 연결해야 실행됩니다. 이 factory를 생성하는 것만으로 모델 로딩이나 외부 포트 공개가 시작되지는 않습니다. worker는 POSIX 잠금을 지원하는 단일 호스트/공유 로컬 파일시스템을 전제로 합니다.

상세 호출 코드와 기존 안내는 [README2.md](README2.md)에 있습니다. 최신 로컬 검증은 [B2 기록](fivecircles/test/relay-b2.md)의 **112 tests PASS**이며, [TEST_REPORT.md](TEST_REPORT.md)는 초기 래퍼 검증 기록입니다.

## 2. 기능

| 구현 요소 | 현재 하는 일 |
| --- | --- |
| `OpenAITextGateway` | 제품 이미지 관찰, 광고 기획·카피 후보 생성, 문맥 검수, 수정안 생성 |
| 구조화 계약 | Pydantic 입력·출력 검증과 [JSON Schema](schemas/) 제공 |
| `ReviewService` | 전체 카피의 규칙 검사와 LLM 검사 병합, 검토된 규칙집 확인, revision·해시로 검수 대상 식별 |
| `HTTPImageGateway` | 내부 이미지 작업의 제출·조회·폴링, 작업 ID·입력 해시 확인, 불확실한 제출과 대기 상태 구분 |
| `inference_api.create_app` | Bearer 인증과 프로젝트 범위의 job PUT/GET·자산 업로드/조회·인증된 health 응답 |
| `JobLedger` | SQLite WAL 기반 atomic ID/hash 등록, 현재 owner와 terminal 상태 보호, durable queued 조회 |
| `SingleHostWorker` | 프로세스 간 POSIX 실행 잠금, 종료 확인 후 interrupted 복구, 결과 파일 검증 후 success |
| `LocalAssetStore` | 파일 형식·크기·회전·색 공간 정규화, 프로젝트별 파일/SQLite 저장과 hash 검증 |
| `image_preparation.py` | 제품 후보 캔버스·보호 마스크 생성, Safe Zone 검사와 불투명 픽셀 보존 검사 |
| `image_integrity.py` | 보호 마스크를 인페인트 마스크로 반전하고 승인 RGBA를 장면 위에 재합성 |
| 공통 오류·메타데이터 | 거절·미완료·타임아웃·프로토콜 오류 분리, 요청 ID·사용량·소요 시간 기록 |

검수 상태는 `no_flags`·`warnings`·`blocked`·`unavailable`입니다. `no_flags`는 설정된 자동 검사에서 표시된 이슈가 없다는 뜻이며 법적 승인이나 광고 효과의 보장이 아닙니다. 규칙집이 준비되지 않았거나 검수가 실패하면 `unavailable`로 남깁니다.

## 3. 아키텍처

```text
[호스트 구현 필요: UI / 사용자 인증 / CampaignService / SSE / Export]
   ├─ OpenAITextGateway ── OpenAI API
   ├─ ReviewService ── 규칙 검사 + TextGateway 문맥 검사
   └─ HTTPImageGateway ── 내부 FastAPI 앱
                           ├─ Bearer → project / LocalAssetStore
                           └─ JobLedger (SQLite WAL)
                                └─ SingleHostWorker (POSIX 실행 잠금)
                                     └─ 주입한 processor
                                          └─ [연결 필요: 실제 누끼·배경 모델]

공통 계약: contracts.py / schemas/    이미지 후보: image_preparation.py
제품 재합성: image_integrity.py      결과 파일 검증 → terminal commit
```

내부 API는 `PUT/GET /internal/v1/jobs/{job_id}`, `POST /internal/v1/assets`, `GET /internal/v1/assets/{asset_id}`를 제공합니다. 동일 프로젝트·작업 ID·입력 hash는 기존 작업을 반환하고, 같은 ID·다른 hash는 `409`입니다. 다른 프로젝트의 job·자산은 미존재와 같이 처리합니다. 접수는 DB commit 이후 응답하며, worker는 DB에서 queued 작업을 다시 찾습니다.

`GET /internal/v1/health`는 인증 후 해당 앱의 worker 구성·실행 상태·안전한 fatal code를 알려줍니다. worker fatal은 `503`이며 `model_readiness`는 `not_assessed`입니다. health 성공은 모델 준비나 이미지 품질 증거가 아닙니다.

실행 잠금은 한 호스트·같은 ledger에 연결한 동기 processor를 직렬화합니다. 실행 중 stop timeout은 취소가 아니며 잠금을 유지합니다. 새 worker는 이전 프로세스 종료로 잠금을 획득한 뒤 running 작업을 interrupted로 수습합니다. 다중 VM 조정과 분리된 GPU subprocess 관리는 포함하지 않습니다.

검수 snapshot은 제품·카피·옵션·규칙·검수 모델·프롬프트에 묶입니다. 이미지 입력 hash는 시각 입력·파이프라인·seed를 구분하지만 실제 편집 revision과 프로젝트 캐시 연결은 다음 구현 대상입니다. [현재 흐름](fivecircles/architecture/spec/image-runtime-flow.html)과 [설계](DESIGN.md)를 참고하세요.

## 4. 미완성으로서 보완해야 할 점

- **서비스 통합:** 사용자용 API·로그인·CampaignService·편집 revision·프로젝트 캐시·SSE·내보내기·렌더러를 연결해야 합니다. 내부 API 인증과 로컬 job 저장·복구는 구현했습니다.
- **실제 모델:** 누끼·배경 모델, 서버 소유 profile/revision, 모델 준비·warm-up과 processor adapter가 필요합니다. 현재 테스트 processor는 실제 누끼·SDXL 모델이 아닙니다.
- **제품 승인:** 후보 RGBA·마스크를 현재 제품 버전으로 승인하고, 변경 시 승인을 무효화하는 저장·검증 흐름이 필요합니다.
- **검수 데이터:** 출처·버전·검토 이력이 있는 규칙집, 제품 사실 확인, 카피 수정 후 재검수·DB revision 경쟁 검사가 필요합니다.
- **품질·실환경:** 라벨·경계·투명/반사·그림자·여백·최종 텍스트 합성, 실제 모델 호출·지연·VRAM·비용을 평가해야 합니다. 불투명 픽셀 검사만으로 광고 품질을 승인하지 않습니다.
- **운영:** 실제 네트워크 접근 범위·비밀값 관리·배포·예산 상한·모델 모니터링을 구성해야 합니다. 로컬 HTTP/파일/DB 테스트와 운영 GPU 검증을 구분합니다.

## 5. 하지 말 것

- API 키, 실제 `.env`, 로컬 설정·DB·로그·테스트 캐시, 업로드·생성 이미지, 모델 가중치를 커밋하지 않습니다. `.env.example`에는 빈 값이나 명백한 예시만 둡니다.
- VLM이 읽은 라벨·효능 문구를 확인된 제품 사실이나 기능성 분류로 자동 승격하지 않습니다. 업로드 이미지·사용자 텍스트를 시스템 지시로 취급하지 않습니다.
- 검수 실패·규칙집 부재·오래된 snapshot을 통과로 처리하지 않습니다. LLM 결과로 규칙 검출을 지우거나 `no_flags`를 법적 적합 판정으로 설명하지 않습니다.
- 사용자 제공 URL을 검증 없이 서버에서 가져오지 않습니다. 이미지 디코딩·용량·픽셀 수·메타데이터와 자산 접근 권한을 별도로 검증합니다.
- 제출 응답 유실이나 폴링 시간 초과 뒤 새 작업 ID로 무조건 재생성하지 않습니다. 기존 ID를 조회·복구합니다. 클라이언트 semaphore를 서버 전체 GPU 동시성이나 영속 멱등성으로 간주하지 않습니다.
- SDK 재시도 위에 무제한 재시도를 덧씌우지 않습니다. 카피 수정 때문에 배경을 재생성하거나, 참고 문서의 속도·메모리·보존율 수치를 실측 결과처럼 쓰지 않습니다.
- 공개 저장소는 `plan/models`에서 모델 코드를, `박지수_협업일지`에서 날짜별 작업 기록을 관리합니다. 협업일지는 PR·병합 없이 별도로 관리합니다. 자세한 운영 방식은 [README2.md](README2.md)를 따릅니다.
