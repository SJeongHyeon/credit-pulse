"""그래프 상태 정의 (G4).

State에 둘 것과 Store에 둘 것을 구분하세요.
  - State: 이번 대화(thread) 안에서만 필요한 것 (질의, 검색 결과, 중간 판단)
  - Store: 대화를 넘어 사용자별로 남기는 것 (backend/memory.py)
"""
from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    user_id: str
    review_id: str
    thread_id: str

    question: str
    corp_name: str
    corp_code: str
    fiscal_year: int
    review_target: dict[str, Any]
    checklist_items: list[str]

    outcome: Literal["answered", "refused", "awaiting_approval"]
    refusal_message: str | None
    refusal_reason: Literal[
        "OUT_OF_DOMAIN",
        "CORP_NOT_IN_CORPUS",
        "ITEM_NOT_DISCLOSED",
        "PERIOD_NOT_IN_CORPUS",
        "PRIOR_YEAR_NOT_IN_CORPUS",
        "INJECTION_SUSPECTED",
    ] | None
    # 멀티턴 인젝션 방어의 대화 누적 위험도. 턴 카운터가 아니라 thread에 남는다(backend/graph/injection_guard.py)
    injection_risk: dict[str, Any] | None
    status: Literal["open", "running", "awaiting_approval", "saving", "closed"]

    retrieval_query: str
    retrieval_rounds: int
    filters: dict[str, Any]
    retrieved_chunks: list[dict[str, Any]]
    retrieval_sufficient: bool
    rewrite_changed: bool
    target_required: bool
    comparison_targets: list[dict[str, Any]]
    refusal_kind: Literal["unrelated", "work_related"] | None  # 뉴스 분기 켜짐일 때만(docs/specs/refusal-news-search.md)
    news_items: list[dict[str, Any]]  # 거절 응답에 붙는 참고용 뉴스(최대 5). 공시 근거가 아니다
    news_search: Literal["ok", "skipped_no_key", "error"] | None
    news_calls: int  # 턴당 0~1. 일일 한도 25,000회 관리용
    domain_check: str  # 59번: off / in_domain / out_of_domain / skipped_no_key
    # 멀티 허브(74번, 104번 구조): 턴마다 비운다(backend/graph/supervisor_flow.py TURN_COUNTERS)
    supervisor_turns: int
    search_calls: int
    writer_calls: int
    redraft_calls: int  # 다시 쓰기(writer_feedback을 실은 작성) 횟수, 질문당 1
    written_search: int  # 104번: 마지막 초안을 몇 번째 검색 결과로 썼나(작성 여부 판단)
    supervisor_trace: list[str]
    supervisor_next: str  # 104번: supervisor가 정한 다음 일(search_agent/writer_agent/approval/refuse/end)
    supervisor_decisions: list[dict[str, Any]]  # 104번: 방문마다 next·reason·llm_next·fallback·guard, 107번 shortcut
    writer_feedback: str  # 104번: Supervisor가 다시 쓰기를 지시할 때만 싣는 고칠 점
    search_query: str  # 165번: Supervisor LLM이 쓴 재검색 질의(재검색을 고른 방문에서만 채운다)
    search_report: dict[str, Any]  # 104번: 검색 에이전트 보고(sufficient, missing_items, chunk_ids, n_chunks)
    grader_missing_items: list[str]  # 125번(grade_to_writer): 채점이 청크에서 찾지 못했다고 본 항목. 작성기에 알린다
    writer_report: dict[str, Any]  # 104번: 작성 에이전트 보고(has_draft, verification_passed, mismatch_items)
    retrieval_empty: bool

    draft_items: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    verification: list[dict[str, Any]]
    verification_rewrites: int
    applied_rules: list[dict[str, Any]]

    approval_seq: int
    approval_decisions: list[dict[str, Any]]
    saved_memo_id: str

    trace_id: str
    node_events: list[str]
    errors: list[str]
