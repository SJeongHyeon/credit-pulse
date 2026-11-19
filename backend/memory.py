"""사용자별 장기 기억 (G4).

테스트는 LangGraph ``BaseStore``를 주입한 ``RuleStore``로, 운영은 Postgres
트랜잭션으로 동시 생성 상한을 보장하는 ``PostgresRuleStore``로 규칙을 다룬다.
"""

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import psycopg
from langgraph.store.base import BaseStore
from psycopg.rows import dict_row

from backend.graph.injection_guard import rule_is_unsafe

logger = logging.getLogger(__name__)

MAX_RULES_PER_USER = 200
# 질문 하나에 Writer로 보내는 규칙 수(SPEC §4). 200건을 모두 보내면 의미 검색이 걸러내는 일을 하지 못한다.
SEMANTIC_RULE_TOP_K = 5
RULE_INPUT_FIELDS = frozenset({"item", "instruction", "source_memo_id"})


class RuleLimitError(ValueError):
    """사용자당 규칙 200건 상한(SPEC §4). 다시 시도해도 같은 결과라 호출 쪽이 건너뛸 수 있다."""

    def __init__(self) -> None:
        super().__init__("CONFLICT_RULE_LIMIT")


class RuleInjectionError(ValueError):
    """규칙 본문이 인젝션 신호에 걸렸다(docs/specs/rule-injection-guard.md). 규칙 본문은 오류에 싣지 않는다."""

    def __init__(self, signals: tuple[str, ...] = ()) -> None:
        super().__init__("REJECTED_RULE_INJECTION")
        self.signals = signals


class RuleNotFound(LookupError):
    """그 사용자 namespace에 규칙이 없다. 다른 사용자의 규칙인지는 구분하지 않는다(존재 여부를 숨긴다)."""


class SemanticIndexNotConfigured(RuntimeError):
    """Store에 의미 검색 인덱스가 없다. 전체 namespace 스캔으로 대체하지 않고 시작 때 멈춘다(EC-07)."""


def namespace(user_id: str) -> tuple[str, ...]:
    if not user_id.strip():
        raise ValueError("user_id must not be blank")
    return ("memories", user_id)


def _validate_rule_id(rule_id: str) -> None:
    if not rule_id.strip():
        raise ValueError("rule_id must not be blank")


def _rule_input(rule: Mapping[str, Any]) -> dict[str, str]:
    if set(rule) != RULE_INPUT_FIELDS:
        raise ValueError("INVALID_RULE_PAYLOAD")
    value = dict(rule)
    if any(not isinstance(value[field], str) or not value[field].strip() for field in RULE_INPUT_FIELDS):
        raise ValueError("INVALID_RULE_PAYLOAD")
    unsafe, signals = rule_is_unsafe(value["instruction"])
    if unsafe:
        raise RuleInjectionError(signals)
    return {field: value[field] for field in RULE_INPUT_FIELDS}


