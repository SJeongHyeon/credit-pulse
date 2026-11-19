import pytest
from types import SimpleNamespace

from langgraph.store.memory import InMemoryStore

from backend import memory
from tests.semantic_store import FAKE_INDEX_CONFIG, fake_embed, indexed_store


class RecordingSearchStore:
    index_config = FAKE_INDEX_CONFIG

    def __init__(self):
        self.search_calls = []

    def search(self, namespace_prefix, *, query=None, limit=None):
        self.search_calls.append(
            {"namespace": namespace_prefix, "query": query, "limit": limit}
        )
        return []


class RecordingWithRules(RecordingSearchStore):
    """규칙이 하나 있는 저장소. 조회 인자만 기록한다."""

    def search(self, namespace_prefix, *, query=None, limit=None):
        super().search(namespace_prefix, query=query, limit=limit)
        return [SimpleNamespace(key="rule-a", value={"item": "차입금", "instruction": "x", "source_memo_id": "memo-a"})]


class UserScopedSearchStore:
    index_config = FAKE_INDEX_CONFIG

    def __init__(self):
        self.rules = {
            ("memories", "user-a"): [
                SimpleNamespace(
                    key="rule-a",
                    value={
                        "item": "차입금",
                        "instruction": "연결 기준으로 판단한다.",
                        "source_memo_id": "memo-a",
                    },
                )
            ],
            ("memories", "user-b"): [
                SimpleNamespace(
                    key="rule-b",
                    value={
                        "item": "소송",
                        "instruction": "우발부채 주석을 우선 확인한다.",
                        "source_memo_id": "memo-b",
                    },
                )
            ],
        }

    def search(self, namespace_prefix, *, query=None, limit=None):
        return self.rules.get(namespace_prefix, [])[:limit]


def test_list_rules_returns_only_calling_users_namespace():
    rules = memory.RuleStore(InMemoryStore())

    rules.create_rule(
        "user-a",
        "rule-a",
        {
            "item": "차입금",
            "instruction": "연결 기준으로 판단한다.",
            "source_memo_id": "memo-a",
        },
    )
    rules.create_rule(
        "user-b",
        "rule-b",
        {
            "item": "소송",
            "instruction": "우발부채 주석을 우선 확인한다.",
            "source_memo_id": "memo-b",
        },
    )

    assert [rule["rule_id"] for rule in rules.list_rules("user-a")] == ["rule-a"]


def test_update_rule_replaces_existing_rule_without_duplicate():
    rules = memory.RuleStore(InMemoryStore())
    rules.create_rule(
        "user-a",
        "rule-a",
        {
            "item": "차입금",
            "instruction": "연결 기준으로 판단한다.",
            "source_memo_id": "memo-a",
        },
    )

    rules.update_rule(
        "user-a",
        "rule-a",
        {
            "item": "차입금",
            "instruction": "별도 기준으로 판단한다.",
            "source_memo_id": "memo-b",
        },
    )

    saved_rule = rules.list_rules("user-a")[0]
    assert {
        key: saved_rule[key]
        for key in ("rule_id", "item", "instruction", "source_memo_id")
    } == {
        "rule_id": "rule-a",
        "item": "차입금",
        "instruction": "별도 기준으로 판단한다.",
        "source_memo_id": "memo-b",
    }
    assert saved_rule["created_at"]
    assert saved_rule["updated_at"]


def test_delete_rule_removes_only_calling_users_rule():
    rules = memory.RuleStore(InMemoryStore())
    rules.create_rule(
        "user-a",
        "rule-a",
        {
            "item": "차입금",
            "instruction": "연결 기준으로 판단한다.",
            "source_memo_id": "memo-a",
        },
    )
    rules.create_rule(
        "user-b",
        "rule-b",
        {
            "item": "소송",
            "instruction": "우발부채 주석을 우선 확인한다.",
            "source_memo_id": "memo-b",
        },
    )

    rules.delete_rule("user-a", "rule-a")

    assert rules.list_rules("user-a") == []
    assert [rule["rule_id"] for rule in rules.list_rules("user-b")] == ["rule-b"]


def test_create_rule_rejects_201st_distinct_rule():
    rules = memory.RuleStore(InMemoryStore())
    rule = {
        "item": "차입금",
        "instruction": "연결 기준으로 판단한다.",
        "source_memo_id": "memo-a",
    }
    for index in range(200):
        rules.create_rule("user-a", f"rule-{index}", rule)

    with pytest.raises(ValueError, match="CONFLICT_RULE_LIMIT"):
        rules.create_rule("user-a", "rule-200", rule)


