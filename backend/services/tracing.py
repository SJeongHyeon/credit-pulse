"""Langfuse 트레이스 어댑터(G6). 질문 한 건 = 트레이스 하나, 외부 호출(채팅·임베딩·리랭크) 한 번 = 그 안의 관측 하나.

계층(docs/specs/trace-hierarchy.md): 질문 → supervisor → 에이전트(search_agent·writer_agent) → 노드 → 외부 호출.
  - 부모는 Langfuse의 '현재 컨텍스트'에 맡기지 않고 이 모듈이 ContextVar(_PARENT)로 들고 다닌다. 자식은 항상
    부모.start_observation()으로 만든다(langfuse 5.1 문서: 수동 관측은 현재 컨텍스트가 되지 않지만, 부모 객체에서
    만들면 그 부모의 자식이 된다). 스레드로 넘어가도 copy_context가 _PARENT를 함께 넘긴다.
  - 미리 실행(speculation)·초안 2개는 시작한 자리(주제 분류·채점 도중)가 아니라 supervisor 아래에 에이전트 관측을
    명시적으로 만든다(open_agent). 쓰였는지 버려졌는지는 나중에 정해지므로, 관측을 열어 두었다가 '일이 끝남'과
    '운명이 정해짐'이 둘 다 되면 메타데이터(speculative·discarded)를 쓰고 실제 끝난 시각으로 닫는다.
    (end() 뒤의 update()는 반영되지 않는다. 5.1.0 실측.)
  - 기록은 메모리에 쌓였다가 백그라운드로 나간다. 요청 경로에 네트워크 호출을 넣지 않는다.

강의 규칙(P19)을 따른다:
  - 키(LANGFUSE_PUBLIC_KEY·LANGFUSE_SECRET_KEY)가 있을 때만 langfuse를 import한다. 없으면 모든 함수가 아무것도 하지 않는다.
  - 비용은 단가표로 다시 계산하지 않고 공급자가 준 값(OpenRouter usage.cost)을 cost_details로 넘긴다(R4).
    Voyage 리랭크 응답에는 비용이 없어 토큰 수만 남긴다.
  - 캐시 토큰은 usage_details에 더하지 않는다(R5).
  - 트레이스에는 질문·근거 id·판정·시간·토큰·비용만 남긴다. 프롬프트·청크 본문은 남기지 않는다(R7).
  - 파일 이름이 langfuse.py가 아닌 이유: 같은 이름이면 `import langfuse`가 이 파일을 가져온다.
  - 짧게 끝나는 프로세스(평가 러너)는 끝에 flush()를 부른다(Langfuse는 백그라운드로 모아 보낸다).

트레이스 실패는 서비스 응답을 바꾸지 않는다: 예외는 로그로만 남긴다.
"""
from __future__ import annotations

import contextvars
import functools
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

_client: Any = None

SUPERVISOR = "supervisor"  # 에이전트·판단 호출의 부모(그림의 Supervisor 칸)
APPROVAL = "approval"  # 승인 3노드를 묶는 칸(supervisor 아래)


def enabled() -> bool:
    if str(os.getenv("LANGFUSE_TRACING_ENABLED", "true")).strip().lower() in {"0", "false", "no"}:
        return False
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY", "").strip() and os.getenv("LANGFUSE_SECRET_KEY", "").strip())


def _langfuse():
    global _client
    if _client is None:
        from langfuse import Langfuse, is_langfuse_span  # 키가 있을 때만 import(R: 키 없는 환경에서도 서비스가 뜬다)

        # 이 어댑터가 만든 관측만 내보낸다. 기본 필터는 다른 LLM 라이브러리의 자동 관측(예: anthropic SDK의
        # anthropic.messages.create)까지 보내서 chat 관측과 토큰·비용이 두 번 잡혔다(11/7 실측).
        _client = Langfuse(should_export_span=is_langfuse_span)
    return _client


