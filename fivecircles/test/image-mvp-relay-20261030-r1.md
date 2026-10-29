# R1 · MPO 입력과 저장 호환 검증

2026-10-30 · 결과 **PASS_WITH_RISKS**

## 변경

- MPO 업로드에서 대표 프레임0을 선택한 뒤 기존 정규화로 연결했다.
- 일반 애니메이션과 put_image의 다중 프레임 차단을 유지했다.
- Windows directory fsync 불지원만 분리했다. 파일 fsync·닫기·원자 교체·DB commit과 오류 처리를 유지했다.
- AssetRef·텍스트 입력·서빙 HTTP 계약은 변경하지 않았다.

## 실제 검증

| 항목 | 결과 |
|---|---|
| 기준선 | 301 PASS / 3.64s |
| 자산 표적 회귀 | 36 PASS / 0.27s |
| 전체 이미지 모듈 | 318 PASS / 3.50s |
| 실제 제품 MPO2개 | frame0, EXIF/ICC, pixel match, metadata 제거, SQLite 재조회 PASS |
| 독립 Linux | 실제 directory fsync2/file fsync1, 재조회·격리·알파·제한·실패처리 등10검사 PASS |
| Windows 모의 분기 | 파일/디렉터리 분리 및 permission/DB 오류 유지 PASS |
| Windows 실제 runtime | NOT_RUN |
| 실제 이미지 API | 0회 |

명령: `/tmp/beauty-mvp-venv-20261028/bin/python -m pytest -c tests/image_generation/pytest.ini tests/image_generation -q`.
코드 기준은 팀 feat/image-generation에서 검증한 후보이며 assets.py SHA256은 `adc79140bfcac5c49b3c9dad23b45de48ca7728166de1edca2a33fb67e4cb25c`다.
환경: macOS Python3.11.15/Pillow13.0.0; Linux Python3.12.14/Pillow12.3.0. Linux 검증은 자산 component smoke이며 전체318 테스트 실행이 아니다.

## 한계와 후속

두 실제 MPO는 보유한 제품 사진이며 팀원 실행의 동일 원본인지는 확인하지 않았다.
첫 프레임이 모든 장치의 최적 대표 사진이라는 보장은 하지 않는다.
제품 생성 품질, safe_zone 생성 준수, 실제 사용자 승인/다운로드는 별도 인수다.
원본 사진·runtime DB·키는 Git에 포함하지 않는다.

[기계 판독 근거](image-mvp-relay-20261030-r1.json) · [실행 원장](../work/image-mvp-relay-20261030.md)


## R1 전달 및 시각 검증

코드 b9d00152de352499ce7773ac398fe8efb5408e8e를 feat/image-generation에 일반 push하고 원격 SHA 일치를 확인했다.
목표/현재 Mermaid PNG2개와 HTML을 만들고 ego-browser TaskSpace23에서 이미지·제목·표 렌더를 검증했다.
318 tests / 실제 MPO2 / Linux10 PASS; Windows 실제 NOT_RUN. 실제 서비스 UI 검증 또는 새 이미지 생성은 하지 않았다.
다음 릴레이는 R2 A 템플릿과 safe_zone 연결이다.
