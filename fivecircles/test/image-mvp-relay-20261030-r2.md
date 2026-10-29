# R2 A 템플릿 검증 · 2026-10-30

판정: PASS_WITH_RISKS(이미지 모듈); 실제 팀 카피·생성 준수·서빙 UI 인수 별도.

- 전체 이미지 회귀 333 PASS. 최종 게이트에는 A 서브라인의 명시적 한글 두 줄 배치 검증을 포함했다.
- 기존 81개 어댑터/A/정합성 targeted PASS 후 최종 전체 suite 실행.
- 수정 전 b9d0015에서 실제 추출한 safe_zone v1/v2 binding을 그대로 재사용했다.
- 기존 요청은 새 prompt builder/asset upload를 호출하지 않는다. 입력 변경은 충돌로 거절했다.
- 새 safe_zone 요청만 prompt version v3. no-zone 입력 fingerprint와 기존 동작은 유지했다.
- 상단 safe_zone (0.06,0.05,0.94,0.25); top0.04 초안을 기존5% 계약에 맞춰 교정했다.
- A headline/subline/CTA 중앙 정렬, 최소48/45/48px. 겹친 제품 영역과 넘치는 원문을 거절한다.
- 예시 실제 출력1080×1350, 한글 중앙 정렬·분리된 CTA를 시각 확인했다.
- 합성 사진의 제품 보호 영역 픽셀이 리사이즈 기준 이미지와 일치한다. 실제 제품 원형 보존 평가가 아니다.
- MockTransport 생성1회 후 재사용·합성; 실제 API0. 합성 receipt provider_calls0.
- 캡션/태그 UTF-8, image/copy hash와 revision 보존. needs_manual_check=True 유지.

예시 PNG/캡션은 작업 루트 reports/image-mvp-relay-20261030/r2/에만 두었다.
원본·실제 생성 광고·런타임 DB를 코드 브랜치에 넣지 않았다.
[JSON 영수증](image-mvp-relay-20261030-r2.json).

## 인수 제한

상단 지시는 마스크가 아니고 CTA 배경까지 예약하지 않는다.
실제 팀원 AdCopyOutput JSON이 없어 실제 팀 카피의 줄바꿈·대비·제품 침범 검증은 NOT_RUN이다.
모듈 helper를 추가한 것으로 서빙 UI에서 A가 선택·연결됐다고 주장하지 않는다.

R2 코드 게시: 86de3ece0b5fb14463ae787b8c4eb0e7a515a99d, feat/image-generation 원격 SHA 일치.
