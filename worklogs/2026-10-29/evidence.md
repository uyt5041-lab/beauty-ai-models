# 2026-10-29 검증 근거

작업일 **2026-10-29**, 기록일 **2026-10-30**, 자료 확인 **2026-10-30 09:44 KST**. 당시 활동은 인계 자료·보관 결과를 대조한 회고이며 같은 생성 호출을 다시 실행하지 않았다.

## 고정 원본과 전체 집계

- [구현 TODO 원본3480c34](https://github.com/uyt5041-lab/beauty-ai-models/blob/3480c34282c4ac0c6bedb1369eb0c2c6247cf34c/docs/imageTodo.md): 전체202개, 원본 체크70개.
- 보고일10/29와 [기존 일정 매핑](schedule-map.json)으로 생성한 일지 체크는 **63/202개·31.2%**. 10/28과 같은 수치다. 새 활동 기록을 구현 체크로 추가하지 않았다.
- 원본 SHA-256: db436fbb5440789fc827258b826a0ec02c9447702b001ede1d275c0a19d69193.
- 일정 미정 API 세부5개와 자체호스팅 제외2개는 일지 분자에서 제외했다. 이후 일정도 [전체 목록](full-todo.md)에 모두 표시한다.
- [10/28 기준선301개 테스트 게시](https://github.com/taemin050-sys/project-it-tem/commit/65c2e84bb3a408a8bca123528b3ad3305b55c677)는 전날 근거다. 10/29에 재실행한 결과나 신규 완료로 합산하지 않았다.

## 10/29 활동 근거

[선별한 근거 데이터와 원본 해시](source-provenance.json)에 보고값·미확인 상태·프로젝트 상대 경로를 기록했다. 원본 사진·생성 광고·실제 입력 JSON·런타임 DB는 공개 파일에 포함하지 않았다. 원본이 로컬 자료인 항목은 공개 링크로 가장하지 않는다.

- 팀원 보고: 첨부1152×1440, 약49초, 입력6491(사진5808+텍스트683), 출력418. 사진 입력 토큰 비중89.5%. 약$0.06과 비용 비중75%는 보고된 추정이며 정산·단가 미확인.
- 팀원 시험 우회: MPO 첫 프레임 JPEG 수동 변환, Windows 디렉터리 동기화 생략. 원본 업로드·실제 저장·최종 합성·서비스 다운로드·제품 보존 승인을 입증하지 않는다.
- [텍스트 검토 커밋c558539](https://github.com/taemin050-sys/project-it-tem/commit/c5585397533ab38c1fd08bb478e80c65d235b8e9): 6개 문구, 기존 하이라이트10개 issue 위치와 escaping 확인. 전체 외부 API 파이프라인은 NOT_RUN.
- [색조 예외 검사 위치](https://github.com/taemin050-sys/project-it-tem/blob/c5585397533ab38c1fd08bb478e80c65d235b8e9/src/text_generation/compliance/rule_checker.py#L153): 같은 문장의 무관한 ‘연출’ 때문에 별개 검출이 제외되는 사례. 법적 판단이 아닌 코드 동작 리뷰다.
- [실패 스크립트 종료 위치](https://github.com/taemin050-sys/project-it-tem/blob/c5585397533ab38c1fd08bb478e80c65d235b8e9/scripts/test_failures.py#L160): 실패 출력과 프로세스 종료 코드의 불일치 가능성을 기록했다.
- 디자인 비교: A 중앙 고딕 선호와 밝은 톤, 재색칠의 부자연스러움, 제품 비율 검토 필요. 피치v2는 생성됐으나 봄 의미 부족 피드백 뒤 합성 보류, v3-spring은 미생성이다. 로컬 실험을 팀 서비스 기능 또는 제품 보존 합격으로 표시하지 않았다.

## 작성·게시 날짜의 구분

10/29 공유 TODO와 후속 계획은 당시 미게시 WIP였으며 [10/30 첫 게시c559ba3](https://github.com/uyt5041-lab/beauty-ai-models/commit/c559ba309df08955bc9b7b4b27eb43ebcff56e8b)에 포함됐다.

10/30 R1–R3 구현·348개 테스트는 [별도 실행 기록](https://github.com/uyt5041-lab/beauty-ai-models/blob/bbff00b7c2769c97e3ccc65e32ea01846124702a/fivecircles/work/image-mvp-relay-20261030.md)에 있다. 이 일지의 완료 수에 소급하지 않았다.

이번 문서 보완에서 새 이미지/API 호출과 외부 메시지 전송은0회다. 10/29 기존 디자인 생성 실험의 호출0회를 뜻하지 않는다. 일지·문서 검증은 [validation.json](validation.json)으로 확인하며 실제 모델 품질·Windows 런타임·서비스 인수와 구분한다.

## 문서 검증 결과 · 2026-10-30

진행률 정책7개 테스트, 전체202개·체크63개, 보고일 이후/일정 미정 항목의 미체크, 일지8항목과 MD/HTML 수치, 상대 링크, 기존 날짜 기록 보존을 확인했다.

README의10/29 링크로 실제 일지와 전체 목록을 열어8항목·63/202 progress·체크202개/선택63개·SVG 로딩·가로 넘침 없음을 확인했다. [일지 화면](preview-journal.png) · [전체 목록 화면](preview-checklist.png) · [브라우저 검증](browser-validation.json) · [공간 종료](browser-finish.json).

초기 CLI 응답 시간초과 뒤 공간 목록을 확인했으며 해당 공간이 생성되지 않은 것을 확인한 후 동일 목표 이름으로 생성했다. 최종 검증 공간24는 정상 종료했다. 실제 모델·Windows·서비스 UI의 시험을 수행한 것은 아니다.