def _safely(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """트레이스 실패는 서비스 응답을 바꾸지 않는다: 예외는 로그로만 남기고 None을 돌려준다."""

    try:
        return call(*args, **kwargs)
    except Exception:
        logger.exception("Langfuse 기록 실패")
        return None


def _is_interrupt(exc: BaseException) -> bool:
    """LangGraph interrupt(사람 승인 대기)는 오류가 아니다. langgraph를 import하지 않으려고 이름으로 본다."""

    return any(cls.__name__ == "GraphBubbleUp" for cls in type(exc).__mro__)


class _Trace:
    """질문 한 건의 보관함: 루트 관측, 필요할 때 만드는 묶음 관측(supervisor·approval), 아직 닫지 않은 손잡이."""

    def __init__(self, root: Any) -> None:
        self.root = root
        self.lock = threading.Lock()
        self.groups: dict[str, Any] = {}
        self.pending: list[_Deferred] = []
        self.closed = False

    def group(self, name: str) -> Any:
        """supervisor(루트 아래)·approval(supervisor 아래) 관측을 처음 쓸 때 만든다. 질문이 끝날 때 닫는다."""

        with self.lock:
            if SUPERVISOR not in self.groups:
                self.groups[SUPERVISOR] = self.root.start_observation(name=SUPERVISOR, as_type="agent")
            if name not in self.groups:
                self.groups[name] = self.groups[SUPERVISOR].start_observation(name=name, as_type="chain")
            return self.groups[name]

    def close(self) -> None:
        with self.lock:
            self.closed = True
            pending, self.pending = self.pending, []
            groups = [self.groups.get(APPROVAL), self.groups.get(SUPERVISOR)]
        for handle in pending:  # 질문이 끝날 때까지 쓰이지 않은 미리 실행은 버려진 것이다
            handle.resolve(discarded=True)
        for observation in groups:
            if observation is not None:
                _safely(observation.end)


# 질문 보관함과 '지금의 부모 관측'. 값이 객체라서 copy_context로 넘어간 스레드도 같은 것을 본다.
_TRACE: contextvars.ContextVar[_Trace | None] = contextvars.ContextVar("tracing_trace", default=None)
_PARENT: contextvars.ContextVar[Any] = contextvars.ContextVar("tracing_parent", default=None)


class _Question:
    """질문 트레이스 손잡이. 꺼져 있으면 trace_id가 None이고 finish는 아무것도 하지 않는다."""

    def __init__(self, span: Any = None, trace_id: str | None = None) -> None:
        self._span = span
        self.trace_id = trace_id

    def finish(self, output: dict[str, Any]) -> None:
        if self._span is None:
            return
        try:
            self._span.update(output=output)
        except Exception:
            logger.exception("Langfuse 트레이스 출력 기록 실패")


@contextmanager
def question(
    name: str,
    *,
    question: str,
    metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> Iterator[_Question]:
    """질문 한 건을 트레이스 하나로 묶는다. 안에서 일어난 외부 호출이 이 트레이스의 자식이 된다.

    user_id·session_id·tags는 트레이스 단위 속성이다(Langfuse v5 `propagate_attributes`로 루트와 자식 관측에 함께 붙음).
    user_id는 로그인 사용자의 내부 UUID만 넣는다(이메일 등 개인정보 아님, P19 R7). session_id는 심사 건 id라
    같은 심사 건의 질의·승인 트레이스가 Langfuse 세션 하나로 묶인다.
    """

    if not enabled():
        yield _Question()
        return
    attributes = None
    context_token = None
    try:
        from langfuse import propagate_attributes
        from opentelemetry import context as otel_context

        # 질문 트레이스는 항상 새 루트로 시작한다. 바깥에 이미 OTel 문맥이 있으면(FastAPI 내장 telemetry가 요청마다 서버 span을
        # 만들고, Cloud Run이 붙인 traceparent 헤더의 '샘플링 안 함'을 물려준다) 그 아래로 들어가 관측이 전부 기록되지 않는다.
        # 11/11 배포 서비스 측정에서 질의 34건 중 1건만 Langfuse에 남은 원인이다(샘플링된 요청만 남음).
        context_token = otel_context.attach(otel_context.Context())
        attributes = propagate_attributes(user_id=user_id, session_id=session_id, tags=tags)
        attributes.__enter__()
        manager = _langfuse().start_as_current_observation(
            name=name, as_type="chain", input={"question": question}, metadata=metadata or {}
        )
        span = manager.__enter__()
    except Exception:
        logger.exception("Langfuse 트레이스 시작 실패")
        if attributes is not None:
            attributes.__exit__(None, None, None)
        _detach(context_token)
        yield _Question()
        return
    handle = _Question(span, getattr(span, "trace_id", None))
    trace = _Trace(span)
    trace_token, parent_token = _TRACE.set(trace), _PARENT.set(span)
    try:
        yield handle
    except BaseException as exc:
        try:
            span.update(level="ERROR", status_message=type(exc).__name__)
        except Exception:
            pass
        _PARENT.reset(parent_token)
        _TRACE.reset(trace_token)
        trace.close()
        manager.__exit__(type(exc), exc, exc.__traceback__)
        attributes.__exit__(type(exc), exc, exc.__traceback__)
        _detach(context_token)
        raise
    else:
        _PARENT.reset(parent_token)
        _TRACE.reset(trace_token)
        trace.close()
        manager.__exit__(None, None, None)
        attributes.__exit__(None, None, None)
        _detach(context_token)


def _detach(context_token: Any) -> None:
    """question()이 붙인 빈 OTel 문맥을 되돌린다(붙이지 못했으면 아무것도 하지 않는다)."""

    if context_token is None:
        return
    try:
        from opentelemetry import context as otel_context

        otel_context.detach(context_token)
    except Exception:
        logger.exception("OTel 문맥 되돌리기 실패")


# ── 계층: 노드 관측, supervisor·approval 묶음, 에이전트 손잡이 ─────────────────
class _Span:
    """span()이 돌려주는 손잡이. 꺼져 있으면 아무것도 하지 않는다."""

    def __init__(self, observation: Any = None) -> None:
        self._observation = observation

    def update(self, *, output: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None) -> None:
        if self._observation is None:
            return
        fields = {key: value for key, value in (("output", output), ("metadata", metadata)) if value is not None}
        if fields:
            _safely(self._observation.update, **fields)


@contextmanager
def span(name: str, *, as_type: str = "span", metadata: dict[str, Any] | None = None,
         group: str | None = None) -> Iterator[_Span]:
    """지금의 부모 아래에 관측 하나를 열고, 그 안에서는 이 관측이 부모가 된다(노드 한 번 실행 = 관측 하나).

    group: SUPERVISOR·APPROVAL이면 지금의 부모 대신 그 묶음 관측 아래에 둔다(그래프 노드는 서로 형제로 실행되므로
    '어디 아래인가'를 실행 위치가 아니라 이름으로 정한다). 질문 트레이스 밖이거나 꺼져 있으면 아무것도 하지 않는다.
    """

    observation = None
    if enabled():
        parent = _PARENT.get()
        if group is not None:
            trace = _TRACE.get()
            parent = None if trace is None or trace.closed else _safely(trace.group, group)
        if parent is not None:
            observation = _safely(parent.start_observation, name=name, as_type=as_type, metadata=metadata or {})
    if observation is None:
        yield _Span()
        return
    token = _PARENT.set(observation)
    try:
        yield _Span(observation)
    except BaseException as exc:
        if _is_interrupt(exc):
            _safely(observation.update, metadata={"interrupted": True})
        else:
            _safely(observation.update, level="ERROR", status_message=type(exc).__name__)
        raise
    finally:
        _PARENT.reset(token)
        _safely(observation.end)


@contextmanager
def supervisor() -> Iterator[None]:
    """이 안의 외부 호출·관측을 supervisor 관측 아래에 둔다(supervisor 노드의 방문마다 쓴다. 새 관측은 만들지 않는다)."""

    trace = _TRACE.get()
    observation = None
    if trace is not None and not trace.closed and enabled():
        observation = _safely(trace.group, SUPERVISOR)
    if observation is None:
        yield
        return
    token = _PARENT.set(observation)
    try:
        yield
    finally:
        _PARENT.reset(token)


def node(name: str, fn: Callable[..., Any], *, as_type: str = "span", group: str | None = None,
         summarize: Callable[[Any], dict[str, Any] | None] | None = None) -> Callable[..., Any]:
    """그래프 노드 함수를 감싸 실행 한 번을 관측 하나로 남긴다. 인자·반환값·예외는 그대로 넘긴다(그래프 동작 불변).

    functools.wraps로 원래 서명을 남긴다(LangGraph는 서명을 보고 config를 넘길지 정한다).
    summarize: 반환값에서 관측 output으로 남길 값(개수·판정·id만. 본문은 넣지 않는다, P19 R7).
    """

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        with span(name, as_type=as_type, group=group) as handle:
            result = fn(*args, **kwargs)
            if summarize is not None:
                handle.update(output=_safely(summarize, result))
            return result

    return run


def under_supervisor(fn: Callable[..., Any]) -> Callable[..., Any]:
    """supervisor 노드 함수를 감싼다: 그 안의 호출(주제 분류·다음 일 판단)이 supervisor 관측 아래에 남는다."""

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        with supervisor():
            return fn(*args, **kwargs)

    return run


class _Deferred:
    """나중에 닫는 관측 손잡이(에이전트·초안). '일이 끝남'과 '운명(쓰임·버림)이 정해짐'이 둘 다 되면 닫는다.

    run()은 어느 스레드에서 불러도 된다: 그 안에서는 이 관측이 부모가 된다. 꺼져 있으면 fn만 부른다.
    """

    def __init__(self, observation: Any = None, *, runs: bool = True, trace: _Trace | None = None) -> None:
        self._observation = observation
        self._trace = trace
        self._lock = threading.Lock()
        if observation is not None and trace is not None:
            with trace.lock:  # 질문이 끝날 때까지 운명이 정해지지 않으면 trace.close()가 버린 것으로 닫는다
                if not trace.closed:
                    trace.pending.append(self)
        self._waiting = runs  # run()이 끝나기를 기다리는가(runs=False는 자식만 담는 칸: 운명이 정해지면 바로 닫는다)
        self._finished_ns: int | None = None
        self._error: str | None = None
        self._fate: dict[str, Any] | None = None
        self._output: dict[str, Any] | None = None
        self._ended = False

    def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        if self._observation is None:
            return fn(*args)
        token = _PARENT.set(self._observation)
        try:
            return fn(*args)
        except BaseException as exc:
            self._error = type(exc).__name__
            raise
        finally:
            _PARENT.reset(token)
            with self._lock:
                self._finished_ns = time.time_ns()
                self._waiting = False
            self._end_if_ready()

    def child(self, name: str, *, as_type: str = "chain", metadata: dict[str, Any] | None = None) -> _Deferred:
        if self._observation is None:
            return _Deferred()
        return _Deferred(_safely(self._observation.start_observation, name=name, as_type=as_type,
                                 metadata=metadata or {}), trace=self._trace)

    def resolve(self, *, discarded: bool, output: dict[str, Any] | None = None, **metadata: Any) -> None:
        """운명을 정한다. 처음 정한 것만 쓴다(질문이 끝날 때의 일괄 '버림'이 이미 정한 것을 덮지 않게)."""

        if self._observation is None:
            return
        with self._lock:
            if self._fate is not None:
                return
            self._fate = {"discarded": bool(discarded), **metadata}
            self._output = output
        self._end_if_ready()

    def _end_if_ready(self) -> None:
        with self._lock:
            if self._ended or self._fate is None or self._waiting:
                return
            self._ended = True
        fields: dict[str, Any] = {"metadata": dict(self._fate)}
        if self._output is not None:
            fields["output"] = self._output
        if self._error is not None:
            fields.update(level="ERROR", status_message=self._error)
        _safely(self._observation.update, **fields)
        if self._finished_ns is not None:
            _safely(self._observation.end, end_time=self._finished_ns)
        else:
            _safely(self._observation.end)


def open_agent(name: str, *, metadata: dict[str, Any] | None = None, runs: bool = True) -> _Deferred:
    """supervisor 아래에 에이전트 관측을 명시적으로 연다. 부르는 자리(스레드·노드)가 어디든 부모는 supervisor다.

    미리 실행은 주제 분류·채점 도중에 시작되므로 '지금의 부모'를 따르면 엉뚱한 칸 아래에 붙는다.
    돌려준 손잡이는 resolve()로 운명을 정한다(질문이 끝날 때까지 정하지 않으면 버린 것으로 닫는다).
    runs=False: run()을 부르지 않고 자식(child)만 담는 칸.
    """

    trace = _TRACE.get()
    if trace is None or trace.closed or not enabled():
        return _Deferred()
    parent = _safely(trace.group, SUPERVISOR)
    observation = None if parent is None else _safely(
        parent.start_observation, name=name, as_type="agent", metadata=metadata or {})
    if observation is None:
        return _Deferred()
    return _Deferred(observation, runs=runs, trace=trace)


class _Fate:
    """이중 요청(llm._first_of_two)의 한쪽 호출. 그 호출이 만든 외부 호출 관측을 열어 두었다가, 어느 쪽 답을
    썼는지 정해지면 메타데이터(hedged·discarded)를 쓰고 닫는다. 정해진 뒤에 끝난 관측은 바로 쓰고 닫는다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: list[tuple[Any, int]] = []
        self._metadata: dict[str, Any] | None = None

    def hold(self, observation: Any) -> None:
        with self._lock:
            if self._metadata is None:
                self._held.append((observation, time.time_ns()))
                return
            metadata = self._metadata
        if metadata:
            _safely(observation.update, metadata=dict(metadata))
        _safely(observation.end)

    def decide(self, **metadata: Any) -> None:
        with self._lock:
            if self._metadata is not None:
                return
            self._metadata = dict(metadata)
            held, self._held = self._held, []
        for observation, finished_ns in held:
            if metadata:
                _safely(observation.update, metadata=dict(metadata))
            _safely(observation.end, end_time=finished_ns)


_FATE: contextvars.ContextVar[_Fate | None] = contextvars.ContextVar("tracing_fate", default=None)
_NAME: contextvars.ContextVar[str | None] = contextvars.ContextVar("tracing_call_name", default=None)


def named(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """llm.py의 호출 함수에 붙이는 데코레이터: 그 안의 채팅 호출 관측 이름을 역할(name)로 남긴다.

    openrouter_structured에 인자를 더하지 않으려고 ContextVar로 넘긴다(시험이 그 함수를 고정 서명의 가짜로 바꾼다).
    이중 요청 스레드는 copy_context로 같은 이름을 본다.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def run(*args: Any, **kwargs: Any) -> Any:
            token = _NAME.set(name)
            try:
                return fn(*args, **kwargs)
            finally:
                _NAME.reset(token)

        return run

    return decorate


def hedged_context() -> tuple[contextvars.Context, _Fate | None]:
    """이중 요청의 한쪽을 돌릴 컨텍스트(지금 컨텍스트의 복사본)와 그 운명 손잡이. 기록할 부모가 없으면 손잡이는 None이다."""

    context = contextvars.copy_context()
    if _PARENT.get() is None or not enabled():
        return context, None
    fate = _Fate()
    context.run(_FATE.set, fate)
    return context, fate


class _Call:
    def __init__(self, observation: Any = None, kind: str | None = None) -> None:
        self._observation = observation
        self._kind = kind
        self._started = time.perf_counter()

    def done(self, *, input_tokens: int | None = None, output_tokens: int | None = None,
             cost_usd: float | None = None, output: dict[str, Any] | None = None) -> None:
        if self._observation is None:
            return
        usage = {key: int(value) for key, value in (("input", input_tokens), ("output", output_tokens)) if value is not None}
        try:
            self._observation.update(
                output=output or {},
                usage_details=usage or None,
                cost_details={"total": float(cost_usd)} if cost_usd is not None else None,
                metadata={"latency_ms": round((time.perf_counter() - self._started) * 1000, 1),
                          **({"kind": self._kind} if self._kind else {})},
            )
        except Exception:
            logger.exception("Langfuse 관측 기록 실패")


@contextmanager
def external_call(kind: str, *, model: str, input: dict[str, Any], name: str | None = None) -> Iterator[_Call]:
    """외부 호출 한 번. kind: chat(generation)·embedding(embedding)·rerank(generation). input에는 개수·종류만(R7).

    name: 관측 이름(역할. 예: classify_domain·decide_next·write_drafts). 없으면 kind를 쓴다. kind는 metadata에 남는다.
    질문 트레이스 안이면 지금의 부모(노드·에이전트 관측) 아래에 만든다. 밖이면(스크립트 단독 호출) 이전처럼 만든다.
    """

    if not enabled():
        yield _Call()
        return
    as_type = "embedding" if kind == "embedding" else "generation"
    if name is None and kind == "chat":
        name = _NAME.get()  # named() 데코레이터가 정한 역할 이름
    parent = _PARENT.get()
    if parent is not None:
        observation = _safely(parent.start_observation, name=name or kind, as_type=as_type, model=model, input=input,
                              metadata={"kind": kind})
        if observation is None:
            yield _Call()
            return
        try:
            yield _Call(observation, kind)
        except BaseException as exc:
            _safely(observation.update, level="ERROR", status_message=type(exc).__name__)
            raise
        finally:
            fate = _FATE.get()
            if fate is not None:
                fate.hold(observation)  # 이중 요청: 어느 쪽 답을 썼는지 정해질 때 닫는다
            else:
                _safely(observation.end)
        return
    try:
        manager = _langfuse().start_as_current_observation(name=name or kind, as_type=as_type, model=model, input=input)
        observation = manager.__enter__()
    except Exception:
        logger.exception("Langfuse 관측 시작 실패")
        yield _Call()
        return
    try:
        yield _Call(observation, kind)
    except BaseException as exc:
        try:
            observation.update(level="ERROR", status_message=type(exc).__name__)
        except Exception:
            pass
        manager.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        manager.__exit__(None, None, None)


def score(trace_id: str | None, name: str, value: float, comment: str | None = None) -> None:
    """평가 점수를 트레이스에 연결한다(G6 '평가 점수 연결')."""

    if not trace_id or not enabled():
        return
    try:
        _langfuse().create_score(trace_id=trace_id, name=name, value=float(value), data_type="NUMERIC", comment=comment)
    except Exception:
        logger.exception("Langfuse 점수 기록 실패")


def flush() -> None:
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            logger.exception("Langfuse flush 실패")
