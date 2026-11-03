# 생성당 추정 비교 · 2026-11-03 KST

## 실제 W&B summary null 표현
- report_id e63b12dafbb37a9bd386494de17c54b4a5d57978028bbc12d060bb899e64b216. 첫 저장 보고서 업로드 후 run finished이나 readback SUMMARY_MISMATCH.
- 원격 대조: 모든 expected-null summary key가 생략됨. 알려진 생성 지표는 일치함. 0.0→0 표현은 기존 숫자 비교로 동등함.
- 수리: summary에서만 expected-null 누락을 null로 읽는 codec 옵션. 알려진 값 누락/변경·null→0은 실패. config/artifact/Table/원본 해시는 strict 유지.
- 가짜 SDK에도 실제 null 생략을 재현하여168 tests PASS. 보고서/이미지 재생성 없이 기존 run을 다시 읽어 READBACK_VERIFIED, deduplicated=true 확인. 최종전체2030 PASS.
