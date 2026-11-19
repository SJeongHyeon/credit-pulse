"""사람 승인 interrupt → 프로세스 종료 → 새 프로세스에서 Postgres 체크포인트로 재개하는 시연(D5).

키·검색 산출물 없이 돈다(가짜 검색기·작성기). 단계마다 따로 실행해 '재시작해도 이어서 재개'를 보인다.
  python -X utf8 scripts/approval_resume_demo.py --step pause   --review-id demo-1
  python -X utf8 scripts/approval_resume_demo.py --step resume  --review-id demo-1
  python -X utf8 scripts/approval_resume_demo.py --step resume  --review-id demo-1   # 재전송 → 409 상당
  python -X utf8 scripts/approval_resume_demo.py --step count   --review-id demo-1

DB는 CHECKPOINT_DATABASE_URL만 읽는다(.env의 DATABASE_URL은 운영 DB일 수 있어 쓰지 않는다).
메모는 시연용 표 demo_review_memos에 UNIQUE(review_id, approval_seq)로 멱등 저장한다(2주11: DB UNIQUE).
실서비스 표(review_memos)는 D6 서비스화에서 만든다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.postgres import PostgresSaver  # noqa: E402
from psycopg import Connection  # noqa: E402

from backend.graph.approval import NotAwaitingApproval, pending_approval, resume_approval, thread_id_for  # noqa: E402
from backend.graph.build import build_graph, build_supervisor_graph  # noqa: E402
from backend.graph.refusal import CorpusCatalog, CorpusTarget  # noqa: E402

QUESTION = "오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요."
USER_ID = "demo-user"
CATALOG = CorpusCatalog(
    corp_names=frozenset({"오뚜기"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset({CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}),
)
CHUNK = {
    "chunk_id": "chunk-demo-1",
    "corp_code": "00000001",
    "fiscal_year": 2025,
    "content": "단기차입금 1,234백만원. 계류 중인 소송 없음",
    "rcept_no": "20260304000913",
    "chapter": "NOTES",
    "note_no": "1",
}
CALLS = {"retriever": 0, "writer": 0}
MEMO_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS demo_review_memos (
    id BIGSERIAL PRIMARY KEY,
    review_id TEXT NOT NULL,
    approval_seq INTEGER NOT NULL,
    approved_by TEXT NOT NULL,
    items JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (review_id, approval_seq)
)
"""


def _retriever(_state):
    CALLS["retriever"] += 1
    return [CHUNK]


def _writer(_state, _chunks):
    CALLS["writer"] += 1
    return [
        {"item": "litigation", "verdict": "not_applicable", "reason": "계류 중인 소송 없음", "chunk_ids": ["chunk-demo-1"]},
        {"item": "borrowing_structure", "verdict": "applicable", "reason": "단기차입금 1,234백만원", "chunk_ids": ["chunk-demo-1"]},
    ]


def _memo_saver(conn: Connection):
    def save(record: dict, key: tuple) -> str:
        user_id, review_id, approval_seq = key
        row = conn.execute(
            "INSERT INTO demo_review_memos (review_id, approval_seq, approved_by, items) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (review_id, approval_seq) DO NOTHING RETURNING id",
            (review_id, approval_seq, record["approved_by"], json.dumps(record["items"], ensure_ascii=False)),
        ).fetchone()
        if row is None:  # 같은 멱등키 → 기존 메모 id(새 행을 만들지 않음)
            row = conn.execute(
                "SELECT id FROM demo_review_memos WHERE review_id = %s AND approval_seq = %s", (review_id, approval_seq)
            ).fetchone()
        return f"memo-{row['id'] if isinstance(row, dict) else row[0]}"

    return save


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--step", choices=("pause", "resume", "count"), required=True)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--graph", choices=("single", "multi"), default="multi")
    args = parser.parse_args(argv)

    url = os.environ.get("CHECKPOINT_DATABASE_URL")
    if not url:
        print("CHECKPOINT_DATABASE_URL이 필요합니다(운영 DB가 아닌 시험용 Postgres)", file=sys.stderr)
        return 2
    config = {"configurable": {"thread_id": thread_id_for(USER_ID, args.review_id)}}
    builder = build_supervisor_graph if args.graph == "multi" else build_graph

    with PostgresSaver.from_conn_string(url) as saver, Connection.connect(url, autocommit=True) as conn:
        saver.setup()  # 처음 1회 체크포인트 표를 만든다(이미 있으면 그대로)
        conn.execute(MEMO_TABLE_SQL)
        if args.step == "count":
            n = conn.execute("SELECT count(*) FROM demo_review_memos WHERE review_id = %s", (args.review_id,)).fetchone()[0]
            print(json.dumps({"step": "count", "review_id": args.review_id, "memo_rows": n}, ensure_ascii=False))
            return 0
        graph = builder(
            retriever=_retriever,
            draft_writer=_writer,
            corpus_catalog=CATALOG,
            checkpointer=saver,
            memo_saver=_memo_saver(conn),
            approval_enabled=True,
        )
        evidence: dict = {"step": args.step, "graph": args.graph, "pid": os.getpid(), "thread_id": config["configurable"]["thread_id"]}
        if args.step == "pause":
            result = graph.invoke(
                {"question": QUESTION, "user_id": USER_ID, "review_id": args.review_id,
                 "checklist_items": ["litigation", "borrowing_structure"]},
                config=config,
            )
            payload = result["__interrupt__"][0].value
            evidence.update({
                "interrupted": True,
                "approval_seq": payload["approval_seq"],
                "draft_items": [(d["item"], d["verdict"]) for d in payload["draft_items"]],
                "status": graph.get_state(config).values["status"],
                "next": list(graph.get_state(config).next),
            })
        else:
            payload = pending_approval(graph, config)
            if payload is None:
                try:
                    resume_approval(graph, config, {"approval_seq": 1, "decisions": []})
                except NotAwaitingApproval as exc:
                    evidence.update({"resumed": False, "error": "CONFLICT_NOT_AWAITING_APPROVAL", "message": str(exc)})
            else:
                decisions = [{"item": d["item"], "verdict": d["verdict"], "reason": d["reason"]} for d in payload["draft_items"]]
                # 심사역이 차입금 항목만 고쳐서 승인했다고 가정(고친 항목 표시 확인용)
                decisions[1] = {**decisions[1], "verdict": "needs_check", "reason": "만기 구조는 원문 확인 필요"}
                done = resume_approval(graph, config, {"approval_seq": payload["approval_seq"], "decisions": decisions})
                evidence.update({
                    "resumed": True,
                    "saved_memo_id": done["saved_memo_id"],
                    "status": done["status"],
                    "edited": [d["edited"] for d in done["approval_decisions"]],
                    "last_events": done["node_events"][-3:],
                })
        evidence["calls_in_this_process"] = dict(CALLS)
    print(json.dumps(evidence, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
