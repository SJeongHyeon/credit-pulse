"""미리 실행(사전 등록 110번): 기다리는 시간을 겹쳐 응답을 줄인다. 그래프의 노드·순서·판단 규칙은 바꾸지 않는다.

- 입구의 주제 분류 LLM을 기다리는 동안 검색 에이전트(검색 → 회사·연도 검사 → 채점)를 미리 돌린다.
- 주제 분류가 통과하면, 검색 청크가 나오는 대로 채점을 기다리지 않고 작성 에이전트(작성 → 검증)를 미리 돌린다.
- 어댑터 노드는 '지금 부르려는 입력'과 미리 실행한 입력이 완전히 같을 때만 그 결과를 쓴다(JSON으로 비교).
  다르면 평소처럼 직접 부른다. 쓰이지 않은 결과는 버린다(그 호출의 비용은 든다).

서브에이전트는 checkpointer 없이 compile한 그래프라 다른 스레드에서 불러도 부모 State를 건드리지 않는다.
스레드에는 contextvars를 복사해 넘긴다(Langfuse 추적 맥락).

Langfuse 기록(docs/specs/trace-hierarchy.md): 미리 시작한 실행은 시작한 자리(주제 분류·채점 도중)가 아니라
supervisor 아래의 search_agent·writer_agent 관측으로 남긴다(tracing.open_agent, speculative=true).
어댑터가 가져가면 그 손잡이를 넘겨주고(taken_search_trace·taken_writer_trace), 쓰이지 않으면 discarded=true로 닫는다.
기록 손잡이는 실행 결과·입력 비교에 관여하지 않는다(기록이 꺼져 있으면 아무 일도 하지 않는 손잡이다).
"""
from __future__ import annotations

import contextvars
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from backend.services import tracing


def _key(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


class Speculation:
    """질문 한 번(턴) 동안의 미리 실행을 맡는다. 그래프 하나에 하나씩 만든다."""

    def __init__(self, run_search: Callable[[dict], dict], run_writer: Callable[[dict], dict],
                 writer_input: Callable[[dict], dict], strict_feedback: str | None = None):
        self._strict_feedback = strict_feedback  # 112번: 엄격 지시를 붙인 두 번째 초안(없으면 초안 하나)
        self._run_search = run_search
        self._run_writer = run_writer
        self._writer_input = writer_input  # 부모 State → 작성 에이전트 인계(select_writer_input)
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="speculation")
        self._lock = threading.Lock()
        self._search_trace: Any = None  # 미리 시작한 검색의 기록 손잡이
        self._writer_trace: tuple[Any, Any, Any] | None = None  # (writer_agent, 보통 초안, 엄격 초안 또는 None)
        self.taken_search_trace: Any = None  # take_search가 맞았을 때 어댑터가 이어받는 손잡이
        self.taken_writer_trace: tuple[Any, Any, Any] | None = None
        self._reset_locked()

    def _discard_search_trace_locked(self) -> None:
        if self._search_trace is not None:
            self._search_trace.resolve(discarded=True)
        self._search_trace = None

    def _discard_writer_trace_locked(self) -> None:
        for handle in self._writer_trace or ():
            if handle is not None:
                handle.resolve(discarded=True)
        self._writer_trace = None

    def _reset_locked(self) -> None:
        self._discard_search_trace_locked()  # 가져가지 않은 미리 실행은 버려진 것으로 기록한다
        self._discard_writer_trace_locked()
        self._search: tuple[str, Future] | None = None
        self._search_key: str | None = None  # 어댑터가 결과를 가져간 뒤에도 on_chunks가 알아보게 남긴다
        self._writer: tuple[str, Future, Future | None] | None = None
        self._chunk_fields: dict[str, Any] | None = None
        self._base: dict[str, Any] | None = None

    def reset(self) -> None:
        """새 질문을 시작할 때 앞 질문의 미리 실행을 버린다(기다리지 않는다)."""

        with self._lock:
            self._reset_locked()

    def cancel(self) -> None:
        """입구에서 거절됐을 때 부른다. 미리 시작한 검색이 끝날 때까지 기다린 뒤 버린다
        (그 검색 호출이 다음 질문의 기록에 섞이지 않게). 미리 시작한 작성은 기다리지 않고 버린다."""

        with self._lock:
            pending = self._search
            self._reset_locked()
        if pending is not None:
            pending[1].exception()  # 끝날 때까지 기다린다(결과·예외는 버린다)

    def _submit(self, fn: Callable[[dict], dict], payload: dict, trace: Any) -> Future:
        # trace.run은 그 안의 관측을 이 손잡이 아래에 두고 fn(payload)를 그대로 부른다
        return self._executor.submit(contextvars.copy_context().run, trace.run, fn, payload)

    # ── 검색 에이전트 ──────────────────────────────────────────
    def start_search(self, handoff: dict[str, Any], assumed_base: dict[str, Any] | None = None) -> None:
        """assumed_base(113번): 입구 검사가 끝나기 전에 작성을 미리 시작할 때 쓸 부모 State 가정값
        (주제 판정을 가장 흔한 값으로 둔 것). 실제 값이 다르면 allow_writer가 버리고 다시 시작한다."""

        with self._lock:
            self._reset_locked()
            self._base = None if assumed_base is None else dict(assumed_base)
            self._search_key = _key(handoff)
            self._search_trace = tracing.open_agent("search_agent", metadata={
                "speculative": True})
            self._search = (self._search_key, self._submit(self._run_search, handoff, self._search_trace))

    def take_search(self, handoff: dict[str, Any]) -> Future | None:
        with self._lock:
            pending, self._search = self._search, None
            matched = pending is not None and pending[0] == _key(handoff)
            if matched:
                self.taken_search_trace, self._search_trace = self._search_trace, None
            else:
                self.taken_search_trace = None
                self._discard_search_trace_locked()
        if matched:
            return pending[1]
        return None

    def on_chunks(self, search_input: dict[str, Any], fields: dict[str, Any]) -> None:
        """검색 에이전트가 청크를 얻은 직후(채점 전) 부른다. 미리 시작한 그 검색일 때만 기억한다."""

        with self._lock:
            if self._search_key is None or self._search_key != _key(search_input):
                return
            self._chunk_fields = dict(fields)
            self._start_writer_locked()

    # ── 작성 에이전트 ──────────────────────────────────────────
    def allow_writer(self, parent_state: dict[str, Any]) -> None:
        """입구 검사를 통과한 뒤 부른다(주제 판정 결과가 작성기 입력에 들어가므로 그 전에는 시작하지 않는다)."""

        with self._lock:
            if self._search_key is None:
                return
            self._base = dict(parent_state)
            if self._writer is not None and self._chunk_fields is not None:
                # 가정값으로 미리 시작한 작성이 실제 입력과 다르면 버리고 다시 시작한다
                real = self._writer_input({**self._base, **self._chunk_fields})
                if self._writer[0] != _key(real):
                    self._writer = None
                    self._discard_writer_trace_locked()
            self._start_writer_locked()

    def _start_writer_locked(self) -> None:
        if self._writer is not None or self._base is None or self._chunk_fields is None:
            return
        if not self._chunk_fields.get("retrieved_chunks"):
            return  # 근거 0건이면 작성하지 않는다(guard 규칙)
        handoff = self._writer_input({**self._base, **self._chunk_fields})
        # 부모는 지금 자리(검색 스레드의 채점 노드)가 아니라 supervisor다(open_agent가 명시적으로 정한다)
        agent = tracing.open_agent("writer_agent", runs=False, metadata={
            "attempt": int(self._base.get("writer_calls", 0)) + 1, "redraft": False, "speculative": True})
        normal_trace = agent.child("draft_normal")
        strict, strict_trace = None, None
        if self._strict_feedback and not handoff.get("writer_feedback"):
            strict_trace = agent.child("draft_strict")
            strict = self._submit(self._run_writer, {**handoff, "writer_feedback": self._strict_feedback}, strict_trace)
        self._writer = (_key(handoff), self._submit(self._run_writer, handoff, normal_trace), strict)
        self._writer_trace = (agent, normal_trace, strict_trace)

    def take_writer(self, handoff: dict[str, Any]) -> tuple[Future, Future | None] | None:
        """(보통 초안, 엄격 초안 또는 None)을 돌려준다. 입력이 다르면 None."""

        with self._lock:
            pending, self._writer = self._writer, None
            self._chunk_fields, self._base, self._search_key = None, None, None
            matched = pending is not None and pending[0] == _key(handoff)
            if matched:
                self.taken_writer_trace, self._writer_trace = self._writer_trace, None
            else:
                self.taken_writer_trace = None
                self._discard_writer_trace_locked()
        if matched:
            return pending[1], pending[2]
        return None
