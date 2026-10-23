# B13 · 밝은 컬러 v2 무광 보존 비교

2026-10-23 사용자 요청: 밝은 컬러를 유지하고 제품의 원본 무광 느낌을 보강해 실제 1장을 추가 생성한다. 프롬프트 출처와 Langfuse 새 버전도 남긴다.

## 변경과 실행

- 기존 `beautyai/cosmetics/vivid-pop` production v1을 조회했다. 배경은 선명한 코발트·코럴로 유지하고 제품 조명을 부드러운 확산광으로 구분했다.
- `REFERENCE MATERIAL FIDELITY`를 추가해 라벤더 병의 원래 무광·미세질감, 코럴 펌프의 원래 반사 수준을 유지하도록 했다. 유광 코팅·날카로운 흰 반사·인위적인 질감 추가를 막는 지시를 넣었다. 꽃 요구나 꽃 금지는 추가하지 않았다.
- Langfuse production **v2**를 생성하고 정확한 버전으로 다시 읽어 요청에 고정했다. v1이 별도로 조회되는 것도 확인했다. 공개 재현본: [vivid-pop-v2.json](../../prompts/cosmetics/vivid-pop-v2.json).
- 이 v2는 이번 라벤더 병/코럴 펌프 샘플을 대상으로 한 지시다. 다른 제품에 적용할 때는 특정 부위 표현을 그 원본에 맞게 검토한다. 모든 제품을 일괄 무광으로 바꾸는 규칙이 아니다.
- 기존 가상 원본 SHA-256 `db8f65e86b2d59b1f48cf0c13fd36cf87d88200fa6111872a4f79f1edf72bbba`를 다시 사용했다. v1 결과를 재편집한 것은 아니다.
- 모델 `gpt-image-2.5-sunburst-2026-09-08`, medium, 1152×1440, product_right. 실제 POST **1회**, 성공 PNG **1장**, 재시도 **0회**.
- 저장 폴더: 로컬 beautyAi의 `outputs/cosmetics-matte-refinement/run-20261023T013349Z-1a9a2852`. 원본·v1·v2 비교 `index.html`, 최종 `provider-prompt.txt`, 요청/실행/원격 확인 JSON을 보관한다. 생성 이미지는 Git에서 제외한다.

## 결과와 확인

- 기존 v1보다 병 어깨의 강한 흰 반사가 줄고, 더 부드러운 음영과 무광 표면 결이 보인다. 배경의 선명한 색상은 유지됐다. AI의 시각 관찰이며 사람의 품질 승인은 PENDING이다.
- 배경 판의 폭·받침대·그림자는 다시 생성됐다. 같은 색/구도 지시를 사용해도 원본 픽셀이나 장면 형태의 완전한 보존을 보장하지 않는다. 빈 라벨이므로 인쇄 글자의 보존은 미검증이다.
- 실행 시간 19.517초. 입력 텍스트 566, 입력 이미지 1505, 출력 이미지 418, 합계 2489토큰. 기존 확인한 공식 단가 기준 추정 **$0.027410**. 실제 청구액은 null / UNRECONCILED이다.
- [Langfuse 실행](https://jp.cloud.langfuse.com/project/cmt966agt0007ad0jn1c3s4up/traces/07227583c4861160ac63767d130d9424)을 v2 observations로 재조회했다. 프롬프트 이름·버전2·요청 해시·출력 해시·모달리티별 사용량이 로컬 영수증과 일치했다.
- 사전 템플릿 검증: JSON 읽기, 변수 치환, 남은 placeholder 없음, 기존 요청과 다른 고정 해시, 꽃 지시 추가 없음 확인. 라이브 호출 전에 provider call 0으로 검증했다.
- 런타임 코드는 변경하지 않았다. 이 문장/자산 변경 때문에 전체 934개 테스트를 다시 실행하지 않았으며 B12 결과와 구분한다.

## 현재 프롬프트의 참고 출처

문장을 통째로 가져오지 않고 구조와 원칙을 참고해 BeautyAI용으로 직접 작성했다. Langfuse는 프롬프트 생성기가 아니라 버전·실행 관리 도구다.

- [OpenAI Image prompting](https://developers.openai.com/api/docs/guides/image-prompting): 참조 사진 편집, 제품 보존, 변경 범위와 결과 확인.
- [Agency Agents Image Prompt Engineer](https://github.com/msitarzewski/agency-agents/blob/main/design/design-image-prompt-engineer.md): 제품·환경·조명·구도·재질 지시 구성.
- [AI Product Photography Prompts](https://github.com/JeremyGDM/awesome-ai-product-photography-prompts): 광고 여백과 정확한 카피 후합성.
- 코발트·코럴 배색, 오른쪽 배치, 왼쪽35% 카피 공간은 프로젝트에 맞춰 정했다. v2 무광 보강은 사용자 피드백과 v1 이미지 관찰을 바탕으로 직접 작성했다.
