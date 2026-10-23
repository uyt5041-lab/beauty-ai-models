# Git 반영 기록

## 2026-10-22 · B9 및 SSOT 문서 publication

- source SHA: `858c6f7709074b230bdb82192aa529583936fc5c`. 게시 직전 원격 B8과 `0 0` 확인 후 일반 push했다. `git ls-remote --heads origin plan/models`와 소스 SHA가 일치했고 작업 폴더는 clean이었다. PR·협업일지 병합·force push 없음.
- 검증: REPO 전체624 tests36.32s, 실제 MPS 누끼1+배경5·동일512 두 번 request/output hash 일치, source hash6개 일치, wheel·격리 lazy import·pip check·FiveCircles·diff·공개 파일 검사 PASS. 최종 wheel SHA-256 `fa2629ec7fd4e4c8a3e8002450424350ee12777aeb359ac364a14535eb015450`.
- 로컬 beautyAi: 기존 B8 파일과 충돌 여부 확인, 변경34파일 백업 후 tracked152파일 byte parity PASS. 백업 `outputs/relay-backup-b9-ssot-20261022-172111`, 별도 receipt 보존. 로컬 독립 환경 전체624 tests33.73s와 설정 검사 PASS. 비밀값·가중치·생성 이미지·캐시는 공개 Git에 포함하지 않았다.
- SSOT 대조·실행 TODO 문서 전달 완료. 기존 완료35개에 날짜·근거를 붙이고 신규12개는 담당·의존·완료기준과 함께 미완료로 유지했다. 문서 전달은 팀원 수락·노션 동기화·실제 기능 인수를 뜻하지 않는다.
- 소스 publication 완료와 별개로 최종 HTML 브라우저 검증은 BLOCKED다. 자동승인검토가 Chrome CUA의 `file:` 접근을 거절했고 우회하지 않았다. 현재 PNG 직접 검사·HTML 정적 검증 완료, 수정 전 screenshot은 최종판 증거가 아니다. Flow diagram verification: GAP/PARTIAL. 후속 코드 릴레이는 STOP_WITH_REASON이며 가장 구체적인 로컬 후보는 실패 뒤 다음 job 복구·부분 결과 자산 처리다.
- 운영 CD NOT_APPLICABLE: 로컬 Python 참조 API·CLI와 문서의 코드 전달이며 승인된 운영 배포 대상이 없다. 로컬 실제 모델 실행은 확인했고 GCP/CUDA 실행은 NOT_RUN, 실사진 광고 품질·최종 한글 렌더러·팀 인수는 미완료다.

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

## 2026-10-22 · B8 publication

- source SHA: `1e05f945a202ac9e53fccb4d6c7a2b721f5fcb10`. FF 확인 후 일반 push, 원격 plan/models SHA 일치.
- 전체521 tests15.01s·실제TCP 응답유실4건·단일PUT/processor·동일ID복구·기한상태구분·오류코드수리·wheel/lazy import·flow/browser PASS.
- 로컬 beautyAi145 tracked파일 동일 반영,21파일변경/추가 전 백업. 로컬521 tests68.92s/설정검사PASS. 모델 코드는 바뀌지 않아 추가 GPU 실행은 하지 않았다.
- 로컬 클라이언트 publication이며 운영배포 NOT_APPLICABLE. 운영프록시/GPU장애·광고품질·팀인수로 확대하지 않는다.

## 2026-10-22 · B10 게시 준비

- 기준 원격 `64124ed119dc6cf96a4e211e6b96d2d62e26060a`를 ff-only로 수신하고 기존 스키마 초안을 보존했다. 게시 직전 HEAD와 origin/plan/models는0/0.
- 전체806 tests51.05s, 로컬신규182 tests3.68s, mock CLI3건/실제제공자0회, wheel·격리import·pip check·schema4개·설정·diff·비밀패턴 PASS.
- 로컬공개파일192개동기화, 기존26개백업·66개갱신·충돌0·byte parity PASS. 백업은 Git 제외 outputs/relay-backup-b10-20261022에 보존했다.
- PR·협업일지브랜치·force push·운영배포 없이 plan/models에 코드와문서를전달한다. 실제소스SHA·원격readback은 게시후receipt에기록한다.

## 2026-10-22 · B10 source publication receipt

- source SHA: `fb7305bfb9acc123a71092ab2bc715b402f64efa`. 일반 push `64124ed..fb7305b`, `git ls-remote --heads origin plan/models`의 SHA가 일치했다. source 게시 후 작업 폴더 clean 확인.
- 공개 tracked192파일과 로컬 beautyAi byte parity PASS. 독립환경 신규182 tests3.68s, 전체 REPO806 tests51.05s, 모의CLI실제제공자0회. 비밀값·모델·생성물 제외.
- Git 코드 전달 완료. 저장소에 CI workflow 없음. 운영 CD NOT_APPLICABLE: 기본 외부호출 차단 Python 래퍼와 문서 전달이며 배포대상/요청 없음.
- 최종 HTML 브라우저 검증은 기존BLOCKED. PNG 직접/HTML정적 검증과 소스게시를 구분한다. 실계정·실품질·잔액·실제비용은 NOT_RUN.
- 이 receipt와 TODO 재검토 결정은 별도 문서 커밋으로 같은 브랜치에 게시하며 source SHA는 위 값을 유지한다. PR·협업일지브랜치작업·force push 없음.

## 2026-10-23 · B11 화장품 프롬프트 전달

- 소스 커밋: `aa54a6ed0ccb80465eade1963c931c2fa6743714`. `plan/models` 일반 push 성공 후 원격 `refs/heads/plan/models` SHA 일치를 확인했다. 강제 push/PR/협업일지 변경 없음.
- 전체866 tests(13.90s), 로컬beautyAi 신규60 tests(0.38s), 설정/링크/diff 검사 PASS. [검증](../test/cosmetics-prompts-b11.md).
- 로컬beautyAi에 같은 파일을 동기화했다. 프리뷰CLI는 프롬프트/요청템플릿만 출력했고 실제API0회·생성이미지0이다. 꽃은 필수연출에서 제거했으며 금지하지 않는다.
- CI/CD: NOT_RUN. 이 전달은 Python라이브러리·프리셋·문서와 로컬 오프라인CLI 범위이며 서비스배포/팀인수가 아니다. 실제품 품질과 서비스 연결은 남아 있다.
