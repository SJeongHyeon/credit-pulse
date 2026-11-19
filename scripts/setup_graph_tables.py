"""운영 DB에 심사 그래프용 표를 한 번 만든다: LangGraph 체크포인터·Store 표, 누적 LLM 비용 표.

Supabase 트랜잭션 풀러(6543)는 표 생성·마이그레이션에 맞지 않으므로 직접 연결(5432) 주소로 실행한다.
서버 시작 때는 표를 만들지 않는다(여러 인스턴스가 동시에 마이그레이션하지 않게).

  DATABASE_URL_DIRECT=postgresql://...:5432/postgres python -X utf8 scripts/setup_graph_tables.py

이미 있으면 건너뛰므로 여러 번 실행해도 된다. 키(임베딩)는 필요 없다: 표만 만들고 임베딩은 부르지 않는다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.api.limits import limit_setup_statements  # noqa: E402
from backend.api.runtime import EMBEDDING_DIMS, RULE_INDEX_FIELDS  # noqa: E402


def _no_embedding(texts):  # 표 생성만 한다. 호출되면 설정이 잘못된 것이다.
    raise RuntimeError("setup 중에는 임베딩을 부르지 않습니다")


def main() -> int:
    url = os.getenv("DATABASE_URL_DIRECT", "").strip()
    if not url:
        print("DATABASE_URL_DIRECT(직접 연결 5432 주소)가 필요합니다", file=sys.stderr)
        return 2
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.store.postgres import PostgresStore
    from psycopg import Connection
    from psycopg.rows import dict_row

    with Connection.connect(url, autocommit=True, prepare_threshold=None, row_factory=dict_row) as connection:
        PostgresSaver(connection).setup()
        PostgresStore(
            connection, index={"dims": EMBEDDING_DIMS, "embed": _no_embedding, "fields": RULE_INDEX_FIELDS}
        ).setup()
        for statement in limit_setup_statements():
            connection.execute(statement)
    print("체크포인터·Store·daily_usage 표 준비 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
