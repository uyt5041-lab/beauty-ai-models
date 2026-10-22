# B4 검증 · PASS_WITH_RISKS

2026-10-22 KST · [구현 계약](../architecture/contract/image-runtime.md).

- root 실행 근거: `.venv/bin/python -m pytest -q -W error` → **201 passed in 20.04s**.
- 실제 TCP CLI: prepare → approve → select revision1 → render → attach → 문구 revision만 변경한 select revision2 → get-edit PASS. 같은 job ID를 재사용했고 실제 모델을 호출하지 않았다.
- 프롬프트 수신: 텍스트·버전·source revision을 hash에 포함. None은 기존 body·golden hash·queued DB snapshot과 호환. 문자 수와 공백 입력·strict integer 검증, 실제 tokenizer/언어 판별은 별도. local template은 prompt를 무시하지 않고 거절.
- 편집: SQLite CAS로 같은 revision의 동시 편집 하나만 허용, copy revision 역행 차단. 현재 project/request hash와 succeeded BackgroundResult·레이어·실파일을 확인한 뒤 attach. 늦은 revision·다른 입력·실패 작업은 최신 선택을 덮지 않음.
- 캐시: project별 key, 실제 파일 검증 뒤 재사용. 사라지거나 변조된 결과는 miss로 제거하며 저장소 장애는 miss로 숨기지 않음. 자동 GPU 재생성 없음. GET selection은 저장 메타데이터이며 내보내기/파일 조회 검증을 대체하지 않음.
- 검토 반영: SQLite 범위를 넘는 revision·bool/실수·문자열 revision, 공백 프롬프트를 거절하도록 강화한 뒤 최종 회귀. 런타임 실패는 보고되지 않음.
- [목표/현재 흐름](../architecture/spec/image-runtime-flow.html): **브라우저 검증 PASS** (Task Space 139). PNG 2개 decode(552×2540, 1568×3316), 제목 7개·범례 5개·GAP 13행·우선순위 6행, 문서/화면 폭 1492px로 가로 넘침 없음. [스크린샷](playwright-screenshots/image-runtime-flow-b4.png)을 직접 확인했다. 스크린샷은 최종 201 tests 결과를 반영하기 전에 촬영했다.
- 전체 목표 판정: **GAP/PARTIAL**. 최종 사용자 로그인·CampaignService·UI·렌더러·실제 GPU/모델·tokenizer·사람 품질·팀 인수는 미검증.
- 다음 안전 단위: B5 고정 평가 manifest와 증거 수집 도구. 평가 기준/사람 판정이 없는 경우 NOT_ASSESSED로 남기며 실제 모델 합격으로 표시하지 않는다.
- publication은 Git 코드 전달. 운영 CD NOT_APPLICABLE (로컬 Python 내부 API 참조 구현), root가 push와 원격 SHA를 별도 기록한다.
