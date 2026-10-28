# 2026-10-28 검증 근거

자료 확인 기준: 2026-10-28 18:27 KST. 완료는 모델 모듈·오프라인 시험·Git 게시 범위다. 서비스 공동 인수와 실제품 품질은 미검증이다.

- [구현 체크 고정 원본](https://github.com/uyt5041-lab/beauty-ai-models/blob/3480c34282c4ac0c6bedb1369eb0c2c6247cf34c/docs/imageTodo.md): 202개, 실제 체크 70개, 보고일 체크 63개(31.2%).
- [팀 공유 마일스톤](https://github.com/uyt5041-lab/beauty-ai-models/blob/a5f5095/docs/imageTodo.md): 오늘까지 7개 중 완료 3개·부분 완료 4개. 10/28 부분 완료.
- [A1~A3 기존 전체 시험](https://github.com/uyt5041-lab/beauty-ai-models/blob/93faaa848bf5f9caf2e4d361017b662a5a69a679/fivecircles/test/vlm-ko-20261028.md): 1,394 PASS는 이전 전체 구현 회귀 기록이다. 이번 일지 작성 중 재실행하지 않았으며 팀 MVP 301개와 합산하지 않는다.
- [팀 MVP 최초 분리](https://github.com/taemin050-sys/project-it-tem/commit/a85af03016c856dd254762b3e0a5993c0f32e9ab).
- [성공/error 모순 차단](https://github.com/taemin050-sys/project-it-tem/commit/db1bc6e161f431209ab6b34f7499d2ee2c30473d).
- [한글 합성](https://github.com/taemin050-sys/project-it-tem/commit/d99dee0d056e1fc91ab57d1b3817cb9e1dd27f03): 도형 제품 fixture로 검사. 실제품 품질 인수 아님.
- [두 프롬프트 경로와 revision 호환](https://github.com/taemin050-sys/project-it-tem/commit/a4c6c23b3de49cc4585d21b6df3b57d605966048).
- [서빙 요청문 게시](https://github.com/taemin050-sys/project-it-tem/commit/fbb7cb0ad9fc7733a1e252fd59a683cb71ecea42): #4~9의 검토 커밋은 본문에 고정. 이후 변경은 미확인.
- [301개 테스트 게시](https://github.com/taemin050-sys/project-it-tem/commit/65c2e84bb3a408a8bca123528b3ad3305b55c677): 3.11에서 301 passed in 3.44s; 별도 체크아웃·새 3.14에서 301 passed in 3.55s. skip 0, 외부 네트워크 차단. 같은 테스트를 두 환경에서 실행했으며 테스트 수는 301개다. 실행 로그의 마지막 결과를 재확인했다.

## 실행 방법

잇템 feat/image-generation 루트에서 실행한다. API 키는 필요하지 않다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r tests/image_generation/requirements.txt
python -m pytest -q -c tests/image_generation/pytest.ini tests/image_generation
```

## 집계와 날짜

[일정 매핑](schedule-map.json)은 이전 일지의 5개 묶음 계획일을 유지하고, 날짜별 마일스톤에는 각 항목의 날짜를 적용했다. A1~A3은 제목의 보충 문구 10/27 대신 실제 묶음 날짜 2026-10-28을 명시했다.

지난 193개에서 A1~A3 3개와 C1~C6 6개가 추가돼 202개가 됐다. 지난 일지 48개 대비 이번 분자는 A1~A3 3개, C1~C6 6개, 기존 단계 2-D 중 보고 범위에 들어온 3개, 공유 계획에서 동기화한 과거 마일스톤 체크 3개가 늘어 63개다. 증가한 15개 전부를 오늘 새 구현으로 해석하지 않는다.

일정 미정 API 세부 체크 5개와 자체호스팅 제외 2개는 실제 체크 이력을 보존하되 일지 분자에서 제외했다. 미래 계획과 미정 항목에 일지 체크가 없음을 검사한다. API 묶음 0/12·SSOT 묶음 0/12는 별도 설명용이며 전체 분모에 더하지 않는다.

[전체 목록](full-todo.md) · [진행률 데이터](progress.json) · [협업일지](park-jisoo-worklog.md)
