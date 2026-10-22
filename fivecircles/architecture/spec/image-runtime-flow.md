# 이미지 런타임 흐름 비교 · B7

2026-10-22 · 대상: plan/models · 배치 B7 고정 SDXL / 로컬 배경 adapter

**Flow diagram verification: GAP/PARTIAL.** 고정 SDXL을 실제 MPS에서 실행하고 512×512 fixture의 승인 제품 픽셀·텍스트 여백·3개 레이어 파일을 검증했다. 전체 433 tests와 실제 실행을 구분해 기록했다. 꽃 표현 부족과 제품 잔상이 관찰돼 광고 품질·안전은 NOT_ASSESSED다. 실사진·반복/크기별 측정·CUDA/GCP·렌더러·팀 인수는 남아 있다.

## Target Flow

의도한 최종 흐름이다. 파란 모델 노드는 계획이며 현재 모델 실행 증거가 아니다.

```mermaid
flowchart TB
    A["A · 자산 검증 및 저장<br/>project · decode · hash · metadata"]:::green
    B["B · 영속 작업 API / ledger<br/>atomic PUT · ID/hash · 복구"]:::green
    C["C · 실제 누끼 모델<br/>고정 profile / revision"]:::blue
    D["D · 제품 캔버스 / 보호 마스크<br/>비율 · 영역 · 동일 해상도"]:::green
    E["E · 제품 레이어 승인<br/>현재 자산 / revision 확인"]:::amber
    F["F · 실제 배경 모델<br/>프롬프트 / 버전 · seed"]:::blue
    G["G · 제품 재합성 / 결과 저장<br/>불투명 pixel 검사 · 파일 확인"]:::green
    H["H · 편집 revision / 캐시 경계<br/>프로젝트 권한 · 늦은 결과 차단"]:::amber
    I["I · 렌더러 / 내보내기 가드<br/>문구 검수 · 이미지 승인 일치"]:::green
    J["J · 사람 품질 평가 / 팀 인수<br/>광고 품질 · 재현 · 최종 확인"]:::amber
    A --> B --> C --> D --> E --> F --> G --> H --> I --> J
classDef green fill:#e8f2ea,stroke:#bdd1c2,color:#1f2933,stroke-width:1.5px
classDef blue fill:#e8f0f7,stroke:#c2d3e0,color:#1f2933,stroke-width:1.5px
classDef amber fill:#fff4dc,stroke:#e7c88f,color:#1f2933,stroke-width:1.5px
classDef red fill:#fde9e6,stroke:#e8bab5,color:#1f2933,stroke-width:1.5px
classDef gray fill:#eef1ed,stroke:#d8e0da,color:#1f2933,stroke-width:1.5px
```

![목표 흐름](image-runtime-target-flow.png)

[Mermaid 원본](image-runtime-target-flow.mmd)

## Current Implementation Flow

실선은 현재 실행 가능한 로컬 연결, 점선은 미연결 경로다. 회색 `uploaded-alpha`와 `bright-template`는 실제 누끼·배경 AI 모델이 아니다. 승인 레코드는 인증된 호스트의 확인 전달을 저장하며 사람 신원·최종 품질을 인증하지 않는다.

