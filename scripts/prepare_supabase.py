"""운영 DB(Supabase)를 한 번 준비한다. 배포 담당이 직접 연결(5432) 주소로 실행한다.

  $env:DATABASE_URL_DIRECT = "postgresql://...:5432/postgres"   # 저장소에 적지 않는다
  python -X utf8 scripts/prepare_supabase.py                              # 무엇을 할지 보여 주기만 한다
  python -X utf8 scripts/prepare_supabase.py --apply                      # 실제로 바꾼다(데모 스냅샷 16사)
  python -X utf8 scripts/prepare_supabase.py --service-corpus             # 서비스 100사 적재 계획만
  python -X utf8 scripts/prepare_supabase.py --service-corpus --apply     # 서비스 100사 적재(사전 등록 90번)

하는 일(여러 번 실행해도 결과가 같다):
  0. 프로젝트 스키마(db/01_init.sql·02_schema.sql·03_llm_budget.sql, 모두 IF NOT EXISTS)를 적용해 없는 표를 만든다.
     02는 HNSW 인덱스 idx_chunks_embedding을 지운다(정확 검색, ARCHITECTURE 결정 기록 11/7)
  1. 심사 그래프 표: LangGraph 체크포인터·Store, 누적 비용(llm_budget)  ← scripts/setup_graph_tables.py와 같음
  2. chunks.embedding 차원이 1536이 아니면: chunks·corpus_versions를 비우고 VECTOR(1536)으로 바꾼다
  3. 코퍼스 적재
     - 기본: 데모 스냅샷(data/snapshot, 16사)  ← compose loader와 같은 내용
     - --service-corpus: 서비스 100사(data/service/의 chunks·벡터, corpus_id가 같은 manifest)를 load_pgvector()로
       적재하고, 행 수를 확인한 뒤 다른 corpus_id를 지운다(CONTRACTS: Supabase에는 활성 corpus_id 한 벌만)
  4. documents에 100사 문서 행을 넣는다(심사 건 생성이 회사를 여기서 찾는다). 있는 행은 그대로 둔다
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

TARGET_DIM = 1536
SCHEMA_FILES = [ROOT / "db" / name for name in ("01_init.sql", "02_schema.sql", "03_llm_budget.sql")]
PROJECT_TABLES = ("documents", "chunks", "users", "reviews", "review_memos", "llm_budget")
# 서비스 100사: data/service/는 scripts/prepare_service_data.py가 만든다. chunks의 corpus_id(ffc1…)와 같은
# manifest는 평가 v9 실험 코퍼스의 것이다(커밋된 data/manifest.jsonl은 collected_at만 달라 corpus_id가 다름).
SERVICE_CHUNKS = Path("data/service/chunks.jsonl")
SERVICE_EMBEDDING_PREFIX = "data/service/embeddings"
SERVICE_MANIFEST = Path("data/experiments/b100-v9-fixed_window/manifest.jsonl")


def _inspect(connection) -> tuple[str | None, dict[str, int | None], list[str]]:
    """chunks.embedding 타입, 프로젝트 표별 행 수(표가 없으면 None), corpus_versions의 corpus_id 목록."""

    existing = {row[0] for row in connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()}
    counts = {table: (connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] if table in existing else None)
              for table in PROJECT_TABLES}
    current = None
    if "chunks" in existing:
        row = connection.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
        ).fetchone()
        current = row[0] if row else None
    corpus_ids = []
    if "corpus_versions" in existing:
        corpus_ids = [row[0] for row in connection.execute(
            "SELECT corpus_id FROM corpus_versions ORDER BY corpus_id").fetchall()]
    return current, counts, corpus_ids


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _service_corpus(manifest_path: Path) -> tuple[str, int]:
    """서비스 적재 입력의 corpus_id와 청크 수. manifest와 chunks의 corpus_id가 다르면 멈춘다."""

    missing = [str(path) for path in (SERVICE_CHUNKS, manifest_path, Path(f"{SERVICE_EMBEDDING_PREFIX}.vectors.npy"))
               if not path.exists()]
    if missing:
        raise SystemExit(f"서비스 적재 입력이 없습니다: {', '.join(missing)} (scripts/prepare_service_data.py 먼저)")
    manifest_ids = {str(row["corpus_id"]) for row in _read_jsonl(manifest_path)}
    chunk_ids: set[str] = set()
    chunk_count = 0
    with SERVICE_CHUNKS.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                chunk_ids.add(str(json.loads(line)["corpus_id"]))
                chunk_count += 1
    if len(manifest_ids) != 1 or manifest_ids != chunk_ids:
        raise SystemExit(f"corpus_id 불일치: manifest {sorted(manifest_ids)}, chunks {sorted(chunk_ids)}")
    return next(iter(manifest_ids)), chunk_count


def _run_load_pgvector(load_pgvector, url: str, manifest_path: Path) -> dict:
    result = load_pgvector(
        manifest_path,
        SERVICE_CHUNKS,
        f"{SERVICE_EMBEDDING_PREFIX}.ids.jsonl",
        f"{SERVICE_EMBEDDING_PREFIX}.vectors.npy",
        f"{SERVICE_EMBEDDING_PREFIX}.metadata.json",
        database_url=url,
    )
    print(f"적재: {json.dumps(result, ensure_ascii=False)}")
    return result


def _load_service_corpus(url: str, manifest_path: Path, corpus_id: str, chunk_count: int) -> int:
    """서비스 100사를 적재하고 확인한 뒤 다른 corpus_id를 지운다. 성공이면 0."""

    import hashlib

    import psycopg

    from backend.rag.vector_store import load_pgvector

    chunks_sha256 = hashlib.sha256(SERVICE_CHUNKS.read_bytes()).hexdigest()
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as connection:
        loaded = connection.execute(
            "SELECT v.chunks_sha256, (SELECT count(*) FROM chunks c WHERE c.corpus_id = v.corpus_id) "
            "FROM corpus_versions v WHERE v.corpus_id = %s", (corpus_id,)).fetchone()
    if loaded and loaded[0] == chunks_sha256 and loaded[1] == chunk_count:
        # 같은 코퍼스를 다시 upsert하면 행마다 새 버전이 생겨 표가 커진다(로컬 실측: 두 번 더 돌리자 313 → 666MB,
        # 지운 공간은 디스크로 돌아가지 않음). Supabase 무료 500MB 안에 두려고 이미 적재된 것은 건너뛴다.
        print(f"이미 적재됨(같은 chunks SHA-256·{chunk_count}청크): 적재를 건너뜁니다")
        result = {"corpus_id": corpus_id, "chunks_stored": chunk_count}
    else:
        result = _run_load_pgvector(load_pgvector, url, manifest_path)
    if result["corpus_id"] != corpus_id or result["chunks_stored"] != chunk_count:
        # 확인 전에는 이전 코퍼스를 지우지 않는다(지금 서비스가 쓰는 것일 수 있다).
        print(f"적재 확인 실패: 기대 {corpus_id}·{chunk_count}청크, 실제 {result['corpus_id']}·{result['chunks_stored']}청크",
              file=sys.stderr)
        return 1
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as connection:
        # 외래키 ON DELETE CASCADE로 이전 corpus_id의 chunks도 함께 지워진다(CONTRACTS 운영 코퍼스 교체 규칙).
        deleted = connection.execute("DELETE FROM corpus_versions WHERE corpus_id <> %s", (corpus_id,)).rowcount
        remaining = connection.execute("SELECT count(*) FROM corpus_versions").fetchone()[0]
        stored = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
    print(f"이전 corpus_id {deleted}개 삭제, corpus_versions {remaining}행, chunks {stored}행")
    if remaining != 1 or stored != chunk_count:
        print("활성 코퍼스 한 벌만 남지 않았습니다", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="운영 DB 준비(한 번)")
    parser.add_argument("--apply", action="store_true", help="실제로 바꾼다(없으면 계획만 출력)")
    parser.add_argument("--service-corpus", action="store_true",
                        help="데모 스냅샷 대신 서비스 100사(data/service/)를 적재하고 다른 corpus_id를 지운다")
    parser.add_argument("--service-manifest", type=Path, default=SERVICE_MANIFEST,
                        help=f"서비스 chunks와 corpus_id가 같은 manifest (기본 {SERVICE_MANIFEST})")
    args = parser.parse_args(argv)
    url = os.getenv("DATABASE_URL_DIRECT", "").strip()
    if not url:
        print("DATABASE_URL_DIRECT(직접 연결 5432 주소)가 필요합니다", file=sys.stderr)
        return 2

    import psycopg

    service = _service_corpus(args.service_manifest) if args.service_corpus else None
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as connection:
        current, counts, corpus_ids = _inspect(connection)
    manifest = _read_jsonl(Path("data/manifest.jsonl"))
    missing = [table for table, count in counts.items() if count is None]
    print(f"현재: chunks.embedding={current}, 행 수(None은 표 없음)={counts}, corpus_versions={corpus_ids}")
    print(f"계획: 0) {'없는 표 만들기: ' + ', '.join(missing) if missing else '프로젝트 표 모두 있음'} (db/01~03, IF NOT EXISTS)"
          " + HNSW 인덱스 삭제")
    print("      1) 그래프 표 준비")
    stale = current is not None and current != f"vector({TARGET_DIM})"
    print(f"      2) {'chunks 비우고 vector(1536)으로 변경' if stale else '차원 변경 필요 없음'}")
    if service:
        corpus_id, chunk_count = service
        others = [cid for cid in corpus_ids if cid != corpus_id]
        print(f"      3) 서비스 100사 적재: corpus_id={corpus_id}, {chunk_count}청크 → 확인 뒤 다른 corpus_id 삭제: {others or '없음'}")
    else:
        print("      3) 데모 스냅샷 16사 적재")
    print(f"      4) documents에 manifest {len(manifest)}건 넣기(있는 행 유지)")
    if not args.apply:
        print("바꾸려면 --apply를 붙여 다시 실행하세요")
        return 0

    from backend.api.limits import sql_statements
    from scripts.setup_graph_tables import main as setup_tables

    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as connection:
        for path in SCHEMA_FILES:
            for statement in sql_statements(path):
                connection.execute(statement)
        current, _, _ = _inspect(connection)
    print(f"스키마 준비 완료(chunks.embedding={current})")
    needs_migration = current != f"vector({TARGET_DIM})"
    os.environ.setdefault("DATABASE_URL_DIRECT", url)
    if setup_tables() != 0:
        return 1
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as connection:
        if needs_migration:
            with connection.transaction():
                connection.execute("TRUNCATE chunks")
                connection.execute("DELETE FROM corpus_versions")
                connection.execute(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE VECTOR({TARGET_DIM})")
            print(f"chunks.embedding → vector({TARGET_DIM})")

    os.environ["DATABASE_URL"] = url
    os.environ["EMBED_DIM"] = str(TARGET_DIM)
    if service:
        if _load_service_corpus(url, args.service_manifest, *service) != 0:
            return 1
    else:
        from backend.rag.snapshot import main as load_snapshot

        if load_snapshot(["--dir", "data/snapshot"]) not in (0, None):
            return 1

    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as connection:
        inserted = 0
        for row in manifest:
            result = connection.execute(
                "INSERT INTO documents (rcept_no, corp_code, corp_name, fiscal_year, report_type, filed_at, status, collected_at, raw_sha256) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (rcept_no) DO NOTHING",
                (row["rcept_no"], row["corp_code"], row["corp_name"], row["fiscal_year"], row["report_type"],
                 row["filed_at"], "superseded" if row.get("superseded") else "active", row["collected_at"], row["raw_sha256"]),
            )
            inserted += result.rowcount
        total = connection.execute("SELECT count(*), count(DISTINCT corp_code) FROM documents WHERE fiscal_year = 2025").fetchone()
    print(f"documents: 새로 {inserted}건, FY2025 {total[0]}건·{total[1]}사")
    if service:
        print(f"완료. Cloud Run 환경변수: EMBED_DIM=1536, VECTOR_BACKEND=pgvector, RETRIEVAL_CORPUS_ID={service[0]}")
    else:
        print("완료. Cloud Run 환경변수 EMBED_DIM=1536으로 배포하세요")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
