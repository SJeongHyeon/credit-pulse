# F3 배포 주소 계정 2개 기억 분리 확인 (2026-11-10)

WBS 코멘트 반영(11.10) **F3**: 배포 주소에서 계정 2개로 로그인해 심사 건·규칙(Store)이 서로 안 보이는지 확인한다. 코드는 수정하지 않았다.

**결론: 배포 주소에서 심사 건과 규칙(Store) 모두 계정별로 분리돼 있음을 확인했다.** 심사 건은 양방향 404로, 규칙은 A에서 규칙을 만든 뒤 B에서 보이지 않는 것으로 확인했다. 확인을 위해 운영 DB에 쓰기가 한 번 있었다(심사 메모 1건 저장, 규칙은 생성 후 삭제 — 5장).

## 환경과 방법

- 대상: 배포 주소 `https://creditpulse-api-236552047198.asia-northeast3.run.app`
- 계정: 평가용 계정 2개. A(화면 표시 이름 "평가 심사역"), B("보조 심사역"). 이메일·비밀번호는 기록하지 않는다.
- 로그인과 승인·삭제 클릭은 사용자가 직접 했다. 같은 Chrome에서 로그아웃 후 계정을 바꿨다.
- 확인 시각: 2026-11-10 오후(KST)
- 방법
  1. 각 계정으로 `/reviews`의 심사 건 목록과 "내 판정 규칙" 패널을 읽고 심사 건 id를 기록
  2. 한 계정으로 로그인한 상태에서 **상대 계정의 심사 건 주소**를 직접 열어 응답 확인(SPEC §6: 다른 사용자의 리소스는 404로 숨김)
  3. 대조군으로 본인의 심사 건은 정상 조회되는지 확인
  4. 규칙은 A에서 판정 이유를 고쳐 승인해 규칙을 만든 뒤(SPEC §5.5: 고친 항목만 규칙으로 저장), B의 규칙 목록에 보이는지 확인하고 A의 규칙을 삭제

## 1. 각 계정이 보는 것 (규칙 생성 전)

| | 계정 A | 계정 B |
|---|---|---|
| 심사 건 수 | 5 | 4 |
| 목록 | 오뚜기 ×4(`dffcd50a`·`5295d60a`·`d730e810`·`f9ae9c04`), SK ×1(`bd53291b`) | LG화학(`13a3ee93`), 오뚜기 ×2(`982fe21d`·`87ee460c`), 롯데칠성음료(`63306dd2`) |
| 판정 규칙 | 없음 | 없음 (`GET /api/v1/rules` 200, 0건) |

- A와 B의 id는 하나도 겹치지 않는다. 오뚜기 11-10 07:35, 질의 3건이 양쪽에 있었지만 상태(A 승인 대기, B 진행 가능)와 id가 달라 서로 다른 심사 건이다.
- 캡처: [A 목록](../images/f3/a_reviews_list.jpg), [B 목록](../images/f3/b_reviews_list.jpg)

## 2. 상대 계정의 심사 건 주소를 열었을 때

**계정 B로 로그인 → A의 심사 건** (브라우저에서 `GET /api/v1/reviews/{id}`, HTTP 상태 확인)

| 대상 | HTTP | 응답 코드 |
|---|---|---|
| A `dffcd50a` | 404 | `RESOURCE_NOT_FOUND` |
| A `5295d60a` | 404 | `RESOURCE_NOT_FOUND` |
| A `d730e810` | 404 | `RESOURCE_NOT_FOUND` |
| A `bd53291b` | 404 | `RESOURCE_NOT_FOUND` |
| A `f9ae9c04` | 404 | `RESOURCE_NOT_FOUND` |
| 대조군: B 본인 `87ee460c` | **200** | 정상 응답 |

**계정 A로 로그인 → B의 심사 건**

| 대상 | 화면 `/reviews/{id}` | API 본문 |
|---|---|---|
| B `982fe21d` | "심사 건을 찾을 수 없습니다." | `RESOURCE_NOT_FOUND` |
| B `13a3ee93`, `87ee460c`, `63306dd2` | (화면은 확인하지 않음) | `RESOURCE_NOT_FOUND` |
| 대조군: A 본인 `dffcd50a` | | 200, 정상 응답 |

- 이 방향에서는 HTTP 숫자 상태를 따로 확인하지 못하고 응답 본문의 오류 코드로 확인했다. SPEC §6의 `RESOURCE_NOT_FOUND`는 404이다.
- 캡처: [A가 B의 심사 건을 열었을 때](../images/f3/a_sees_b_review_not_found.jpg)
- 참고: 응답 JSON을 Chrome에서 볼 때 한 주소(`d730e810`)에서 키가 `error` 대신 `오류`로 보였다. 브라우저 안에서 직접 요청했을 때는 모든 id가 키 `error`·코드 `RESOURCE_NOT_FOUND`였으므로 서버 응답이 아니라 Chrome의 표시 문제로 보인다(원인은 추정이고, 서버 응답이 같다는 것만 확인했다).

## 3. 규칙(Store) 분리

