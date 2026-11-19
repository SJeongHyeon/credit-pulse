import os
import threading
from uuid import uuid4

import pytest
from backend.memory import PostgresRuleStore


from urllib.parse import urlparse

DATABASE_URL = os.getenv("TEST_MEMORY_DATABASE_URL")
if DATABASE_URL:
    parsed = urlparse(DATABASE_URL)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or not parsed.path.endswith("_test"):
        pytest.skip("Rule-store integration requires a local database ending in _test", allow_module_level=True)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TEST_MEMORY_DATABASE_URL is required for the Postgres rule-store integration test",
)


def test_postgres_rule_store_isolates_users_across_store_instances():
    suffix = uuid4().hex
    user_a = f"rule-store-a-{suffix}"
    user_b = f"rule-store-b-{suffix}"
    rule_a = {
        "item": "차입금",
        "instruction": "연결 기준으로 판단한다.",
        "source_memo_id": f"memo-a-{suffix}",
    }
    rule_b = {
        "item": "소송",
        "instruction": "우발부채 주석을 우선 확인한다.",
        "source_memo_id": f"memo-b-{suffix}",
    }

    first_store = PostgresRuleStore(DATABASE_URL)
    first_store.setup()
    first_store.create_rule(user_a, "rule-a", rule_a)
    first_store.create_rule(user_b, "rule-b", rule_b)

    second_store = PostgresRuleStore(DATABASE_URL)
    try:
        saved_a = second_store.list_rules(user_a)[0]
        saved_b = second_store.list_rules(user_b)[0]
        assert {key: saved_a[key] for key in ("rule_id", *rule_a)} == {
            "rule_id": "rule-a",
            **rule_a,
        }
        assert {key: saved_b[key] for key in ("rule_id", *rule_b)} == {
            "rule_id": "rule-b",
            **rule_b,
        }
    finally:
        second_store.delete_rule(user_a, "rule-a")
        second_store.delete_rule(user_b, "rule-b")


def test_postgres_rule_store_allows_only_one_concurrent_200th_rule():
    suffix = uuid4().hex
    user_id = f"rule-limit-{suffix}"
    rule = {
        "item": "차입금",
        "instruction": "연결 기준으로 판단한다.",
        "source_memo_id": f"memo-{suffix}",
    }
    setup_store = PostgresRuleStore(DATABASE_URL)
    setup_store.setup()
    for index in range(199):
        setup_store.create_rule(user_id, f"rule-{index}", rule)

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def create_last_rule(rule_id: str) -> None:
        store = PostgresRuleStore(DATABASE_URL)
        barrier.wait()
        try:
            store.create_rule(user_id, rule_id, rule)
        except ValueError as error:
            outcome = str(error)
        else:
            outcome = "created"
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [
        threading.Thread(target=create_last_rule, args=("rule-199",)),
        threading.Thread(target=create_last_rule, args=("rule-200",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert sorted(outcomes) == ["CONFLICT_RULE_LIMIT", "created"]
        assert len(setup_store.list_rules(user_id)) == 200
    finally:
        for saved_rule in setup_store.list_rules(user_id):
            setup_store.delete_rule(user_id, saved_rule["rule_id"])