def test_namespace_rejects_blank_user_id():
    with pytest.raises(ValueError, match="user_id"):
        memory.namespace("   ")


def test_update_rule_at_limit_keeps_rule_count():
    rules = memory.RuleStore(InMemoryStore())
    rule = {
        "item": "차입금",
        "instruction": "연결 기준으로 판단한다.",
        "source_memo_id": "memo-a",
    }
    for index in range(200):
        rules.create_rule("user-a", f"rule-{index}", rule)

    rules.update_rule(
        "user-a",
        "rule-0",
        {**rule, "instruction": "별도 기준으로 판단한다."},
    )

    assert len(rules.list_rules("user-a")) == 200


def test_update_rule_rejects_missing_rule_id():
    rules = memory.RuleStore(InMemoryStore())

    with pytest.raises(ValueError, match="RULE_NOT_FOUND"):
        rules.update_rule(
            "user-a",
            "missing-rule",
            {
                "item": "차입금",
                "instruction": "연결 기준으로 판단한다.",
                "source_memo_id": "memo-a",
            },
        )


def test_other_user_cannot_update_or_delete_rule():
    rules = memory.RuleStore(InMemoryStore())
    rule = {
        "item": "차입금",
        "instruction": "연결 기준으로 판단한다.",
        "source_memo_id": "memo-b",
    }
    rules.create_rule("user-b", "rule-b", rule)

    with pytest.raises(ValueError, match="RULE_NOT_FOUND"):
        rules.update_rule("user-a", "rule-b", rule)
    rules.delete_rule("user-a", "rule-b")

    saved_rule = rules.list_rules("user-b")[0]
    assert {key: saved_rule[key] for key in ("rule_id", *rule)} == {
        "rule_id": "rule-b",
        **rule,
    }
    assert saved_rule["created_at"]
    assert saved_rule["updated_at"]


def test_create_rule_rejects_fields_outside_rule_contract():
    rules = memory.RuleStore(InMemoryStore())

    with pytest.raises(ValueError, match="INVALID_RULE_PAYLOAD"):
        rules.create_rule(
            "user-a",
            "rule-a",
            {
                "item": "차입금",
                "instruction": "연결 기준으로 판단한다.",
                "source_memo_id": "memo-a",
                "rule_id": "forged-rule-id",
            },
        )

    assert rules.list_rules("user-a") == []


def test_semantic_rule_store_searches_only_the_authenticated_user_namespace():
    store = RecordingSearchStore()
    rules = memory.SemanticRuleStore(store)

    assert rules.search_rules("user-a", "차입금 만기 구조") == []
    # 규칙이 없으면 임베딩 없는 존재 확인 1회로 끝난다(질문을 임베딩하는 의미 검색은 하지 않는다)
    assert store.search_calls == [{"namespace": ("memories", "user-a"), "query": None, "limit": 1}]


def test_semantic_rule_store_embeds_the_question_only_when_the_user_has_rules():
    """규칙이 있을 때만 의미 검색(질문 임베딩)을 하고, 두 조회 모두 그 사용자 namespace만 본다."""

    embedded: list[str] = []

    def counting_embed(texts):
        embedded.extend(texts)
        return fake_embed(texts)

    store = InMemoryStore(index={**FAKE_INDEX_CONFIG, "embed": counting_embed, "fields": ["instruction"]})
    rules = memory.SemanticRuleStore(store)

    assert rules.search_rules("user-a", "차입금 만기 구조") == [] and embedded == []  # 규칙 없음: 임베딩 0회
    store.put(memory.namespace("user-b"), "memo-9:x", {"item": "소송", "instruction": "주석을 본다.", "source_memo_id": "memo-9"})
    embedded.clear()
    assert rules.search_rules("user-a", "차입금 만기 구조") == [] and embedded == []  # 다른 사용자 규칙은 세지 않는다

    store.put(memory.namespace("user-a"), "memo-1:borrowing", {"item": "차입금", "instruction": "연결 기준으로 판단한다.", "source_memo_id": "memo-1"})
    embedded.clear()
    found = rules.search_rules("user-a", "차입금 만기 구조")
    assert [rule["rule_id"] for rule in found] == ["memo-1:borrowing"]
    assert embedded == ["차입금 만기 구조"]  # 규칙 있음: 질문 임베딩 1회

    recording = RecordingWithRules()
    memory.SemanticRuleStore(recording).search_rules("user-a", "차입금 만기 구조")
    assert recording.search_calls == [
        {"namespace": ("memories", "user-a"), "query": None, "limit": 1},
        {"namespace": ("memories", "user-a"), "query": "차입금 만기 구조", "limit": 5},  # SPEC §4: 질문당 적용 규칙 5
    ]


