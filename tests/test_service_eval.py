"""scripts/run_service_eval.py: 배포 서비스로 평가 질문을 보내는 흐름(네트워크 없이 가짜 응답으로)."""
import pytest

from scripts import run_service_eval as svc

CASE = {"id": 3, "question": "GS리테일의 주요 소송은?", "kind": "relevant", "corp_code": "00140177", "fiscal_year": 2025}


class FakeService:
    """요청을 기록하고 정해 둔 응답을 차례로 돌려준다."""

    def __init__(self, query_responses):
        self.calls = []
        self.query_responses = list(query_responses)

    def send(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/api/v1/reviews":
            return 201, {"id": "11111111-1111-1111-1111-111111111111", "corp_code": body["corp_query"]}
        if path.endswith("/queries"):
            return self.query_responses.pop(0)
        if path.endswith("/approvals:cancel"):
            return 200, {"review_id": "r", "approval_seq": 1, "status": "open"}
        raise AssertionError(path)


def ticking_clock(step=2.0):
    now = {"t": 0.0}

    def clock():
        now["t"] += step
        return now["t"]

    return clock


def test_answered_question_is_timed_and_its_approval_is_cancelled():
    draft = [{"item": "q", "verdict": "applicable", "reason": "r",
              "citations": [{"chunk_id": "chunk-a"}, {"chunk_id": "chunk-b"}]}]
    service = FakeService([(200, {"outcome": "awaiting_approval", "draft": draft, "applied_rules": []})])

    record = svc.ask_one(service.send, CASE, cancel=True, clock=ticking_clock())

    assert [(m, p.rsplit("/", 1)[-1]) for m, p, _ in service.calls] == [("POST", "reviews"), ("POST", "queries"), ("POST", "approvals:cancel")]
    assert service.calls[0][2] == {"corp_query": "00140177", "fiscal_year": 2025}  # 회사 코드로 심사 건을 만든다
    assert service.calls[1][2] == {"question": CASE["question"]}
    assert record["status_code"] == 200 and record["seconds"] == 2.0 and record["outcome"] == "awaiting_approval"
    assert record["citations"] == [{"chunk_id": "chunk-a"}, {"chunk_id": "chunk-b"}] and record["cancelled"] is True


def test_refusal_is_not_cancelled_and_no_cancel_flag_leaves_approval():
    refused = FakeService([(200, {"outcome": "refused", "refusal_reason": "ITEM_NOT_DISCLOSED", "applied_rules": []})])
    record = svc.ask_one(refused.send, CASE, cancel=True, clock=ticking_clock())
    assert record["outcome"] == "refused" and record["refusal_reason"] == "ITEM_NOT_DISCLOSED"
    assert record["cancelled"] is None and len(refused.calls) == 2

    kept = FakeService([(200, {"outcome": "awaiting_approval", "draft": []})])
    assert svc.ask_one(kept.send, CASE, cancel=False, clock=ticking_clock())["cancelled"] is None
    assert len(kept.calls) == 2


def test_rate_limit_waits_and_retries_at_most_twice():
    limited = (429, {"error": {"code": "RATE_LIMITED", "message": "x"}})
    waits = []
    service = FakeService([limited, (200, {"outcome": "refused", "refusal_reason": "OUT_OF_DOMAIN"})])
    record = svc.ask_one(service.send, CASE, cancel=True, sleep=waits.append, clock=ticking_clock())
    assert record["status_code"] == 200 and record["rate_limited_retries"] == 1 and waits == [61.0]

    waits.clear()
    stuck = FakeService([limited, limited, limited])
    record = svc.ask_one(stuck.send, CASE, cancel=True, sleep=waits.append, clock=ticking_clock())
    assert record["status_code"] == 429 and record["error_code"] == "RATE_LIMITED"
    assert waits == [61.0, 61.0] and len([c for c in stuck.calls if c[1].endswith("/queries")]) == 3  # 상한 3번


def test_failures_are_recorded_not_raised():
    def no_corp(method, path, body=None):
        return 422, {"error": {"code": "VALIDATION_CORP_NOT_FOUND", "message": "x"}}

    record = svc.ask_one(no_corp, CASE, cancel=True)
    assert record["status_code"] == 422 and record["error_code"] == "VALIDATION_CORP_NOT_FOUND" and record["seconds"] is None

    upstream = FakeService([(503, {"error": {"code": "SERVICE_UNAVAILABLE_LLM", "message": "x"}})])
    record = svc.ask_one(upstream.send, CASE, cancel=True, clock=ticking_clock())
    assert record["status_code"] == 503 and record["error_code"] == "SERVICE_UNAVAILABLE_LLM" and record["seconds"] == 2.0


def test_only_questions_with_a_fixed_company_are_sent():
    dataset = [{"id": 1, "input": "a", "kind": "relevant"}, {"id": 2, "input": "b", "kind": "irrelevant"},
               {"id": 3, "input": "c", "kind": "relevant"}]
    filters = [{"id": 1, "corp_code": "001", "fiscal_year": 2025}, {"id": 2, "corp_code": None, "fiscal_year": None},
               {"id": 3, "corp_code": "003", "fiscal_year": 2025}]
    cases, skipped = svc.service_cases(dataset, filters)
    assert [c["id"] for c in cases] == [1, 3] and skipped == [2]
    assert svc.service_cases(dataset, filters, ids={3})[0][0]["corp_code"] == "003"


def test_summary_scores_only_completed_questions_and_times_searched_ones():
    gold = {
        1: {"id": 1, "kind": "relevant", "gold_chunks": ["g1"]},
        2: {"id": 2, "kind": "relevant", "gold_chunks": ["g2"]},
        3: {"id": 3, "kind": "irrelevant", "irrelevant_type": "missing_attribute", "gold_chunks": []},
        4: {"id": 4, "kind": "relevant", "gold_chunks": ["g4"]},
    }
    base = {"error_code": None, "rate_limited_retries": 0, "cancelled": True, "draft_items": []}
    records = [
        {**base, "id": 1, "status_code": 200, "seconds": 4.0, "outcome": "awaiting_approval", "refusal_reason": None, "citations": [{"chunk_id": "g1"}]},
        {**base, "id": 2, "status_code": 200, "seconds": 6.0, "outcome": "awaiting_approval", "refusal_reason": None, "citations": [{"chunk_id": "other"}]},
        {**base, "id": 3, "status_code": 200, "seconds": 1.0, "outcome": "refused", "refusal_reason": "ITEM_NOT_DISCLOSED", "citations": [], "cancelled": None},
        {**base, "id": 4, "status_code": 503, "seconds": 9.0, "outcome": None, "refusal_reason": None, "citations": [], "error_code": "SERVICE_UNAVAILABLE_LLM", "cancelled": None},
    ]
    summary = svc.summarize(records, gold, searched_ids={1, 2, 4})

    assert summary["sent"] == 4 and summary["ok"] == 3
    assert summary["failed"] == [{"id": 4, "status_code": 503, "error_code": "SERVICE_UNAVAILABLE_LLM"}]
    assert summary["response"]["correct_citation"] == {"numerator": 1, "denominator": 2}  # 실패한 4번은 분모에서 빠진다
    assert summary["response"]["wrong_citation"] == {"numerator": 1, "denominator": 2}
    assert summary["response"]["correct_refusal"] == {"numerator": 1, "denominator": 1}
    assert summary["seconds_searched"] == {"n": 2, "p50": 5.0, "p95": 6.0, "mean": 5.0, "max": 6.0}  # 1·2번만(3번은 검색 문항 아님)
    assert summary["seconds_all"]["n"] == 3 and summary["first_query_seconds"] == 4.0
    assert summary["citation_ids_overlap_gold"] is True and summary["not_cancelled"] == []


def test_plain_http_is_refused_except_for_local_hosts():
    with pytest.raises(SystemExit):
        svc.http_sender("http://example.com", 5)
    svc.http_sender("http://localhost:8000", 5)
    svc.http_sender("https://example.com", 5)


def test_flow_matches_the_real_api_contract():
    """가짜 응답이 아니라 실제 FastAPI 앱(저장소·그래프만 가짜)으로 경로·상태 코드·응답 칸을 확인한다."""

    from dataclasses import replace

    from fastapi.testclient import TestClient

    from backend.api.reviews import get_review_graph_runner, get_review_repository
    from backend.auth import current_user_id
    from backend.main import app
    from tests.test_reviews_api import USER_A, FakeReviewGraphRunner, FakeReviewRepository, _review

    class Repository(FakeReviewRepository):
        def create_review(self, user_id, corp_query, fiscal_year):
            review = replace(_review(review_id="30000000-0000-0000-0000-0000000000c3", user_id=user_id), corp_code=corp_query)
            self.reviews[review.id] = review
            self.created.append(review)
            return review

    class Runner(FakeReviewGraphRunner):
        def ask(self, **kwargs):
            super().ask(**kwargs)
            return {"outcome": "awaiting_approval", "draft": [
                {"item": "q", "verdict": "applicable", "reason": "r", "citations": [{"chunk_id": "chunk-a", "quote": "x"}]}]}

    repository, runner = Repository(), Runner()
    app.dependency_overrides[get_review_repository] = lambda: repository
    app.dependency_overrides[get_review_graph_runner] = lambda: runner
    app.dependency_overrides[current_user_id] = lambda: USER_A
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)

    def send(method, path, body=None):
        response = client.request(method, path, json=body)
        return response.status_code, (response.json() if response.content else None)

    try:
        record = svc.ask_one(send, CASE, cancel=True)
    finally:
        app.dependency_overrides.clear()

    assert record["status_code"] == 200 and record["outcome"] == "awaiting_approval" and record["error_code"] is None
    assert record["citations"] == [{"chunk_id": "chunk-a"}] and record["cancelled"] is True
    assert repository.created[0].corp_code == CASE["corp_code"]
    assert runner.calls[0]["question"] == CASE["question"] and runner.resume_calls[0]["action"] == "cancel"
    assert repository.reviews[record["review_id"]].status == "open"  # 취소 뒤 대기 중인 승인이 남지 않는다


