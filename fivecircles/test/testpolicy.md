# BeautyAI 검증 정책

## 문서/프레임워크

저장소 루트에서 `python3 scripts/check_fivecircles.py`를 실행한다. 경로·JSON·라이선스·원본 해시·프로젝트 참조·금지된 이전 프로젝트 설정 잔존을 검사한다.
기존 코드/스키마/TODO와 README 보존, 두 설치 폴더 일치, `git diff --check`를 확인한다.
문서 변경만으로 실모델 테스트가 통과했다고 쓰지 않는다.

## Python 래퍼

Python 3.11 이상. 프로젝트 전용 가상환경을 사용한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q -W error
```

코드/의존성/스키마가 바뀌면 관련 테스트를 수행한다. 현재 `tests/test_wrappers.py`는 SDK/HTTP 모킹과 Pillow 검증이다. 실제 API 호출·모델 품질·GPU 성능을 증명하지 않는다.
설정 검사에는 Python 표준 라이브러리만 필요하다. pytest는 test 의존성 설치 후 실행한다.

## M1/M2 실제 통합

[이미지 TODO](../../docs/imageTodo.md)의 기준대로 실제 모델·샘플·seed·버전·지연/VRAM·결과를 기록한다.
정상 요청, 실패/미확인 분석, 긴 프롬프트, 문구만 수정, 이미지 재생성, 응답 유실, 늦은 결과를 확인한다.
이 호출은 비용/자산/서버 조건이 갖춰진 담당 작업에서 수행한다. 프레임워크 초기화가 자동 호출을 시작하지 않는다.

## 결과

PASS / FAIL / BLOCKED / NOT_RUN을 구분하고 명령·범위·시각·근거를 남긴다.
실패는 [errorlogs](errorlogs/README.md), 해결은 [learn-from-log](learn-from-log.md), 요약은 [work/update](../work/update.md)에 기록한다.