```mermaid
flowchart TB
    A["A · 자산 검증 / 저장<br/>LocalAssetStore · 파일 / DB"]:::green
    B["B · 인증 internal API / WAL ledger<br/>project scope · atomic ID/hash"]:::green
    W["단일 호스트 worker<br/>POSIX 실행 잠금 · queued 복구"]:::green
    U["uploaded-alpha / 1<br/>이미 투명한 RGBA 입력만 처리"]:::gray
    C["C · BiRefNetLite 실제 누끼<br/>고정 revision / MPS fixture 검증"]:::green
    D["D · 제품 후보 캔버스 / 마스크<br/>종횡비 / 실제 alpha 영역 검사"]:::green
    E["E · 명시적 승인 저장<br/>정확한 source / layer / visual hash"]:::green
    P["서버 소유 profile / revision<br/>승인 / 자산을 실행 직전 재검증"]:::green
    T["bright-template / 1<br/>밝은 로컬 템플릿 · AI 모델 아님"]:::gray
    F["F · SDXL 실제 배경 / 고정 profile<br/>512 MPS fixture / 품질 미평가"]:::green
    G["G · 제품 재합성 / 3개 레이어 저장<br/>불투명 픽셀 / 실제 파일 검사"]:::green
    R["결과 파일 / project 확인<br/>성공 commit · 안전한 실패 코드"]:::green
    H["H · 편집 revision CAS / 프로젝트 캐시<br/>실파일 hit 검증 / 늦은 attach 거절"]:::green
    I["I · GAP: 렌더러 / 내보내기<br/>실서비스 연결 없음"]:::red
    J["J · GAP: 사람 평가 / 팀 인수<br/>실제 광고 품질 증거 필요"]:::red
    Q["영어 프롬프트 수신 / hash<br/>SDXL 두 tokenizer · 각 77 token 검사"]:::green
    X["local template은 prompt 미지원<br/>무시하지 않고 명시적 거절"]:::gray
    V["별도 로컬 평가 입력<br/>버전 / 권리 선언 / 정답 mask"]:::gray
    K["평가 manifest CLI / mask·픽셀 지표<br/>권리 미확인 건너뜀 / 품질 NOT_ASSESSED"]:::green
    L["JobTiming API / CLI<br/>접수→terminal wall-clock / GPU 시간 아님"]:::green
    V --> K -.-> J
    R --> L
    Q --> X
    Q --> F
    A --> B --> W --> U --> D --> E --> P --> T --> G --> R
    S["명시적 모델 준비 / 검증<br/>고정 4개 파일 hash / 로컬 로딩"]:::green
    N["GAP: 실사진 품질 / CUDA<br/>합성 fixture 성공과 별도 검증"]:::red
    S --> C
    W --> C --> D
    C -.-> N
    S2["SDXL 고정 fp16 파일 준비<br/>18 runtime + card / license · SHA 검증"]:::green
    S2 --> F
    P --> F --> G
    R --> H -.-> I -.-> J
classDef green fill:#e8f2ea,stroke:#bdd1c2,color:#1f2933,stroke-width:1.5px
classDef blue fill:#e8f0f7,stroke:#c2d3e0,color:#1f2933,stroke-width:1.5px
classDef amber fill:#fff4dc,stroke:#e7c88f,color:#1f2933,stroke-width:1.5px
classDef red fill:#fde9e6,stroke:#e8bab5,color:#1f2933,stroke-width:1.5px
classDef gray fill:#eef1ed,stroke:#d8e0da,color:#1f2933,stroke-width:1.5px
```

![현재 흐름](image-runtime-current-flow.png)

[Mermaid 원본](image-runtime-current-flow.mmd)

## Target vs Current Gap

