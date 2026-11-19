"""G4 시연: 승인에서 고친 판정 규칙이 그 사용자의 Store namespace에만 저장되고, 다음 질문에서 그 사용자에게만 쓰이는지 보인다.

운영과 같은 부품을 그대로 쓴다.
- Store: LangGraph PostgresStore + 의미 검색 색인(`runtime.rule_index_config`, OpenAI 3-small 1,536차원)
- 저장: 승인 그래프의 `save_memo` 노드(`backend/graph/approval.py`), 고친 항목만 규칙으로 저장
- 읽기: 질문 첫머리의 `load_rules` 노드(`backend/graph/build.py`), 작성기 입력 `applied_rules`가 된다

메모 저장(DB의 review_memos)만 가짜 함수로 바꾼다. 이 시연은 Store 격리만 본다.

실행(빈 pgvector DB와 OPENAI_API_KEY 필요, 임베딩 호출 4번 정도):
    $env:STORE_DEMO_DATABASE_URL = "postgresql://postgres:postgres@localhost:55433/postgres"
    python -X utf8 scripts/demo_store_isolation.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True), override=False)
    database_url = os.environ.get("STORE_DEMO_DATABASE_URL", "").strip()
    if not database_url:
        print("STORE_DEMO_DATABASE_URL이 없습니다. 운영 DB가 아닌 빈 pgvector DB 주소를 넣으세요", file=sys.stderr)
        return 2
    if "supabase" in database_url:
        print("운영 DB(Supabase)에는 실행하지 않습니다", file=sys.stderr)
        return 2

    from langgraph.store.postgres import PostgresStore

    from backend.api.runtime import open_pool, rule_index_config
    from backend.graph.approval import save_memo_node
    from backend.graph.build import _load_rules_node
    from backend.memory import SemanticRuleStore, namespace
    from backend.rag.embedding import create_embedder

    embedder = create_embedder("openai/text-embedding-3-small")
    pool = open_pool(database_url)
    store = PostgresStore(pool, index=rule_index_config(embedder))
    store.setup()
    memory_store = SemanticRuleStore(store)

    suffix = uuid4().hex[:8]
    user_a, user_b = f"demo-user-a-{suffix}", f"demo-user-b-{suffix}"

    def fake_memo_saver(record: dict, idempotency_key: tuple) -> str:
        return f"memo-{record['user_id']}-{record['approval_seq']}"

    save_memo = save_memo_node(fake_memo_saver, approval_enabled=True, memory_store=memory_store)
    load_rules = _load_rules_node(rule_store=None, memory_store=memory_store)

    # 1) 사용자 A가 승인하면서 차입금 항목의 판정 이유를 고쳤다(edited=True만 규칙이 된다)
    save_memo({
        "user_id": user_a, "review_id": "review-a", "approval_seq": 1, "status": "saving",
        "approval_decisions": [
            {"item": "단기차입금", "verdict": "applicable", "edited": True,
             "reason": "단기차입금은 연결 기준 주석의 차입금 명세 표에서 확인하고 유동성장기부채는 따로 적는다"},
            {"item": "소송", "verdict": "not_applicable", "edited": False, "reason": "계류 소송 없음"},
        ],
    })
    # 2) 사용자 B도 자기 규칙 하나를 남겼다
    save_memo({
        "user_id": user_b, "review_id": "review-b", "approval_seq": 1, "status": "saving",
        "approval_decisions": [
            {"item": "배당", "verdict": "needs_check", "edited": True,
             "reason": "배당은 현금배당성향과 주당배당금을 함께 확인한다"},
        ],
    })

    question = "오뚜기의 2025년 말 연결 기준 단기차입금은 얼마인가요?"
    report = {
        "question": question,
        "stored": {
            "A": [item.key for item in store.search(namespace(user_a), limit=10)],
            "B": [item.key for item in store.search(namespace(user_b), limit=10)],
        },
        "applied_rules": {
            "A": load_rules({"user_id": user_a, "question": question})["applied_rules"],
            "B": load_rules({"user_id": user_b, "question": question})["applied_rules"],
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    a_items = {rule["item"] for rule in report["applied_rules"]["A"]}
    b_items = {rule["item"] for rule in report["applied_rules"]["B"]}
    ok = a_items == {"단기차입금"} and b_items == {"배당"}
    print("격리 확인:", "통과" if ok else "실패")
    for user_id in (user_a, user_b):
        for item in store.search(namespace(user_id), limit=10):
            store.delete(namespace(user_id), item.key)
    pool.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