class FakeDirectService:
    """바로 질문 API: 심사 건을 먼저 만들지 않는다."""

    def __init__(self, response):
        self.calls = []
        self.response = response

    def send(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/api/v1/queries":
            return self.response
        if path.endswith("/approvals:cancel"):
            return 200, {"review_id": "r", "approval_seq": 1, "status": "open"}
        raise AssertionError(path)


def test_direct_question_sends_only_the_question_and_cancels_the_review_the_service_made():
    draft = [{"item": "q", "verdict": "applicable", "reason": "r", "citations": [{"chunk_id": "chunk-a"}]}]
    service = FakeDirectService((200, {"outcome": "awaiting_approval", "draft": draft,
                                       "review": {"id": "22222222-2222-2222-2222-222222222222"}}))
    case = {"id": 3, "question": "GS리테일의 2025년 주요 소송은?", "kind": "relevant"}

    record = svc.ask_one(service.send, case, cancel=True, clock=ticking_clock(), direct=True)

    assert service.calls[0] == ("POST", "/api/v1/queries", {"question": case["question"]})
    assert service.calls[1][1] == "/api/v1/reviews/22222222-2222-2222-2222-222222222222/approvals:cancel"
    assert record["review_id"] == "22222222-2222-2222-2222-222222222222" and record["cancelled"] is True
    assert record["citations"] == [{"chunk_id": "chunk-a"}] and record["corp_code"] is None


def test_direct_refusal_without_a_review_makes_one_request():
    service = FakeDirectService((200, {"outcome": "refused", "refusal_reason": "CORP_NOT_IN_CORPUS", "review": None}))

    record = svc.ask_one(service.send, {"id": 26, "question": "없는회사의 2025년 차입금은?", "kind": "irrelevant"},
                         cancel=True, clock=ticking_clock(), direct=True)

    assert len(service.calls) == 1
    assert (record["outcome"], record["refusal_reason"], record["review_id"], record["cancelled"]) == (
        "refused", "CORP_NOT_IN_CORPUS", None, None)


def test_direct_cases_keep_every_question_including_those_without_a_company():
    dataset = [{"id": 1, "input": "A사 질문", "kind": "relevant"}, {"id": 31, "input": "두 회사 비교", "kind": "relevant"}]

    assert [c["id"] for c in svc.direct_cases(dataset)] == [1, 31]
    assert svc.direct_cases(dataset, ids={31}) == [{"id": 31, "question": "두 회사 비교", "kind": "relevant"}]


def test_restart_resume_checks_the_pending_draft_then_approves_once():
    """서버 재시작 뒤 재개 확인(service_restart_resume): 승인 대기와 초안이 그대로인지 보고, 승인 뒤 재전송은 409."""

    from scripts import service_restart_resume as restart

    draft = [{"item": "borrowing_structure", "verdict": "applicable", "reason": "r", "citations": [{"chunk_id": "c"}]}]
    calls = []
    approvals = iter([(200, {"review_id": "rid", "approval_seq": 1, "memo_id": "m"}),
                      (409, {"error": {"code": "CONFLICT_NOT_AWAITING_APPROVAL"}})])
    details = iter([(200, {"status": "awaiting_approval", "pending_draft": draft}), (200, {"status": "closed"})])

    def send(method, path, body=None):
        calls.append((method, path, body))
        return next(approvals) if path.endswith("/approvals") else next(details)

    record = restart.resume(send, {"review_id": "rid", "draft": restart.draft_summary(draft)})

    assert record["status_after_restart"] == "awaiting_approval" and record["draft_same_as_before_restart"] is True
    assert (record["approve_http"], record["memo_id_saved"], record["second_approve_http"]) == (200, True, 409)
    assert calls[1][2] == {"decisions": [{"item": "borrowing_structure", "verdict": "applicable", "reason": "r"}]}


# 162번: 되묻기로 끝난 문항에 되묻기 답을 한 번 더 보낸다
ASKED = {"id": 31, "question": "두 회사의 차입금을 비교해 주세요.", "kind": "relevant"}
ASK_BACK = (200, {"outcome": "answered", "target_required": True, "draft": [], "review": None,
                  "refusal_message": "비교할 회사 이름과 사업연도를 모두 적어 다시 질문해 주세요."})
CLARIFIED = (200, {"outcome": "awaiting_approval", "applied_rules": [],
                   "draft": [{"item": "q", "verdict": "applicable", "reason": "r", "citations": [{"chunk_id": "chunk-a"}]}],
                   "review": {"id": "22222222-2222-2222-2222-222222222222", "corp_name": "가", "fiscal_year": 2025}})


def test_clarification_reply_is_sent_once_with_the_original_question_and_times_are_summed():
    service = FakeService([ASK_BACK, CLARIFIED])
    waits = []

    record = svc.ask_one(service.send, ASKED, cancel=True, direct=True, clock=ticking_clock(), sleep=waits.append,
                         clarify="가와 나, 2025 사업연도", clarify_pause=12.5)

    queries = [call for call in service.calls if call[1] == "/api/v1/queries"]
    assert [body for _, _, body in queries] == [
        {"question": "두 회사의 차입금을 비교해 주세요."},
        {"question": "가와 나, 2025 사업연도", "clarifies": "두 회사의 차입금을 비교해 주세요."},
    ]
    assert waits == [12.5]  # 같은 계정이면 분당 한도 때문에 띄운다
    assert record["first_turn"] == {"status_code": 200, "seconds": 2.0, "outcome": "answered", "target_required": True,
                                    "reply": "가와 나, 2025 사업연도", "account": "same"}
    assert (record["outcome"], record["seconds"], record["citations"]) == ("awaiting_approval", 4.0, [{"chunk_id": "chunk-a"}])
    assert record["cancelled"] is True and service.calls[-1][1].endswith("/approvals:cancel")


def test_clarification_turn_can_use_a_second_account_and_cancels_with_it():
    first, second = FakeService([ASK_BACK]), FakeService([CLARIFIED])
    waits = []

    record = svc.ask_one(first.send, ASKED, cancel=True, direct=True, clock=ticking_clock(), sleep=waits.append,
                         clarify="가와 나, 2025 사업연도", clarify_send=second.send)

    assert [call[1] for call in first.calls] == ["/api/v1/queries"]
    assert [call[1] for call in second.calls] == [
        "/api/v1/queries", "/api/v1/reviews/22222222-2222-2222-2222-222222222222/approvals:cancel"]
    assert waits == [] and record["first_turn"]["account"] == "second" and record["cancelled"] is True


def test_no_second_turn_when_the_first_answer_is_not_an_ask_back_or_no_reply_is_given():
    answered = FakeService([CLARIFIED])
    record = svc.ask_one(answered.send, ASKED, cancel=False, direct=True, clock=ticking_clock(), clarify="가와 나")
    assert "first_turn" not in record and len(answered.calls) == 1

    asked_back = FakeService([ASK_BACK])
    record = svc.ask_one(asked_back.send, ASKED, cancel=False, direct=True, clock=ticking_clock())
    assert "first_turn" not in record and len(asked_back.calls) == 1 and record["outcome"] == "answered"


def test_failed_clarification_turn_is_recorded_with_the_first_turn():
    service = FakeService([ASK_BACK, (503, {"error": {"code": "UPSTREAM_API_ERROR"}})])

    record = svc.ask_one(service.send, ASKED, cancel=True, direct=True, clock=ticking_clock(), sleep=lambda _: None,
                         clarify="가와 나")

    assert (record["status_code"], record["error_code"]) == (503, "UPSTREAM_API_ERROR")
    assert record["first_turn"]["target_required"] is True and record["outcome"] is None