| 노드 / 연결 | 상태 | 확인한 차이 | 다음 근거 / 작업 |
| --- | --- | --- | --- |
| A | MATCHED (모듈) | 파일 decode/정규화/hash/프로젝트 경계와 저장 구현. 최종 사용자 로그인은 별도. | assets.py / 자산 테스트 |
| A → B / B | MATCHED (로컬 서버) | Bearer→project 매핑, atomic PUT, 동일 ID/hash 재사용, WAL ledger와 조회 API. | inference_api.py / job_ledger.py |
| B → worker | MATCHED (로컬 실행 기반) | POSIX 잠금, DB queued 검색, owner 검증, 실제 파일 확인 후 success. 단일 호스트 범위. | worker·loopback·프로세스 복구 테스트 |
| 모델 준비 → C / F | MATCHED (실제 파일 검증) | BiRefNet 4개와 SDXL 18개 fp16 런타임+카드/라이선스의 크기·SHA 검증. 명시 prepare만 다운로드하고 API 다운로드·원격 코드·장치 자동 대체 없음. | 실제 verify / 고정 model manifest |
| worker → C / C | PARTIAL (실제 MPS 실행) | BiRefNetLite가 실제 MPS에서 합성 병 fixture를 처리. 원본 RGB 유지, alpha 0..255, RGBA·마스크 저장과 수동 확인 경고. 실사진 품질·CUDA는 별도. | 실제 TCP CLI prepare / 339 tests / B6 실행 기록 |
| C → D / D | MATCHED (로컬 실행) | 실제 누끼 결과를 후보 캔버스·마스크로 준비하고 approved=false로 저장. 사람 확인·품질 통과는 별도. | 실제 MPS job → prepare API → 후보 파일 |
| D → E / E | MATCHED (저장·검증 범위) | 처음에는 미승인. literal true와 candidate hash를 확인하고 project/실파일/마스크/영역을 검증. 사람 신원·품질 승인은 보증하지 않음. | ProductApprovalStore / 승인 API |
| E → profile → template → G | MATCHED (로컬 템플릿 경로) | 고정 ID/revision/style/layout/크기만 허용. 승인·자산을 실행 직전 확인하고 3개 레이어를 저장. 실제 AI 모델 대체 증거는 아님. | LocalTemplateProcessor / runtime / CLI |
| 프롬프트 수신 → F | MATCHED (고정 SDXL 경로) | ImagePrompt 필수, positive/negative를 두 CLIP tokenizer로 각 77 이하 확인. 특수 token 포함, 무음 절단 없음. 기존 None hash 호환·template prompt 거절은 유지. | SDXL adapter / tokenizer 경계 회귀 |
| E → F / F | PARTIAL (실제 512 MPS 실행) | 승인·파일·geometry 재검증, SDXL 고정 profile·seed·steps20·guidance7.5·strength0.99. 꽃 예제는 호스트 작성 prompt. | 실제 TCP/MPS receipt / 합성 fixture의 시험 확인 |
| F → G / G | MATCHED (512 fixture 기술 검증) | 생성 후 Safe Zone 중립색 복원·승인 RGBA 재합성·불투명 픽셀·3개 레이어 검증. 원출력 전부0·NaN/Inf·형상/범위 오류 거절. 안전/품질 경고 유지. | opaque 770 / mismatch 0 / Safe Zone exact / 3개 hash |
| G → H / H | MATCHED (로컬 API/DB/CLI) | SQLite CAS로 현재 selection을 갱신. 성공 job의 입력 hash/project/실파일을 확인한 뒤 attach. 문구 revision만 바뀌면 같은 image key의 결과 재사용. 늦은 revision은 거절하며 자동 생성하지 않음. | image_edits.py / API·CLI·201 tests PASS |
| H → I / I | GAP | 렌더러·내보내기 정책과 현재 문구 검수/이미지 품질/레이아웃 대조가 미연결. | 서비스·렌더러 공동 검증 |
| 별도 파일 → 평가 도구 | MATCHED (로컬 도구) | 권리 선언·dataset/version·dev/holdout을 수신하고 실제 파일 hash·불투명 픽셀·mask IoU·경계를 측정. 권리 미확인은 건너뛰고 정답이 없으면 None. 실제 평가셋·품질 승인은 별도. | evaluation.py / evaluation_metrics.py / CLI |
| ledger → timing | MATCHED (로컬 API/CLI) | terminal 작업의 접수→종료 wall-clock 경과를 기록. 대기·처리·저장을 포함하며 GPU 연산 시간·VRAM·비용은 아님. queued/running·시계 역행은 None. | JobLedger.timing / 인증 API / get-timing |
| I → J / J | GAP | 사람 품질 평가·팀원 재현·최종 인수 미실행. 측정 도구의 quality_approval은 NOT_ASSESSED이며 점수만으로 합격하지 않음. | 권리 확보 평가셋·합의 기준·사람 판정·팀 인수 |

## Done / Not Done Priority

| 순서 | 작업 | 상태 | 연결 점수 | 선정 이유 | 다음 동작 |
| --- | --- | --- | --- | --- | --- |
| 1 | B8 후보 · 동일 job ID 제출 조회·조정 | 부분 구현 / 후속 후보 | 3 + 3 + 2 + 2 − 1 = 9 | 상류 제출 응답 유실에서 중복 생성·잘못된 상태 판단을 방지 | 기존 ID/hash의 bounded 조회·조정 계약 후 검증 |
| 2 | 로컬 반복/크기 측정·단계별 trace | 미구현 / 로컬 진행 가능 | 1 + 2 + 2 + 2 − 1 = 6 | 실제 모델의 cold/warm·처리 단계·메모리 차이를 기록 | 제출 경계 다음 측정 계약과 반복 실험 |
| 3 | 실사진 품질 / GCP CUDA / 렌더러·팀 인수 | GAP / 공동 준비 필요 | 2 + 2 + 1 + 1 − 2 = 4 | 제품 유형·품질 기준·실서비스 결과 확인 | 권리 확보 데이터·환경·담당과 병행 |
| 완료 | B7 · SDXL fp16 / inpaint / prompt guard | MATCHED (512 fixture 기술 범위) | 선정 대상 제외 | 실제 배경 경로와 승인 제품·텍스트 여백 연결 | 433 tests·실제 MPS·wheel PASS / 품질 미평가 |
| 완료 | B6 · 고정 실누끼 / MPS / 모델 저장 | MATCHED (합성 fixture 범위) | 선정 대상 제외 | 고정 파일부터 실제 누끼·미승인 후보까지 연결 | 339 tests + 실제 MPS/TCP PASS |
| 완료 | B5 · 평가 manifest / 지표 / timing | MATCHED (로컬 도구) | 선정 대상 제외 | UNKNOWN·측정값·사람 판정 구분 | 280 tests + 실제 CLI PASS |
| 완료 | B1–B4 · 자산 / job / 승인 / revision·cache | MATCHED (로컬 범위) | 선정 대상 제외 | 파일·DB·HTTP·복구·현재 상태 경계 | 기존 검증 기록 유지 |