def _rule_result(rule_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    return {"rule_id": rule_id, **dict(value)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuleStore:
    """테스트용 Store 경계. 단일 프로세스의 단위 테스트에서만 사용한다."""

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    def create_rule(
        self,
        user_id: str,
        rule_id: str,
        rule: Mapping[str, Any],
    ) -> dict[str, Any]:
        user_namespace = namespace(user_id)
        _validate_rule_id(rule_id)
        value = _rule_input(rule)
        existing = self._store.get(user_namespace, rule_id)
        if existing is None and len(self.list_rules(user_id)) >= MAX_RULES_PER_USER:
            raise ValueError("CONFLICT_RULE_LIMIT")

        now = _now()
        value["created_at"] = (
            str(existing.value["created_at"]) if existing is not None else now
        )
        value["updated_at"] = now
        self._store.put(user_namespace, rule_id, value)
        return _rule_result(rule_id, value)

    def list_rules(self, user_id: str) -> list[dict[str, Any]]:
        return [
            _rule_result(item.key, item.value)
            for item in self._store.search(namespace(user_id), limit=MAX_RULES_PER_USER)
        ]

    def update_rule(
        self,
        user_id: str,
        rule_id: str,
        rule: Mapping[str, Any],
    ) -> dict[str, Any]:
        user_namespace = namespace(user_id)
        _validate_rule_id(rule_id)
        existing = self._store.get(user_namespace, rule_id)
        if existing is None:
            raise ValueError("RULE_NOT_FOUND")

        value = _rule_input(rule)
        value["created_at"] = str(existing.value["created_at"])
        value["updated_at"] = _now()
        self._store.put(user_namespace, rule_id, value)
        return _rule_result(rule_id, value)

    def delete_rule(self, user_id: str, rule_id: str) -> None:
        _validate_rule_id(rule_id)
        self._store.delete(namespace(user_id), rule_id)


class SemanticRuleStore:
    """LangGraph Store에서 사용자별 규칙을 질문으로 의미 검색한다."""

    def __init__(self, store: BaseStore) -> None:
        # InMemoryStore·PostgresStore는 인덱스가 없으면 index_config가 None이고, 그때 search(query=)는
        # 점수 없이 전체를 돌려준다. 그 결과를 '관련 규칙'으로 쓰지 않도록 만들 때 막는다.
        if not getattr(store, "index_config", None):
            raise SemanticIndexNotConfigured(
                "Store에 시멘틱 인덱스가 없습니다. 앱 조립 지점에서 index 설정(embed, dims)을 넣어 주세요"
            )
        self._store = store

    def search_rules(self, user_id: str, question: str) -> list[dict[str, Any]]:
        # 저장된 규칙이 하나도 없으면 의미 검색을 하지 않는다. 의미 검색은 질문을 임베딩하는 외부 호출(약 1.3초)이고,
        # 그래프 맨 앞에서 차례로 돌아 그동안 검색·주제 분류가 시작하지 못한다(11/11 배포 서비스 트레이스).
        # query 없는 search는 임베딩 없이 DB만 본다. 결과는 같다(규칙이 없으면 의미 검색도 빈 목록).
        if not self._store.search(namespace(user_id), limit=1):
            return []
        results = self._store.search(
            namespace(user_id),
            query=question,
            limit=SEMANTIC_RULE_TOP_K,
        )
        return [_rule_result(item.key, item.value) for item in results]

    def list_rules(self, user_id: str) -> list[dict[str, Any]]:
        """로그인 사용자 namespace의 규칙만 최대 200건(SPEC §4 사용자당 상한). 질문 없이 목록으로 읽는다."""

        results = self._store.search(namespace(user_id), limit=MAX_RULES_PER_USER)
        return [_rule_result(item.key, item.value) for item in results]

    def delete_rule(self, user_id: str, rule_id: str) -> None:
        """그 사용자 namespace에 있는 규칙만 지운다. 없으면 RuleNotFound(다른 사용자 규칙도 같은 결과)."""

        user_namespace = namespace(user_id)
        if not rule_id.strip() or self._store.get(user_namespace, rule_id) is None:
            raise RuleNotFound()
        self._store.delete(user_namespace, rule_id)

    def save_edited_rules(
        self,
        user_id: str,
        memo_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        user_namespace = namespace(user_id)
        _validate_rule_id(memo_id)
        for item in items:
            if item.get("edited") is not True:
                continue
            try:
                rule = _rule_input(
                    {
                        "item": item.get("item", ""),
                        "instruction": item.get("reason", ""),
                        "source_memo_id": memo_id,
                    }
                )
            except RuleInjectionError as exc:
                # 악성 규칙 하나가 승인·다른 규칙 저장을 막지 않는다. 본문은 로그에 남기지 않는다.
                logger.warning(
                    "인젝션 의심 규칙을 저장하지 않았습니다: memo_id=%s item_len=%d signals=%s",
                    memo_id, len(str(item.get("item", ""))), ",".join(exc.signals),
                )
                continue
            rule_id = f"{memo_id}:{rule['item']}"
            existing = self._store.get(user_namespace, rule_id)
            if existing is None and len(
                self._store.search(user_namespace, limit=MAX_RULES_PER_USER)
            ) >= MAX_RULES_PER_USER:
                raise RuleLimitError()
            self._store.put(user_namespace, rule_id, rule)


class PostgresRuleStore:
    """운영용 규칙 저장소. 사용자별 트랜잭션 잠금으로 200건 상한을 보장한다."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def setup(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_memory_rules (
                    user_id TEXT NOT NULL CHECK (char_length(btrim(user_id)) > 0),
                    rule_id TEXT NOT NULL CHECK (char_length(btrim(rule_id)) > 0),
                    item TEXT NOT NULL CHECK (char_length(btrim(item)) > 0),
                    instruction TEXT NOT NULL CHECK (char_length(btrim(instruction)) > 0),
                    source_memo_id TEXT NOT NULL CHECK (char_length(btrim(source_memo_id)) > 0),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (user_id, rule_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_memory_rules_user_id "
                "ON user_memory_rules (user_id)"
            )

    def create_rule(
        self,
        user_id: str,
        rule_id: str,
        rule: Mapping[str, Any],
    ) -> dict[str, Any]:
        namespace(user_id)
        _validate_rule_id(rule_id)
        value = _rule_input(rule)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._lock_user(cursor, user_id)
                cursor.execute(
                    "SELECT created_at FROM user_memory_rules WHERE user_id = %s AND rule_id = %s",
                    (user_id, rule_id),
                )
                existing = cursor.fetchone()
                if existing is None:
                    cursor.execute(
                        "SELECT count(*) AS rule_count FROM user_memory_rules WHERE user_id = %s",
                        (user_id,),
                    )
                    if cursor.fetchone()["rule_count"] >= MAX_RULES_PER_USER:
                        raise ValueError("CONFLICT_RULE_LIMIT")
                    cursor.execute(
                        """
                        INSERT INTO user_memory_rules
                            (user_id, rule_id, item, instruction, source_memo_id)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING rule_id, item, instruction, source_memo_id, created_at, updated_at
                        """,
                        (user_id, rule_id, value["item"], value["instruction"], value["source_memo_id"]),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE user_memory_rules
                        SET item = %s, instruction = %s, source_memo_id = %s, updated_at = now()
                        WHERE user_id = %s AND rule_id = %s
                        RETURNING rule_id, item, instruction, source_memo_id, created_at, updated_at
                        """,
                        (value["item"], value["instruction"], value["source_memo_id"], user_id, rule_id),
                    )
                return self._row_result(cursor.fetchone())

    def list_rules(self, user_id: str) -> list[dict[str, Any]]:
        namespace(user_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT rule_id, item, instruction, source_memo_id, created_at, updated_at
                FROM user_memory_rules
                WHERE user_id = %s
                ORDER BY created_at, rule_id
                LIMIT %s
                """,
                (user_id, MAX_RULES_PER_USER),
            ).fetchall()
        return [self._row_result(row) for row in rows]

    def update_rule(
        self,
        user_id: str,
        rule_id: str,
        rule: Mapping[str, Any],
    ) -> dict[str, Any]:
        namespace(user_id)
        _validate_rule_id(rule_id)
        value = _rule_input(rule)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._lock_user(cursor, user_id)
                cursor.execute(
                    """
                    UPDATE user_memory_rules
                    SET item = %s, instruction = %s, source_memo_id = %s, updated_at = now()
                    WHERE user_id = %s AND rule_id = %s
                    RETURNING rule_id, item, instruction, source_memo_id, created_at, updated_at
                    """,
                    (value["item"], value["instruction"], value["source_memo_id"], user_id, rule_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("RULE_NOT_FOUND")
                return self._row_result(row)

    def delete_rule(self, user_id: str, rule_id: str) -> None:
        namespace(user_id)
        _validate_rule_id(rule_id)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._lock_user(cursor, user_id)
                cursor.execute(
                    "DELETE FROM user_memory_rules WHERE user_id = %s AND rule_id = %s",
                    (user_id, rule_id),
                )

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(
            self._database_url,
            prepare_threshold=None,
            row_factory=dict_row,
        )

    @staticmethod
    def _lock_user(cursor: Any, user_id: str) -> None:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (user_id,))

    @staticmethod
    def _row_result(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "rule_id": row["rule_id"],
            "item": row["item"],
            "instruction": row["instruction"],
            "source_memo_id": row["source_memo_id"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }
