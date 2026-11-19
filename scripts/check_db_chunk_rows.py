"""운영 DB(pgvector)의 청크 행 수를 읽기 전용으로 조회해 기록한다(D10, 11/12 리뷰 코멘트:
'번들의 corpus_id와 DB의 활성 코퍼스가 달라 DB 행 수 조회 기록이 필요합니다').

하는 일(SELECT만 한다. 쓰기 없음)
  1. `corpus_versions`의 행(어떤 코퍼스가 적재돼 있는가)
  2. `chunks`를 corpus_id별로: 행 수, 고유 본문 해시 수(content_sha256), 문서 수, 벡터가 있는 행 수
  3. DB의 고유 본문 해시 집합이 저장소의 해시 목록(`data/unique_chunk_hashes.100-b-v9.txt`, 31,953줄)과 같은지

DB 주소는 환경 변수 `SUPABASE_DATABASE_URL`에서 읽고(없으면 `DATABASE_URL`, 그다음 저장소 루트의 `.env`), 출력·기록에 남기지 않는다.

사용: python -X utf8 scripts/check_db_chunk_rows.py [--output docs/evidence/d10/db_chunk_rows.json]

열람 방법(평가자): 운영 DB 접속 정보는 저장소에 없다. 같은 본문은 `data/g3/chunks.100-b-v9.jsonl.gz`로 저장소에 있고
`python -X utf8 scripts/verify_g3_bundle.py`가 행 수·고유 수를 다시 센다. 이 스크립트의 3번이 DB와 그 묶음이 같은 본문임을 잇는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASH_LIST = ROOT / "data" / "unique_chunk_hashes.100-b-v9.txt"


def database_url() -> str:
    """운영 DB 주소. 운영(Supabase)은 SUPABASE_DATABASE_URL이고, DATABASE_URL은 로컬 compose DB라 나중에 본다."""
    file_values = {}
    if (ROOT / ".env").exists():
        from dotenv import dotenv_values

        file_values = dotenv_values(ROOT / ".env")
    for name in ("SUPABASE_DATABASE_URL", "DATABASE_URL"):
        url = (os.environ.get(name) or str(file_values.get(name) or "")).strip()
        if url:
            return url
    raise SystemExit("SUPABASE_DATABASE_URL(또는 DATABASE_URL)이 없습니다")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output")
    args = parser.parse_args()

    import psycopg

    listed = set(HASH_LIST.read_text(encoding="utf-8").split())
    queries = {
        "corpus_versions": "SELECT * FROM corpus_versions ORDER BY 1",
        "chunks_by_corpus": ("SELECT corpus_id, count(*) AS rows, count(DISTINCT content_sha256) AS unique_contents, "
                             "count(DISTINCT rcept_no) AS documents FROM chunks GROUP BY corpus_id ORDER BY rows DESC"),
    }
    record: dict = {"what": "운영 DB(pgvector)의 chunks 표 조회(읽기 전용)", "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "queries": queries}
    with psycopg.connect(database_url(), connect_timeout=30) as conn:
        conn.read_only = True
        columns = [row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'chunks' ORDER BY ordinal_position")]
        record["chunks_columns"] = columns
        if "rcept_no" not in columns:  # 문서 열 이름이 다르면 문서 수는 세지 않는다
            queries["chunks_by_corpus"] = queries["chunks_by_corpus"].replace("count(DISTINCT rcept_no) AS documents", "NULL AS documents")
        cursor = conn.execute(queries["corpus_versions"])
        names = [d.name for d in cursor.description]
        record["corpus_versions"] = [{k: (str(v) if v is not None and not isinstance(v, (int, float, bool)) else v) for k, v in zip(names, row)} for row in cursor.fetchall()]
        record["chunks_by_corpus"] = [dict(zip(("corpus_id", "rows", "unique_contents", "documents"), row)) for row in conn.execute(queries["chunks_by_corpus"])]
        vector_column = next((c for c in columns if c in ("embedding", "vector")), None)
        if vector_column:
            record["rows_with_vector"] = conn.execute(f"SELECT count(*) FROM chunks WHERE {vector_column} IS NOT NULL").fetchone()[0]
        db_hashes = {row[0].strip() for row in conn.execute("SELECT DISTINCT content_sha256 FROM chunks")}
        record["server_version"] = conn.execute("SHOW server_version").fetchone()[0]
    record["hash_list"] = {"path": "data/unique_chunk_hashes.100-b-v9.txt", "lines": len(listed)}
    record["db_unique_hashes"] = len(db_hashes)
    record["db_hashes_equal_hash_list"] = db_hashes == listed
    record["only_in_db"] = len(db_hashes - listed)
    record["only_in_hash_list"] = len(listed - db_hashes)
    text = json.dumps(record, ensure_ascii=False, indent=2, default=str)
    if args.output:
        (ROOT / args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0 if record["db_hashes_equal_hash_list"] else 1


if __name__ == "__main__":
    sys.exit(main())
