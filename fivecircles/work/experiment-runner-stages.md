# 실험 실행기 단계별 실행 기록

2026-10-23 · 방식: sequential. 소스 기준: plan/models 79ad0b1. 유료 호출 0회.

사용자 지시: 단계별 완료 판정 후 다음 단계 진행. 검증 실패 시 자동 수리·재시도 없이 중단한다.

| 단계 | 범위 | 상태 | 완료 근거 |
| --- | --- | --- | --- |
| 입력 | 제품 2~5장·참고 0~5장, 정규화·중복·크기 | COMPLETED | 55 tests PASS, 실제 JPEG 2장 정규화 PASS |
| 선택 | 프리셋·정확한 버전·고정 요청 | COMPLETED | 176 tests PASS, 독립 코드 검토 P1/P2 없음 |
| 제한 | 영속 시도 상한·실패 시 중단 | COMPLETED | 65 tests PASS, 독립 코드 검토 P1/P2 없음 |
| 미리보기 | JSON/HTML·CLI·통합 검증 | COMPLETED | 전체 1,258 tests PASS, 실제 사진 CLI PREPARED |

## 입력 자료

사용자가 제공한 Drive에서 피지오겔 사진 2장을 로컬 uploads에 받았다. 한 장은 제품이 잘려 광고용 표본으로 부적합하고 다른 장은 손·TRY ME 스티커 검토가 필요하다. 두 장은 입력 처리 시험에만 사용한다. iCloud는 미확인이다. 공개 저장소에 사진·접근 토큰·원본 공유 URL을 추가하지 않는다.

## 검증

1단계: `.venv/bin/python -m pytest -q -W error tests/test_experiment_inputs.py tests/test_assets.py` → **55 passed (0.40s)**. 공유 JPEG 2장 처리도 PASS. 각 3000×4000, 정규화 PNG 12,898,108B / 10,603,218B. 유료 호출 0회. 입력 처리 통과는 광고용 적합성 승인이 아니다. 실제품 광고 품질·유료 API·팀 인수는 이번 판정 대상이 아니다.

2단계: `.venv/bin/python -m pytest -q -W error tests/test_experiment_plan.py tests/test_cosmetics_prompts.py tests/test_langfuse_prompts.py` → **176 passed (0.83s)**. 외부 호출 없이 고정 요청과 프롬프트를 검증했다.

3단계: `.venv/bin/python -m pytest -q -W error tests/test_experiment_limits.py tests/test_provider_attempts.py tests/test_api_image_processor.py tests/test_openai_runtime.py` → **65 passed (1.60s)**. 동시 claim/begin·재시작·실패/미확인 중단과 기존 일반 원장 호환을 확인했다. 슬롯 수는 실제 POST나 청구 확인 횟수가 아니다.

4단계: `.venv/bin/python -m pytest -q -W error` → **1,258 passed (23.01s)**. 입력·프롬프트·상한·CLI·MockTransport 연결과 기존 전체 회귀를 한 번에 검증했다. 독립 정적 검토에서 P1/P2 없음. 테스트 중 의도한 거절·실패 응답은 정상적으로 차단됐으며 실패한 테스트는 없다.

공유 사진 2장으로 실제 `prepare` 명령을 실행했다. `outputs/experiment-trials/20261023-input-preview/`에 JSON/HTML을 저장했고 `PREPARED`, 예정 1회, 상한 1회, 이미지 API 0회를 확인했다. 원본이 잘린 두 번째 입력은 기술 시험용이며 광고용으로 승인하지 않았다. 고정 계획 SHA: `1b4455ece0f6334113ded940c223db37d4d9fe9ab53f19b14a2249d4b3e7398e`.

`python3 scripts/check_fivecircles.py` PASS, `git diff --check` PASS. Git ignore에서 uploads/outputs/.env 제외를 확인했다. 유료 이미지 호출·원격 프롬프트 게시·W&B 업로드·커밋·푸시는 하지 않았다. 소스 검증과 실제 광고 품질·팀 계정/청구·서비스 인수는 구분한다. 새 실행 형식의 W&B 수집 연결은 별도 확인 대상이다.

최종 상태: 네 단계 COMPLETED. BLOCKED/FAILED/SKIPPED 단계 없음. 사용자 지시에 따른 실패 후 재시도는 발생하지 않았다.
