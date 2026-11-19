"""그래프 노드별 소요 시간 기록(측정 전용, 그래프 동작은 바꾸지 않음).

LangChain 콜백으로 LangGraph가 노드마다 여는 실행(run)의 시작·끝 시각을 잰다. 콜백은 config로만 넘기므로
그래프 코드와 State는 그대로다. 같은 노드 안의 하위 실행(라우팅 함수 등)은 같은 checkpoint_ns를 가지므로
바깥 실행만 센다. 서브그래프 노드는 'search_agent/retriever'처럼 부모 노드 이름을 앞에 붙인다.
"""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler


def _node_path(checkpoint_ns: str, node: str) -> str:
    parts = [part.split(":", 1)[0] for part in checkpoint_ns.split("|") if part]
    return "/".join(parts) if parts else node


class NodeTimer(BaseCallbackHandler):
    """한 질문 동안 노드 경로별 소요 시간(ms)과 실행 횟수를 모은다."""

    def __init__(self) -> None:
        self._open: dict[UUID, tuple[str, str, float]] = {}
        self.totals: dict[str, dict[str, float]] = {}

    def on_chain_start(self, serialized: Any, inputs: Any, *, run_id: UUID, parent_run_id: UUID | None = None,
                       metadata: dict[str, Any] | None = None, **kwargs: Any) -> None:
        meta = metadata or {}
        node = meta.get("langgraph_node")
        if not node:
            return
        namespace = str(meta.get("langgraph_checkpoint_ns", ""))
        parent = self._open.get(parent_run_id) if parent_run_id else None
        if parent is not None and parent[1] == namespace:
            return  # 같은 노드 안의 하위 실행
        self._open[run_id] = (_node_path(namespace, str(node)), namespace, time.perf_counter())

    def _close(self, run_id: UUID) -> None:
        opened = self._open.pop(run_id, None)
        if opened is None:
            return
        path, _, started = opened
        entry = self.totals.setdefault(path, {"ms": 0.0, "calls": 0})
        entry["ms"] = round(entry["ms"] + (time.perf_counter() - started) * 1000, 1)
        entry["calls"] += 1

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._close(run_id)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._close(run_id)