## Scoring Criteria

연결 점수 = upstream_weight(0–4) + connection_value(0–3) + safety_value(0–2) + validation_value(0–2) + risk_penalty(0∼−3). 같은 점수면 상위 노드, 더 작은 검증 가능한 변경을 먼저 선택한다. 모델/환경/합의가 필요한 항목은 점수만으로 완료하지 않는다.

후속 우선 후보: **동일 job ID/hash의 불확실 제출 조회·조정, 9점**. 상류 제출 경계를 먼저 연결해 응답 유실 뒤 중복 생성이나 잘못된 실패 판정을 막는다. 새 job을 자동 생성하지 않는 bounded 조회·조정 계약부터 정한다. 로컬 반복·크기별 측정과 단계별 trace는 **6점**이다. cold/warm·모델 로딩·대기·전처리·생성·재합성·저장과 메모리 관찰을 구분하며 ledger 경과를 순수 GPU 시간으로 부르지 않는다. 두 후보 모두 미완료이며 B7 게시 후 root가 다음 폼에서 확정한다. 실사진 품질·CUDA·렌더러 공동 준비와 병행할 수 있다.

## Color Semantics

| 색상 | 의미 |
| --- | --- |
| green | 서버 소유 정상 제어 / 구현·로컬 검증 모듈 |
| blue | 외부·GPU 모델 실행 경로 (목표 그림의 계획) |
| amber | 승인·revision·민감 입력 또는 부분 구현 |
| red | 미연결·미검증 GAP |
| gray | 명시적 로컬 template / 기존 alpha 입력 / 보조 제어 |

## Validation

- 이전 배치: [B1](../../test/relay-b1.md), [B2](../../test/relay-b2.md), [B3](../../test/relay-b3.md), [B4](../../test/relay-b4.md), [B5](../../test/relay-b5.md), [B6](../../test/relay-b6.md) 근거 유지.
- B7 최종 회귀(root): **433 tests PASS(9.39s, -W error)**. pip check·최종 wheel·격리 lazy import PASS. 단위 검증과 실제 모델 호출은 별도다. [B7 기록](../../test/relay-b7.md).
- 수리: 원출력 전부0 거절. 실제 시도 2회 실패 원인이었던 제거된 Diffusers pipeline.enable_vae_tiling()를 pipeline.vae.enable_tiling()로 수정하고 구 API를 허용한 대체 backend 테스트를 보완했다. 실제 설치 API 확인을 포함한 관련 44 tests PASS.
- 실제 세 번째 실행 PASS: 고정 20개 SDXL 파일 SHA VERIFIED, MPS/CPU fallback 없음, 합성 fixture 512×512, 시험 확인 후 실제 배경 생성·3개 레이어 저장. opaque 770 / mismatch 0, Safe Zone exact neutral.
- 시간: cutout ledger 2690.63ms, background ledger 14700.575ms, render CLI 15.343s. 모델 로딩·대기·파일 저장을 포함한 단일 실행 경과이며 순수 GPU 연산 시간·warm 반복 성능이 아니다.
- 작업 후 MPS allocated 7135957248 / driver 10240196608 bytes 관찰. post-job snapshot이며 peak·CUDA VRAM으로 해석하지 않는다.
- root 결과 관찰: 꽃 표현이 부족하고 큰 cap/반사 형태의 제품 잔상이 보임. 기술 실행 PASS, 광고 품질·안전 NOT_ASSESSED. 확인은 SYNTHETIC_TEST_CONFIRMATION_ONLY이며 실제 사람/제품 승인 증거가 아니다.
- 실제 제품 사진 품질·CUDA/GCP·렌더러·팀 인수는 미완료. 모델 파일·생성 이미지는 Git 제외 경로에만 보관한다.
- Mermaid: 기존 목표 PNG 재사용, B7 현재 PNG 갱신. HTML 브라우저 검증 **PASS** (Task Space 143): PNG 2개 decode(552×2540, 1568×3366), 제목 7개·범례 5개·GAP 16행·우선순위 7행, 문서/화면 폭 1492px로 가로 넘침 없음. 최종 실행 근거와 후속 점수표를 반영한 스크린샷을 직접 확인했다.
- 스크린샷: [B7 보고서](../../test/playwright-screenshots/image-runtime-flow-b7.png) · [보존한 B6 보고서](../../test/playwright-screenshots/image-runtime-flow-b6.png).
- 전체 제품 흐름 판정: GAP/PARTIAL. 반복·크기·단계 측정과 제출 조정 등 후속 로컬 구현 후보가 남아 있다.
