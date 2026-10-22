# 릴레이 B9 검증 · 작업 실행 관찰·로컬 반복 측정

2026-10-22 · plan/models · 전체 제품 판정 **GAP/PARTIAL**

## 계약과 구현

- worker thread ContextVar recorder가 제한한 model_load/preprocess/model_inference/composite/asset_store/result_validation 단계의 monotonic wall-clock을 기록한다. 직접 processor 호출에 관찰이 없으면 기존처럼 동작한다.
- 요청 장치·모델 revision·재사용·환경·동기화와 작업 후 process 메모리 snapshot을 기록한다. prompt·자산 경로/URL·토큰·원문 예외는 수집하지 않는다.
- `job_executions`의 관찰은 terminal 상태와 같은 transaction으로 저장한다. 기존 wire/hash/이미지 결과/timing은 유지한다. 프로젝트·입력 hash·현재 상태를 대조하고 미기록은 NOT_RECORDED/null로 반환한다.
- GET `/internal/v1/jobs/{job_id}/execution`, CLI `get-execution`은 기존 인증·project 경계를 따른다. 이전/강제중단 작업의 관찰을 소급 생성하지 않는다.

## 코드와 실제 실행 검증

- root 전체 회귀: **624 tests PASS(36.32s, -W error)**. 기존DB·프로젝트·관찰 손상·실패·기록 없는 직접 호출·모델 단계·메모리 관찰·API 경계를 포함한다.
- pip check·wheel·격리 lazy import PASS. wheel SHA-256 `fa2629ec7fd4e4c8a3e8002450424350ee12777aeb359ac364a14535eb015450`.
- 테스트 fixture의 worker 시작 recovery 시각 조회가 누락돼 iterator가 고갈된 실패를 fixture 시각 추가로 수리했다. 제품 코드 결함과 구분하며 최종624 tests PASS를 사용한다.
- 실제 benchmark receipt를 직접 읽었고 source_sha256 6개가 현재 코드와 일치했다. MPS·CPU fallback 없음·합성 drawn input 하나·한 프로세스, 누끼1+배경5 모두 성공했다. get-execution CLI·model reuse·phase·장치 동기화·환경 관찰을 확인했다.

| 실행 | worker 경과(s) | 모델 로딩(s) | inference 단계(s) | 작업 후 MPS driver(bytes) |
| --- | --- | --- | --- | --- |
| cutout-cold | 9.102 | 6.471 | 2.604 | 5,471,289,344 |
| background-512x512-cold | 47.170 | 15.982 | 31.118 | 10,204,545,024 |
| background-512x512-warm | 28.488 | — (재사용) | 28.438 | 10,212,933,632 |
| background-768x768-warm | 57.418 | — (재사용) | 57.337 | 14,113,718,272 |
| background-1024x1024-warm | 104.426 | — (재사용) | 104.289 | 19,876,773,888 |
| background-512x768-warm | 50.855 | — (재사용) | 50.312 | 18,039,750,656 |

- cold는 프로세스 내 해당 모델 첫 로딩, warm은 재사용이다. OS 캐시 제거·장치 재시작 실험은 아니다. model_load 누락을 로딩0초로 표시하지 않는다. 단계는 monotonic wall-clock, queue/ledger는 서버 wall-clock이다. 일부 preprocess가 model_inference 안에 중첩돼 단계값을 단순 합산하지 않는다.
- 같은512 입력/seed42의 job 두 개는 request hash와 3개 output hash가 같았다. scene `d648e34969fa1071059275a8ff55c6d1baba87173503e4d5381623cb3b58ab26`, product `505dc63827703de7a7de9ba0c91a09d3e59b0fd8b6e5691aab0f28b93b8e49a0`, preview `1926867f4c42ccea44024e3c47d01d8f3614d27fcce2ff69226e615d358a50bb`. 다른 장치·버전의 픽셀 동일성은 확인하지 않았다.
- 배경5건의 opaque mismatch0·Safe Zone exact neutral·3개 레이어 hash를 확인했다. 기술 실패는 누끼0/1·배경0/5이며 이 표본을 일반 실패율/품질 보장으로 확대하지 않는다.
- 최고 관찰 MPS driver19,876,773,888 bytes는 작업 후 process snapshot의 최댓값이다. peak·요청별 증가량·CUDA VRAM·비용이 아니다. 사용량 한도/OOM 측정으로 체크하지 않는다.
- root 시각 확인: 512는 꽃 표현이 부족했다. 768은 오른쪽 큰 꽃이 보였지만 turquoise cap 연장과 제품 주변 잔상이 남았다. 1024는 꽃이 보이지 않았고 검은 cap 옆 잔상·바닥 반사가 있었다. 512×768은 오른쪽 가장자리 꽃·분홍 원형 장식이 있었지만 제품 외곽·뚜껑·반사 잔상이 남았다. 기술 실행 PASS이며 광고 품질·안전은 NOT_ASSESSED다.
- 실제 증거는 Git 제외 `outputs/b9-model-benchmark/receipt.json`과 결과 파일에 보존한다. 공개 문서에는 요약/검증 hash만 남긴다.

## 시각 검증과 후속

- [목표/현재 흐름](../architecture/spec/image-runtime-flow.html): 최종 HTML 브라우저 검증 **BLOCKED**. Chrome CUA 정책이 `file:` URL을 금지하고 다른 경로·브라우저로의 우회도 금지했다. 같은 요청을 반복하거나 로컬 HTTP로 우회하지 않았다.
- 최초 Task Space 145의 수정 전 HTML: 이미지2개 decode·제목7개·범례5개·GAP18행·우선순위9행·1492px 가로 넘침 없음. finish 뒤 헤더의 `r4`를 `624`로 고치고 현재 도식의 중복 보조선을 정리했으므로 최종판 검증으로 재사용하지 않는다. 사용자 소유 Task Space126은 사용하지 않았다.
- 현재 Mermaid PNG(1568×2628)는 직접 열어 실행 관찰과 미구현 노드·점선이 유지됨을 확인했다. 최종 HTML 헤더·표·로컬 이미지 참조는 정적 검사했다.
- [보존한 수정 전 스크린샷 — 최종판 증거 아님](playwright-screenshots/image-runtime-flow-b9.png).
- 별도 사용자 요청의 [SSOT 대조문서](../../docs/ssotAlignment.md)·실행 TODO 정리는 완료했다. 신규 SSOT-01~12 구현이나 다음 코드 배치를 시작한 것은 아니다. 후속 로컬 후보는 OOM/저장 실패 뒤 같은 worker에서 다음 정상 job을 처리하는 회복 검증이다. 실패 주입 근거와 실제 GPU 고갈을 구분한다. Git publication은 [게시 기록](../work/integrate-log.md)에서 별도로 확인한다.

원본 TODO의 재현 범위·단계 시간·반복 수/실패율·실행 추적 4개만 추가 체크했다(누적35개). 최대 메모리·실사진/최종 품질·CUDA·렌더러·팀 인수는 미완료다.
