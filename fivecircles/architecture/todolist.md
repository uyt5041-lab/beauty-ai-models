# BeautyAI 작업 인덱스

이 파일은 배치와 원본 체크리스트를 연결한다. 제품 항목의 체크 상태는 링크한 원본 한 곳에서 관리한다.

## BEAUTY-FC-001 · 프레임워크 도입 · CUSTOM

완료 기준: [도입 계약](contract/framework-setup.md). 검증 결과는 [도입 검증](../test/setup-validation.md)에 기록한다.

- [x] 원본 저장소/고정 버전/라이선스 기록
- [x] BeautyAI 담당·계약·Git·테스트·로컬 스킬 구성
- [x] 기존 소스/TODO 보존, 경로와 두 폴더 일치 확인

## 모델 구현 배치

| 배치 | 시점 | 원본 / 종료 조건 |
| --- | --- | --- |
| 요구/계약·래퍼·실험 준비 | 10/20–10/23 | [이미지 TODO 2절](../../docs/imageTodo.md#2-일정과-마일스톤): API 래퍼·복수 입력·버전 선택·영속 호출 상한·미리보기. 단계 통과 후 다음 진행 |
| 실제품/관찰·팀 통합 | 10/26–10/30 | 권리/사진 적합성·개발 평가·Langfuse/W&B·실제 LLM/렌더러·M1 통합 |
| 품질·FinOps·서비스 인수 | 11/02–11/06 | 개발 개선→설정 고정→독립 검증→청구/원가·장애/사용성→M2 인수. 비중은 이미지40/FinOps40/서비스20 임시안 |
| 텍스트 최종 변경 회귀 | 11/09–11/10 | 다혜 담당의 최종 버전과 이미지·검토·렌더링 영향 회귀 |
| 발표·보고서·결과 제출 | 11/11–11/12 19:00 | 검증 근거와 시연·보고서·GitHub 링크 대조, 제출본 확인 |
| 일지·인계·백업 | 11/13 23:50까지 | 별도 일지 브랜치와 제출·허용 자료 백업 |

위 날짜는 프로젝트 작업량을 배분한 계획이다. 실제 구현 완료일이나 현재 진행률을 뜻하지 않는다. 날짜별 계획 체크는 원본 TODO 2절에서 모두 미체크로 관리하고, 아래 릴레이의 실제 검증 이력은 보존한다.

구현할 항목을 선택하면 작업 ID·원본 항목 링크·담당·완료 기준·검증을 work/worklog에 기록한다. 프레임워크 초기화는 모델 완료를 뜻하지 않는다.

## 구현 릴레이 (날짜는 대기 조건 아님)

- [x] B1 자산 파일/DB·캔버스·보존 검사 구현 및 72 tests/CLI 검증. [원본 완료 항목](../../docs/imageTodo.md), [검증](../test/relay-b1.md).
- [x] B2 영속 작업 API·원자적 멱등성·단일 호스트 worker 복구. 112 tests 및 원격8cb6402 확인.
- [x] B3 profile·승인·실행 연결 및 결과 파일 검증. 로컬 148 tests·CLI PASS, dbba5da 게시 확인. [검증](../test/relay-b3.md).
- [x] B4 프롬프트 수신·revision/캐시·늦은 결과 연결 차단. 201 tests·실제 TCP CLI PASS, a1b1602 게시 확인. [검증](../test/relay-b4.md).
- [x] B5 평가 manifest·마스크/픽셀 지표·job timing 도구. 280 tests·실제 CLI·wheel build PASS. [검증](../test/relay-b5.md).
- [x] B6 고정 BiRefNetLite·명시적 준비/검증·adapter. 339 tests와 실제 MPS/TCP 합성 fixture PASS. [검증](../test/relay-b6.md).
- [x] B7 고정 SDXL·실제 MPS 512 fixture 생성/제품·여백 보존. 433 tests·실제 TCP/CLI PASS, 광고 품질 미평가. [검증](../test/relay-b7.md).
- [x] B8 동일 ID 제출 조회·조정. 521 tests·실제 TCP 응답 유실에서 PUT/처리 각 1회, 미관찰/관찰 후 기한 구분. [검증](../test/relay-b8.md).
- [x] B9 실행 관찰·로컬 반복/크기 측정. 624 tests·실제 MPS6 jobs·동일512 두 번 hash 일치. peak/품질 제외. [검증](../test/relay-b9.md).
- [x] 별도 사용자 요청: SSOT 대조문서·현재 구현/검증/실행 TODO 정리 (2026-10-22). [대조표](../../docs/ssotAlignment.md)와 [원본 TODO](../../docs/imageTodo.md). 신규 SSOT-01~12 구현은 미완료이며 다음 코드 배치를 실행한 것은 아니다.
- 최신 우선순위: [원본 이미지 TODO](../../docs/imageTodo.md)의 외부 API 제공자 조사·adapter·오류/과금·출력 검증. 제공자는 OpenAI로 정했다. 실제품 품질·공유 잔액·확정 청구와 서비스 인수는 별도 검증한다. 로컬 GPU OOM·다음 job 복구는 자체호스팅을 다시 선택할 때의 보존 후보로 전환한다. 부분 결과 자산 처리는 API 경로에서도 필요하다.
- [ ] 실사진 품질·외부 API 실제 실행·서비스/렌더러·팀 인수: 실제 환경/공동 확인 근거 필요. CUDA/GCP GPU 이전은 현재 필수 제외.

- [x] B11 화장품 프롬프트3종·배치·revision2 제공자 지시·오프라인 프리뷰. 기본 꽃 요구만 제거, 꽃 금지 없음. 전체866 tests·로컬60 tests PASS. [검증](../test/cosmetics-prompts-b11.md). 실제품 품질/팀인수는 원본imageTodo의 API-06·SSOT 항목에서 계속 관리한다.

- B12: Langfuse 관리 프롬프트·고정 스냅샷·실행 관찰과 화장품 3종 실비교. [계약](contract/langfuse-prompts.md), [검증](../test/cosmetics-langfuse-b12.md). 서비스 인수와 구분한다.

- B14: W&B의 기존20실행 비교·FinOps40% 임시 평가계획·오프라인 채점. [계약](contract/wandb-experiments.md), [검증](../test/wandb-finops-b14.md). 실제품 평가·실제청구/잔액·런타임 예산통제·서비스 인수는 [원본 TODO](../../docs/imageTodo.md)의 미완료 항목으로 유지한다.
