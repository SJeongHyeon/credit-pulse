"""규칙 메모리 목록·삭제 API(SPEC §6). 키·DB 없이 indexed_store로 돈다."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.rules import get_rule_store
from backend.auth import current_user_id
from backend.graph.build import build_supervisor_graph
from backend.main import app
from backend.memory import SemanticRuleStore
from tests.semantic_store import indexed_store
from tests.test_supervisor_graph import RELATED_CATALOG, RELATED_QUESTION, FakeRetriever, fake_draft_writer

USER_A = "user-a"
USER_B = "user-b"


def _save_rule(rules: SemanticRuleStore, user_id: str, memo_id: str, item: str, reason: str) -> str:
    """승인 때 고친 항목 하나를 규칙으로 저장하고 rule_id를 돌려준다(save_edited_rules와 같은 경로)."""

    rules.save_edited_rules(user_id, memo_id, [{"item": item, "reason": reason, "edited": True}])
    return f"{memo_id}:{item}"


@pytest.fixture
def rules() -> SemanticRuleStore:
    return SemanticRuleStore(indexed_store())


@pytest.fixture
def login():
    """로그인 사용자를 시험 안에서 바꿀 수 있게 한다."""

    current = {"user_id": USER_A}
    app.dependency_overrides[current_user_id] = lambda: current["user_id"]

    def as_user(user_id: str) -> None:
        current["user_id"] = user_id

    yield as_user
    app.dependency_overrides.pop(current_user_id, None)


@pytest.fixture
def client(rules: SemanticRuleStore, login) -> TestClient:
    app.dependency_overrides[get_rule_store] = lambda: rules
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_user_sees_only_own_rules(client, rules, login):
    rule_a = _save_rule(rules, USER_A, "memo-a", "litigation", "소송은 연결 기준으로 본다")
    rule_b = _save_rule(rules, USER_B, "memo-b", "guarantee_collateral", "보증은 계열사 포함")

    response = client.get("/api/v1/rules")

    assert response.status_code == 200
    assert [rule["rule_id"] for rule in response.json()["items"]] == [rule_a]
    assert response.json()["items"][0] == {
        "rule_id": rule_a,
        "item": "litigation",
        "instruction": "소송은 연결 기준으로 본다",
        "source_memo_id": "memo-a",
    }
    login(USER_B)
    assert [rule["rule_id"] for rule in client.get("/api/v1/rules").json()["items"]] == [rule_b]


def test_deleting_another_users_rule_is_404_and_keeps_it(client, rules, login):
    rule_b = _save_rule(rules, USER_B, "memo-b", "guarantee_collateral", "보증은 계열사 포함")

    response = client.delete(f"/api/v1/rules/{rule_b}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert [rule["rule_id"] for rule in rules.list_rules(USER_B)] == [rule_b]


def test_missing_and_foreign_rule_return_the_same_body(client, rules, login):
    rule_b = _save_rule(rules, USER_B, "memo-b", "guarantee_collateral", "보증은 계열사 포함")

    missing = client.delete("/api/v1/rules/memo-x:litigation")
    foreign = client.delete(f"/api/v1/rules/{rule_b}")

    def without_request_id(body: dict) -> dict:
        return {key: value for key, value in body["error"].items() if key != "request_id"}

    assert missing.status_code == foreign.status_code == 404
    assert without_request_id(missing.json()) == without_request_id(foreign.json())
    assert set(missing.json()["error"]) == set(foreign.json()["error"])


def test_owner_delete_returns_204_and_removes_rule(client, rules, login):
    rule_a = _save_rule(rules, USER_A, "memo-a", "litigation", "소송은 연결 기준으로 본다")

    response = client.delete(f"/api/v1/rules/{rule_a}")

    assert response.status_code == 204
    assert response.content == b""
    assert rules.list_rules(USER_A) == []
    assert client.delete(f"/api/v1/rules/{rule_a}").status_code == 404


def test_deleted_rule_is_not_applied_on_next_graph_run(client, rules, login):
    keep = _save_rule(rules, USER_A, "memo-a", "litigation", "소송은 연결 기준으로 본다")
    wrong = _save_rule(rules, USER_A, "memo-a", "borrowing_structure", "차입금은 별도 기준으로 본다")
    graph = build_supervisor_graph(
        retriever=FakeRetriever(),
        corpus_catalog=RELATED_CATALOG,
        draft_writer=fake_draft_writer,
        memory_store=rules,
    )

    def applied_rule_ids(review_id: str) -> set[str]:
        final = graph.invoke({
            "user_id": USER_A,
            "review_id": review_id,
            "question": RELATED_QUESTION,
            "node_events": [],
            "retrieval_rounds": 0,
        })
        return {rule["rule_id"] for rule in final["applied_rules"]}

    assert applied_rule_ids("review-1") == {keep, wrong}  # 지우기 전에는 둘 다 적용됨(시험이 헛돌지 않는지)
    assert client.delete(f"/api/v1/rules/{wrong}").status_code == 204
    assert applied_rule_ids("review-2") == {keep}


def test_rules_require_login(rules):
    app.dependency_overrides[get_rule_store] = lambda: rules
    try:
        with TestClient(app) as test_client:
            listed = test_client.get("/api/v1/rules")
            deleted = test_client.delete("/api/v1/rules/memo-a:litigation")
    finally:
        app.dependency_overrides.clear()

    assert listed.status_code == deleted.status_code == 401
    assert listed.json()["error"]["code"] == "AUTH_REQUIRED"


def test_rules_are_503_when_store_is_not_assembled(login):
    # REVIEW_RUNTIME=off(conftest)라 앱 상태에 런타임이 없다: 데모 모드·미조립 배포와 같은 상태.
    with TestClient(app) as test_client:
        listed = test_client.get("/api/v1/rules")
        deleted = test_client.delete("/api/v1/rules/memo-a:litigation")

    assert listed.status_code == deleted.status_code == 503
    assert listed.json()["error"]["code"] == "SERVICE_UNAVAILABLE_RULES"