def test_semantic_rule_store_saves_only_edited_approval_items():
    store = indexed_store()
    rules = memory.SemanticRuleStore(store)

    rules.save_edited_rules(
        "user-a",
        "memo-1",
        [
            {
                "item": "litigation",
                "reason": "원본 초안 그대로 승인",
                "edited": False,
            },
            {
                "item": "borrowing_structure",
                "reason": "만기 구조를 별도로 확인한다.",
                "edited": True,
            },
        ],
    )

    user_namespace = memory.namespace("user-a")
    assert store.get(user_namespace, "memo-1:litigation") is None
    saved = store.get(user_namespace, "memo-1:borrowing_structure")
    assert saved is not None
    assert saved.value == {
        "item": "borrowing_structure",
        "instruction": "만기 구조를 별도로 확인한다.",
        "source_memo_id": "memo-1",
    }


def test_semantic_rule_store_never_returns_another_users_rules():
    rules = memory.SemanticRuleStore(UserScopedSearchStore())

    user_a_rules = rules.search_rules("user-a", "차입금 판단")
    user_b_rules = rules.search_rules("user-b", "차입금 판단")

    assert [rule["rule_id"] for rule in user_a_rules] == ["rule-a"]
    assert [rule["rule_id"] for rule in user_b_rules] == ["rule-b"]
    assert user_a_rules[0]["instruction"] != user_b_rules[0]["instruction"]


def test_semantic_rule_store_rejects_201st_new_rule_but_updates_same_key():
    store = indexed_store()
    rules = memory.SemanticRuleStore(store)
    user_namespace = memory.namespace("user-a")
    store.put(
        user_namespace,
        "old-memo:item-0",
        {
            "item": "item-0",
            "instruction": "기존 규칙",
            "source_memo_id": "old-memo",
        },
    )
    for number in range(1, 200):
        store.put(
            user_namespace,
            f"existing-{number}",
            {
                "item": f"item-{number}",
                "instruction": "기존 규칙",
                "source_memo_id": "old-memo",
            },
        )

    with pytest.raises(ValueError, match="CONFLICT_RULE_LIMIT"):
        rules.save_edited_rules(
            "user-a",
            "new-memo",
            [{"item": "new-item", "reason": "새 규칙", "edited": True}],
        )

    rules.save_edited_rules(
        "user-a",
        "old-memo",
        [{"item": "item-0", "reason": "수정한 기존 규칙", "edited": True}],
    )
    assert store.get(user_namespace, "old-memo:item-0").value["instruction"] == "수정한 기존 규칙"


def _rule(number):
    return {
        "item": f"item-{number}",
        "instruction": f"규칙 {number} " + "가" * number,
        "source_memo_id": "memo",
    }


def test_semantic_search_returns_only_the_top_five_of_many_rules():
    store = indexed_store()
    rules = memory.SemanticRuleStore(store)
    user_namespace = memory.namespace("user-a")
    for number in range(30):
        store.put(user_namespace, f"rule-{number}", _rule(number))

    found = rules.search_rules("user-a", "차입금 만기 구조")

    assert len(found) == 5  # 리터럴: SPEC §4 질문당 적용 규칙
    assert memory.SEMANTIC_RULE_TOP_K == 5
    assert len({rule["rule_id"] for rule in found}) == 5


def test_semantic_rule_store_rejects_store_without_semantic_index():
    # EC-07: 인덱스 없는 Store는 전체 namespace 스캔으로 대체하지 않고 만들 때 멈춘다
    with pytest.raises(memory.SemanticIndexNotConfigured):
        memory.SemanticRuleStore(InMemoryStore())


def test_semantic_rule_store_rejects_store_that_does_not_report_an_index():
    class NoIndexStore:
        def __init__(self):
            self.calls = 0

        def search(self, *args, **kwargs):
            self.calls += 1
            return []

        def put(self, *args, **kwargs):
            self.calls += 1

    store = NoIndexStore()
    with pytest.raises(memory.SemanticIndexNotConfigured):
        memory.SemanticRuleStore(store)
    assert store.calls == 0


def test_rule_limit_error_keeps_the_conflict_code_and_is_a_value_error():
    error = memory.RuleLimitError()
    assert isinstance(error, ValueError)
    assert str(error) == "CONFLICT_RULE_LIMIT"