처음에는 두 계정 모두 규칙이 0개여서 분리를 확인하지 못했다. 박민영님이 배포 환경에서 확인하라고 요청해 아래 순서로 확인했다.

| 단계 | 계정 | 결과 |
|---|---|---|
| 승인 전 | A | 판정 규칙 0건 (승인 대기 `dffcd50a` 1개) |
| 이유 끝에 `[F3 확인용 임시 규칙 - 삭제 예정]`을 덧붙여(수정 1개) 승인 | A | "심사 메모로 저장했습니다." 심사 건 `dffcd50a`는 `open`(질의 3건), 대기 초안 없음 |
| 승인 후 | A | **판정 규칙 1건** — 항목 "오뚜기 진행중인 소송건 있어?", 내용 579자(원래 근거 문장 + 위 문구), 출처 메모 `7bd47e02…` |
| 같은 시점 | B | **판정 규칙 0건** (`GET /api/v1/rules` 200, "내 판정 규칙" 패널은 "저장된 판정 규칙이 없습니다", "F3 확인용" 문구 없음, 심사 건 4개 그대로) |
| 정리 | A | 사용자가 "내 판정 규칙" 패널에서 규칙을 삭제 → `GET /api/v1/rules` **0건**, 심사 건 `dffcd50a`는 `open` 유지 |

- 캡처: [A의 규칙 패널(규칙 1건)](../images/f3/a_rule_created.jpg), [B의 규칙 패널(빈 상태)](../images/f3/b_rules_empty_after_a_rule.jpg)
- 이 확인은 규칙이 A의 namespace에만 저장되고 B에게 보이지 않는다는 것을 배포 환경에서 보인 것이다. 고친 항목만 규칙으로 저장되는 동작(SPEC §5.5)도 함께 확인됐다.
- **하지 않은 것**: B가 A의 규칙 id로 삭제를 시도해 404가 나는지는 확인하지 않았다. 격리가 깨져 있다면 A의 규칙이 실제로 지워지는 요청이라 위험해서 생략했다. 이 동작은 `tests/test_rules_api.py::test_deleting_another_users_rule_is_404_and_keeps_it`로만 검증된다.
- 코드 수준 증거(참고)
  - Store namespace는 `("memories", user_id)`이며 user_id 없이 읽거나 쓰지 않는다(SPEC §3.5)
  - 실제 부품으로 한 시연: [G4 에이전트 증거 정리](g4_agent_2026-11-08.md)의 "Store 격리 시연"
  - 테스트: `tests/test_memory.py::test_semantic_rule_store_never_returns_another_users_rules`, `tests/test_memory.py::test_other_user_cannot_update_or_delete_rule`, `tests/test_rules_api.py::test_deleting_another_users_rule_is_404_and_keeps_it`, `tests/test_rules_api.py::test_missing_and_foreign_rule_return_the_same_body`

## 4. 심사 건 분리의 코드 수준 증거 (참고)

`tests/test_reviews_api.py`의 `test_detail_hides_other_users_and_missing_reviews_with_same_not_found`, `test_query_hides_other_users_review_before_running_graph`, `test_approval_hides_other_users_review_before_resume`가 다른 사용자의 심사 건을 같은 404 본문으로 숨기는 동작을 검증한다. 이번 배포 확인은 그 동작이 배포 주소에서도 같다는 것을 보인다.

## 5. 운영 데이터에 남은 변경

F3는 원래 "코드 수정 없는 확인"이었지만, 규칙 분리를 확인하려면 규칙을 만들어야 해서 운영 DB에 쓰기가 한 번 있었다.

| 항목 | 상태 |
|---|---|
| 심사 메모 1건(`7bd47e02…`) | **남아 있다.** 승인으로 저장된 메모는 되돌릴 수 없다. A의 심사 건 `dffcd50a`(오뚜기, 11-10 07:35)의 승인 대기 초안이 승인됐고, 이유 끝에 `[F3 확인용 임시 규칙 - 삭제 예정]`이 붙어 있다. |
| 심사 건 `dffcd50a` 상태 | 승인 대기 → `open`으로 바뀜(질의 3건, 그대로) |
| 판정 규칙 | 생성 후 삭제했다. A·B 모두 0건 |
| 새 LLM 호출·비용 | 없음(기존 초안 승인만, 규칙 임베딩 비용은 미미) |

## 다시 확인하려면

1. 계정 A로 로그인 → `/reviews`에서 심사 건 id를 읽는다(링크 주소 `/reviews/{id}`).
2. 로그아웃 후 계정 B로 로그인 → 주소창에 `/api/v1/reviews/{A의 id}`를 열어 `RESOURCE_NOT_FOUND`가 나오는지 본다.
3. 규칙까지 확인하려면 A에서 승인 화면의 판정이나 이유를 고쳐 승인하고(승인 확인 팝업에서 "확인"), B의 "내 판정 규칙"과 `GET /api/v1/rules`가 0건인지 본다. 끝나면 A의 규칙을 패널에서 삭제한다. 운영 DB에 심사 메모가 남으므로 사전에 합의한다.
