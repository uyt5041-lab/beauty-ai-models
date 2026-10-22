# Git 반영 기록

## 2026-10-22 · BEAUTY-FC-001

- 상태: LOCAL_ONLY. 로컬 beautyAi와 plan/models 작업 폴더에 구성.
- 이번 프레임워크 커밋/푸시/PR: 수행하지 않음.
- 기존 TODO 원격 반영 이력과 이번 프레임워크 설정 상태는 구분한다.
- 반영 요청 시 [Git 규칙](../agent/git.md)에 따라 fetch/FF/선택 커밋/push/원격 SHA 확인 후 별도 결과를 남긴다.

## 2026-10-22 · B1 / framework publication

- 사용자 릴레이샷 실행 요청에 따라 기존 로컬 framework와 B1을 `plan/models`에 통합.
- source SHA: `1bc4fea80ffb29094c676689d8a4b147a7ec5666`.
- 일반 push: `00e455e..1bc4fea`; `git ls-remote --heads origin plan/models`와 SHA 일치 확인.
- 검증: 72 tests, CLI, 설정 검사, staged diff 검사, 보고서 브라우저 렌더.
- 운영 CD: NOT_APPLICABLE (로컬 라이브러리/CLI). 실 GPU/모델 서비스 배포 완료를 뜻하지 않음.

## 2026-10-22 · B2 publication

- source SHA: `8cb64029d79ea42a6b9c4aefeaee448656bea41a`. 일반 push 후 원격 plan/models SHA 일치.
- 검증: 전체112 tests PASS, health 추가 API8 PASS, 실제 TCP loopback/SQLite/프로세스 복구 PASS, staged diff/비밀패턴 검사 PASS.
- publication: Git 코드 전달. 운영 CD NOT_APPLICABLE (로컬 내부 API 참조 구현, 배포 대상 미설정). GPU 서비스 배포·실모델 품질은 미수행.

## 2026-10-22 · B3 publication

- source SHA: `dbba5da7859f8d23e62d895be39617b1e2e38e3b`. 일반 push 후 원격 plan/models 일치.
- 148 tests, 실제 TCP CLI, wheel/isolated import, browser 보고서, FiveCircles/staged diff/비밀 패턴 검사 PASS.
- 로컬 template runtime 코드 전달. 운영 CD NOT_APPLICABLE. GPU·실모델·사람 품질 승인과 구분한다.

## 2026-10-22 · B4 publication

- source SHA: `a1b1602f52834191dca6730600c6ac475c106b00`. 일반push 후 원격 plan/models 일치.
- 전체201 tests와 실제TCP CLI 문구수정/동일job재사용 PASS. legacyhash/DB호환, CAS경쟁, 늦은결과, 타project, 파일손상/장애 검증.
- 코드publication이며 운영CD NOT_APPLICABLE. 실제모델/사용자UI/팀인수는미검증.

## 2026-10-22 · B5 publication

- source SHA: `c0c1240508c253872ee8cc480e2bf19d256ccaa2`. 일반push 후 원격 plan/models SHA일치.
- 전체280 tests4.06s, 평가예제CLI, 실제TCP timingCLI, wheel/isolatedimport, HTML/PNG/browser, 설정/비밀패턴/diff 검사 PASS.
- 로컬 `/Users/pio/IdeaProjects/beautyAi`에 공개tracked125파일을동일하게반영했다. 기존파일백업후72파일갱신, Git생성/비밀값/생성이미지복사없음. 로컬설정검사PASS.
- 오프라인평가/로컬참조API 코드publication. 운영CD NOT_APPLICABLE. 모델호출·품질승인은미수행.

## 2026-10-22 · B6 publication

- source SHA: `a7f3957664eeb98ef76d2352c566c47f2a733ced`. 일반push와원격plan/models일치.
- 코드339tests4.30s, 실제MPS/TCPCLI BiRefNetLite합성fixture, 해시검증/평가연결, wheel/isolatedlazyimport, flow/browserPASS.
- 로컬beautyAi132tracked파일일치, 별도Python3.11가상환경설치. 로컬339tests8.86s/설정/가중치검증PASS. 가중치는models/,실행결과는outputs/로Git제외.
- 로컬실누끼실행증거이며GCP배포NOT_RUN. 실제제품사진품질/배경AI/팀인수는미검증.

## 2026-10-22 · B7 publication

- source SHA: `b71fadcaf22bebd67c9089c67d452e449d3596ea`. FF 확인 후 일반 push, 원격 plan/models SHA 일치.
- 전체433 tests9.39s, 실제 MPS 누끼·SDXL/TCP CLI512×512, 결과 파일/hash·제품770픽셀·Safe Zone, 최종wheel/lazy import·flow/browser PASS.
- 로컬 beautyAi141 tracked파일 동일 반영, 기존30파일 변경/추가 전 백업. 독립 로컬 환경433 tests19.17s·pip check·두 모델 파일 검증 PASS. 모델/결과/캐시는 Git 제외.
- 코드 publication과 로컬 실행 증거다. GCP 배포 NOT_RUN. 광고 품질·실사진·팀 인수는 미검증이며 꽃 표현 부족·제품 잔상을 기록했다.
