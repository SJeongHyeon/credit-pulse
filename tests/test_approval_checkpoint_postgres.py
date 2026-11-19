"""Postgres 체크포인트로 '프로세스를 다시 띄워도' 승인을 이어서 재개하는지 (approval-checkpoint AC-09).

멈춤·재개·재전송·집계를 서로 다른 프로세스로 실행한다(scripts/approval_resume_demo.py).
CHECKPOINT_DATABASE_URL(시험용 Postgres)이 있을 때만 돈다. .env의 DATABASE_URL(운영 DB일 수 있음)은 쓰지 않는다.
  docker run -d --rm --name proj3-ckpt-test -e POSTGRES_PASSWORD=ckpt -e POSTGRES_DB=ckpt -p 55432:5432 postgres:17
  CHECKPOINT_DATABASE_URL=postgresql://postgres:ckpt@localhost:55432/ckpt pytest tests/test_approval_checkpoint_postgres.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

URL = os.getenv("CHECKPOINT_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="CHECKPOINT_DATABASE_URL(시험용 Postgres)이 있을 때만 실행")
ROOT = Path(__file__).resolve().parents[1]


def _run(step: str, review_id: str, graph: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "scripts/approval_resume_demo.py"),
         "--step", step, "--review-id", review_id, "--graph", graph],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, "CHECKPOINT_DATABASE_URL": URL},
        cwd=ROOT, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("graph", ["single", "multi"])
def test_resume_in_new_process_saves_once_without_rerunning_search(graph):
    review_id = f"pytest-{graph}-{uuid4().hex[:8]}"

    paused = _run("pause", review_id, graph)
    resumed = _run("resume", review_id, graph)
    again = _run("resume", review_id, graph)
    count = _run("count", review_id, graph)

    assert paused["interrupted"] is True and paused["next"] == ["human_approval"]
    assert paused["status"] == "awaiting_approval"
    # 멀티는 112번부터 초안 2개(보통·엄격)를 함께 쓰므로 작성 호출이 2회다. 이 시험은 DB가 있을 때만 돌아
    # 그동안 1회 기대값이 낡은 채 남아 있었다(11/12 D10 점검에서 DB를 띄워 돌리다 찾음).
    assert paused["calls_in_this_process"] == {"retriever": 1, "writer": 2 if graph == "multi" else 1}
    assert paused["pid"] != resumed["pid"]  # 다른 프로세스
    assert resumed["resumed"] is True and resumed["status"] == "open"
    # 재개한 프로세스는 검색·작성을 다시 하지 않았다(체크포인트에서 이어 감)
    assert resumed["calls_in_this_process"] == {"retriever": 0, "writer": 0}
    assert resumed["edited"] == [False, True]
    assert again["error"] == "CONFLICT_NOT_AWAITING_APPROVAL"
    assert count["memo_rows"] == 1
