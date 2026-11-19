"""멀티 그래프 Supervisor: 정해진 길은 코드가 정하고, 판단이 필요한 갈림길에서 LLM이 고른다(사전 등록 104·107·159·160번, SPEC §5.3).

흐름: turn_start → load_rules → supervisor ⇄ (search_agent | writer_agent) → approval | END.
- 첫 방문에는 입구 검사(코드 규칙·주제 분류, build._supervisor_node)를 한다. 여기서 끝나는 질문은
  Supervisor LLM을 부르지 않는다(P10 R3: 비싼 호출 앞에서 거른다).
- 첫 검색은 원래 질문으로 한다. 결과가 정해진 방문(검색 전, 근거 0건, 검증 통과,
  검증 실패, 작성 단계 거절)은 decision_shortcut이 코드로 정한다(사전 등록 107번).
- 판단이 필요한 갈림길은 둘이고 decider(기본 llm.decide_next)가 고른다. guard_decision은 구조 규칙만 본다.
  (1) 160·165번: 검색·채점이 끝나고 작성 전. 채점 보고를 읽고 작성(writer_agent)·거절(refuse)·재검색(search_agent) 중
      하나를 고른다. 재검색은 채점이 부족할 때 1회만이고 질의는 LLM이 쓴다(MAX_SEARCHES = 2). 재검색을 마친 뒤에는
      LLM을 부르지 않고 작성으로 간다(166번). 회사 둘 이상을 비교하는 질문은 재검색하지 않는다(167번).
  (2) 검증은 통과했지만 needs_check·not_disclosed 항목이 있는 초안. 승인으로 넘길지 다시 쓰게 할지를 고른다.
  decider 입력은 질문·회사·연도·서브에이전트의 구조화 보고뿐이다(청크 본문 없음, P18 R4).
- LLM이 없거나(키 없음·데모 모드) 출력이 깨지면 fallback_decision(코드 규칙)으로 대신하고 기록에 남긴다.
- 다시 쓰기는 writer_feedback을 실은 writer_agent로만 일어난다.

guard_decision·fallback_decision은 State dict만 받는 순수 함수라 그래프 없이 시험한다(P11 R2).
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END

from backend.services import llm

# supervisor 방문 상한(안전 상한). 정상 경로는 검색 1 → 작성 1 → (다시 쓰기 1) → 승인으로 4방문 안에 끝난다.
# 159번에서 재검색을 없앤 뒤에도 값은 그대로 둔다. SPEC §4. 시험은 리터럴 8과 비교한다.
MAX_SUPERVISOR_TURNS = 8
# 다시 쓰기(writer_feedback을 실은 작성) 상한. SPEC §4 '검증 실패 후 재작성 1'.
MAX_REDRAFTS = 1
# 165번: 질문당 검색 상한(첫 검색 1 + 재검색 1). 시험은 리터럴 2와 비교한다.
MAX_SEARCHES = 2
SEARCH_QUERY_MAX_CHARS = 200
# 주제 분류(59·71번)가 '사업보고서 기재 사항이 아님'으로 표시한 값. 작성 전 거절은 이 표시가 있을 때만 받는다(172번).
NOT_DISCLOSED = "not_disclosed_item"
WORK_NODES = ("search_agent", "writer_agent")
# 턴(질문 한 번)마다 비우는 필드(P11 R4). 승인 재개는 START를 거치지 않아 영향 없다.
TURN_COUNTERS = {
    "supervisor_turns": 0, "search_calls": 0, "writer_calls": 0, "redraft_calls": 0, "written_search": 0,
    "supervisor_trace": [], "supervisor_decisions": [],
    "search_report": {}, "writer_report": {}, "supervisor_next": "", "writer_feedback": "", "search_query": "",
    "grader_missing_items": [],
    # 앞 질문의 결과를 지운다. 지우지 않으면 같은 thread(심사 건)에서 한 번 거절된 뒤의 질문이
    # 입구에서 앞 질문의 거절(outcome=refused)을 그대로 물려받아 전부 거절된다.
    "outcome": None, "refusal_reason": None, "refusal_message": None,
}
# 뉴스 분기가 켜진 그래프에서만 턴마다 비우는 필드(꺼짐이면 State에 이 키가 생기지 않는다)
NEWS_TURN_COUNTERS = {"refusal_kind": None, "news_items": [], "news_search": None, "news_calls": 0}
NUMBER_CHECK = "reason_number_or_unit_not_in_chunk"
# LLM이 없거나 출력이 깨졌을 때 코드 규칙으로 대신하는 예외(llm.py가 내는 것만)
LLM_FALLBACK_ERRORS = (
    llm.ExternalApiKeyMissing,
    llm.ExternalApiResponseError,
    llm.ExternalApiStatusError,
    llm.ExternalApiTimeout,
)


def turn_start_node(state: dict[str, Any]) -> dict[str, Any]:
    """질문 한 번(턴)의 시작에서 횟수·보고·지시 필드를 비운다."""

    return {key: (list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value)
            for key, value in TURN_COUNTERS.items()}


def news_turn_start_node(state: dict[str, Any]) -> dict[str, Any]:
    """뉴스 분기가 켜진 그래프의 턴 시작: 허브 카운터에 뉴스 필드 초기화를 더한다."""

    return {**turn_start_node(state),
            **{key: (list(value) if isinstance(value, list) else value) for key, value in NEWS_TURN_COUNTERS.items()}}


def redraft_feedback(state: dict[str, Any]) -> str:
    """다시 쓰기 지시의 코드 기본 문장. 숫자는 verifier가 기록한 것만 쓴다(청크·질의 본문은 넣지 않음).

    숫자 불일치는 78번 문장 그대로다(데모 녹화와 같은 입력이 되도록). 그 밖의 검사 실패는 실패한 검사 이름만 적는다.
    """

    failed = [v for v in state.get("verification") or [] if not v.get("passed")]
    if any(NUMBER_CHECK in (v.get("failed_checks") or []) for v in failed):
        numbers = sorted({
            str(n) for v in failed if NUMBER_CHECK in (v.get("failed_checks") or [])
            for n in v.get("missing_numbers") or []
        })
        listed = ", ".join(numbers[:10]) or "일부 숫자"
        return (
            f"이전 초안의 reason에 쓴 숫자 {listed}는 인용한 청크에 없습니다. reason에는 인용 청크에 적힌 숫자만 "
            "그대로 쓰고, 직접 세거나 계산한 숫자나 질문의 표현을 숫자로 옮겨 쓰지 마세요."
        )
    checks = sorted({str(c) for v in failed for c in v.get("failed_checks") or []})
    return (
        f"이전 초안의 인용 검증이 실패했습니다(실패한 검사: {', '.join(checks) or '알 수 없음'}). "
        "인용과 reason에는 청크 본문에 그대로 있는 내용만 쓰세요."
    )


def _latest_written(state: dict[str, Any]) -> bool:
    """마지막 검색 결과로 작성까지 끝났는가.

    작성할 때의 검색 회차(written_search)가 지금 검색 횟수와 같으면 끝난 것이다.
    """

    return int(state.get("writer_calls", 0)) > 0 and int(state.get("written_search", 0)) == int(state.get("search_calls", 0))


def _verification_failed(state: dict[str, Any]) -> bool:
    report = state.get("writer_report") or {}
    return report.get("verification_passed") is False


def _decision(next_node: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"next": next_node, "writer_feedback": "", "search_query": "", "refusal_reason": None,
            "reason": reason, **extra}


def _is_comparison(state: dict[str, Any]) -> bool:
    """167번: 회사 둘 이상을 비교하는 질문인가. 이런 질문은 첫 검색에서 이미 회사마다 한 번씩 검색하므로 재검색하지 않는다."""

    return len(state.get("comparison_targets") or []) >= 2


def _research_decision(state: dict[str, Any], data: dict[str, Any], reason: str) -> dict[str, Any]:
    """165번: Supervisor LLM이 고른 재검색을 받아 줄지 정한다(구조 규칙만).

    받는 조건: 아직 작성 전, 검색 횟수가 상한 미만, 회사 하나에 대한 질문(167번), 채점이 '충분'이라고 하지 않았음, 질의가 있고 원래 질문과 다르며
    지시형 문장이 아님. 하나라도 어긋나면 코드 규칙(작성)으로 대신하고 이유 코드를 남긴다.
    """

    from backend.graph.trust_boundary import safe_missing_items

    report = state.get("search_report") or {}
    query = " ".join(str(data.get("search_query") or "").split())[:SEARCH_QUERY_MAX_CHARS]
    question = " ".join(str(state.get("question", "")).split())
    if _latest_written(state):
        return _replace(state, "search_after_write")
    if int(state.get("search_calls", 0)) >= MAX_SEARCHES:
        return _replace(state, "search_cap")
    if _is_comparison(state):
        return _replace(state, "search_comparison")
    if report.get("sufficient") is True:
        return _replace(state, "search_despite_sufficient")
    if not query or query == question:
        return _replace(state, "search_query_missing")
    if not safe_missing_items([query])[0]:  # 빠진 항목에 실려 온 지시형 문장이 질의가 되지 않게 한다
        return _replace(state, "search_query_unsafe")
    return _decision("search_agent", reason, search_query=query, guard=None)


def _no_evidence_refusal(reason: str, **extra: Any) -> dict[str, Any]:
    return _decision("refuse", reason, refusal_reason="ITEM_NOT_DISCLOSED", **extra)


def fallback_decision(state: dict[str, Any]) -> dict[str, Any]:
    """코드 규칙(91번 기본 경로와 같은 순서): 검색 → 작성 → (검증 실패면 다시 쓰기 1회) → 승인.

    LLM이 없거나 출력이 깨졌을 때, 그리고 guard가 LLM 결정을 받아들이지 않을 때 쓴다.
    """

    turns = int(state.get("supervisor_turns", 0))
    if not state.get("search_calls"):
        return _decision("search_agent", "코드 규칙: 아직 검색하지 않음")
    if not _latest_written(state):
        report = state.get("search_report") or {}
        if report.get("graph_low_score_reviewed") and report.get("sufficient") is not True:
            return _no_evidence_refusal("코드 규칙: 그래프 이웃 재검토에서 근거 부족")
        if int(report.get("n_chunks", 0)) == 0:
            return _no_evidence_refusal("코드 규칙: 검색 근거 0건")
        return _decision("writer_agent", "코드 규칙: 검색 근거로 작성")
    if state.get("outcome") == "refused":
        return _decision("end", "코드 규칙: 작성 단계 거절 그대로")
    if (
        _verification_failed(state)
        and int(state.get("redraft_calls", 0)) < MAX_REDRAFTS
        and turns + 1 <= MAX_SUPERVISOR_TURNS
    ):
        return _decision("writer_agent", "코드 규칙: 검증 실패, 다시 쓰기", writer_feedback=redraft_feedback(state))
    return _decision("approval", "코드 규칙: 초안 완료")


def _terminal(state: dict[str, Any], guard: str) -> dict[str, Any]:
    """더 일할 수 없을 때(방문 상한) 끝내는 길. 작성 결과가 있으면 승인으로 간다(78번 규칙)."""

    if _latest_written(state):
        if state.get("outcome") == "refused":
            return _decision("end", "상한: 작성 단계 거절 그대로", guard=guard)
        return _decision("approval", "상한: 작성 결과로 승인", guard=guard)
    if state.get("search_calls") and int((state.get("search_report") or {}).get("n_chunks", 0)) == 0:
        return _no_evidence_refusal("상한: 검색 근거 0건", guard=guard)
    return _decision("end", "상한: 작성 전에 멈춤", guard=guard)


def _replace(state: dict[str, Any], guard: str) -> dict[str, Any]:
    return {**fallback_decision(state), "guard": guard}


def guard_decision(state: dict[str, Any], decision: Any) -> dict[str, Any]:
    """LLM 결정을 코드 규칙으로 고친다. 돌려주는 값: next·writer_feedback·refusal_reason·reason·guard.

    next: search_agent(검색 전 한 번만) / writer_agent / approval / refuse / end. guard는 고친 이유 코드(고치지 않았으면 None).
    decision은 llm.SupervisorDecision 또는 같은 키의 dict. None이나 허용 밖 값이면 코드 규칙으로 대신한다.
    """

    if decision is None:
        return _replace(state, "no_decision")
    data = decision.model_dump() if hasattr(decision, "model_dump") else dict(decision)
    next_node = data.get("next")
    reason = str(data.get("reason") or "")[:200]
    turns = int(state.get("supervisor_turns", 0))
    search_calls = int(state.get("search_calls", 0))
    n_chunks = int((state.get("search_report") or {}).get("n_chunks", 0))

    if next_node == "search_agent":
        # 첫 검색은 원래 질문으로 한다(106번). 검색 뒤의 search_agent는 재검색이다(165번, 질의는 LLM이 쓴 것).
        if search_calls:
            result = _research_decision(state, data, reason)
        else:
            result = _decision("search_agent", reason, guard=None)
    elif next_node == "writer_agent":
        if not search_calls:
            result = {**_decision("search_agent", "검색 전 작성 금지"), "guard": "write_before_search"}  # 106번: 질문으로
        elif not _latest_written(state):
            report = state.get("search_report") or {}
            if report.get("graph_low_score_reviewed") and report.get("sufficient") is not True:
                result = _no_evidence_refusal("그래프 이웃 재검토에서 근거 부족", guard="graph_low_score_insufficient")
            elif n_chunks == 0:
                result = _no_evidence_refusal("검색 근거 0건이라 작성 금지", guard="write_without_evidence")
            else:
                result = _decision("writer_agent", reason, guard=None)  # 첫 작성에는 지시를 싣지 않는다
        elif state.get("outcome") == "refused":
            result = _replace(state, "rewrite_after_refusal")
        elif int(state.get("redraft_calls", 0)) >= MAX_REDRAFTS or turns + 1 > MAX_SUPERVISOR_TURNS:
            result = _replace(state, "redraft_cap")
        else:
            llm_feedback = " ".join(str(data.get("writer_feedback") or "").split())[:500]
            # 105번: 검증이 실패했으면 무엇이 틀렸는지 아는 코드 문장(78번)을 항상 앞에 둔다. LLM 지시는 덧붙일 뿐이다.
            code_feedback = redraft_feedback(state) if _verification_failed(state) else ""
            feedback = " ".join(part for part in (code_feedback, llm_feedback) if part) or redraft_feedback(state)
            result = _decision("writer_agent", reason, writer_feedback=feedback, guard=None)
    elif next_node == "approval":
        if not _latest_written(state):
            result = _replace(state, "approval_before_writer")
        elif state.get("outcome") == "refused":
            result = _decision("end", "작성 단계 거절은 승인으로 보내지 않음", guard="approval_after_refusal")
        elif (
            _verification_failed(state)
            and int(state.get("redraft_calls", 0)) < MAX_REDRAFTS
            and turns + 1 <= MAX_SUPERVISOR_TURNS
        ):
            result = _decision("writer_agent", "검증 실패: 승인 전 다시 쓰기 1회",
                               writer_feedback=redraft_feedback(state), guard="approval_before_verified")
        else:
            result = _decision("approval", reason, guard=None)
    elif next_node == "refuse":
        if search_calls and not _latest_written(state) and n_chunks == 0:
            result = _no_evidence_refusal(reason or "검색 근거 0건", guard=None)
        elif search_calls and not _latest_written(state):
            # 160번: 작성 전 거절은 Supervisor LLM의 결정을 그대로 따른다. 다만 검색 에이전트의 채점이 '충분'이라고
            # 보고한 검색을 거절하는 것은 보고와 어긋나므로 작성으로 고친다(구조 규칙 하나).
            if (state.get("search_report") or {}).get("sufficient") is True:
                result = {**_decision("writer_agent", "채점이 충분이라고 본 검색은 거절하지 않음"), "guard": "refuse_despite_sufficient"}
            elif state.get("domain_check") != NOT_DISCLOSED:
                # 172번: 주제 분류가 '기재 사항 아님'으로 표시하지 않은 질문은 검색 점수만으로 거절하지 않는다.
                # 170번: 일상어로 물은 23문항 중 10건을 점수 미달로 거절했고 그중 7건은 정답 청크가 검색돼 있었다.
                # 이 질문의 거절은 작성 단계의 두 신호 규칙(72번)이 정한다.
                result = {**_decision("writer_agent", "기재 사항으로 본 질문은 검색 점수만으로 거절하지 않음"),
                          "guard": "refuse_without_not_disclosed"}
            else:
                result = _no_evidence_refusal(reason or "검색 근거가 질문과 맞지 않음", guard=None)
        elif _latest_written(state) and state.get("outcome") == "refused":
            result = _decision("end", "작성 단계 거절 그대로", guard=None)
        else:
            result = _replace(state, "refuse_without_code_condition")  # 거절은 구조로만(P10 R5)
    else:
        result = _replace(state, "invalid_next")

    if result["next"] in WORK_NODES and turns >= MAX_SUPERVISOR_TURNS:
        return _terminal(state, "visit_cap")
    return result


FLAGGED_VERDICTS = {"needs_check", "not_disclosed"}


def decision_shortcut(state: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """107번: 결과가 정해진 방문이면 (이름, 결정)을, 판단이 필요한 갈림길이면 None을 돌려준다(순수 함수).

    (a) 검색 전 → 검색(원래 질문, 106번)
    (c) 작성 완료, 검증 전부 통과, needs_check·not_disclosed 항목 없음, 거절 아님 → 승인
    (d) 110번: 검증 실패이고 다시 쓰기가 남음 → 다시 쓰기(코드 지시문)
    (e) 작성 단계 거절 → 거절 그대로. (g) 검색 근거 0건 → 거절
    (h) 166번: 재검색까지 마침(검색 상한) → 작성
    그 밖은 LLM이 고른다: 검색을 마쳤고 근거가 있으며 아직 작성 전(160번: 작성할지, 근거가 질문과 맞지 않아 거절할지),
    검증은 통과했지만 needs_check·not_disclosed 항목이 있는 초안(승인으로 넘길지, 다시 쓰게 할지).
    돌려준 결정도 guard_decision을 거치므로 상한·순서 규칙은 그대로다.
    """

    if not int(state.get("search_calls", 0)):
        return "(a) 검색 전", {"next": "search_agent", "reason": "정해진 길 (a): 검색 전, 원래 질문으로 검색"}
    report = state.get("search_report") or {}
    if not _latest_written(state):
        if int(report.get("n_chunks", 0)) > 0 and int(state.get("search_calls", 0)) >= MAX_SEARCHES:
            # 166번 (h): 재검색까지 마쳤으면 고를 것이 작성뿐이다(검색 상한). LLM을 부르지 않는다.
            # 165번에서 이 자리의 LLM은 재검색을 또 고르거나 초안 없이 승인을 골랐고 guard가 모두 작성으로 고쳤다.
            return "(h) 재검색 뒤", {"next": "writer_agent", "reason": "정해진 길 (h): 재검색을 마침, 모은 근거로 작성"}
        if int(report.get("n_chunks", 0)) > 0:
            # 160번: 작성할지 거절할지는 Supervisor LLM이 채점 보고를 읽고 정한다.
            # 초안은 미리 쓰고 있어서(speculation.py) LLM이 작성을 고르면 기다림이 없다.
            return None
        return "(g) 근거 0건", {"next": "refuse", "reason": "정해진 길 (g): 검색 근거 0건이라 거절"}
    if state.get("outcome") == "refused":
        return "(e) 작성 단계 거절", {"next": "refuse", "reason": "정해진 길 (e): 작성 단계 거절 그대로"}
    verdicts = {str(item.get("verdict")) for item in state.get("draft_items") or []}
    verified = (state.get("writer_report") or {}).get("verification_passed")
    if verified is True and not (verdicts & FLAGGED_VERDICTS):
        return "(c) 검증 통과", {"next": "approval", "reason": "정해진 길 (c): 검증 통과, 승인"}
    if verified is False:
        if int(state.get("redraft_calls", 0)) < MAX_REDRAFTS and int(state.get("supervisor_turns", 0)) + 1 <= MAX_SUPERVISOR_TURNS:
            # 지시문은 guard_decision이 코드 문장(105번)으로 채운다
            return "(d) 검증 실패", {"next": "writer_agent", "reason": "정해진 길 (d): 검증 실패, 다시 쓰기 1회"}
        # 115번 (f): 다시 쓰기(엄격 초안 포함)까지 쓰고도 검증 실패면 확인 필요로 승인 대기에 보낸다.
        # 112~114번에서 이 자리의 LLM은 매번 '다시 쓰기'를 골랐고 guard가 승인으로 고쳤다(결과에 영향 없이 약 3초).
        return "(f) 다시 쓰기 뒤 실패", {"next": "approval", "reason": "정해진 길 (f): 다시 쓰기 뒤에도 검증 실패, 확인 필요로 승인"}
    return None


def supervisor_reports(state: dict[str, Any]) -> dict[str, Any]:
    """Supervisor LLM 입력의 reports. 구조화 보고와 횟수만 담는다(청크 본문·이전 질문 없음)."""

    turns = int(state.get("supervisor_turns", 0))
    return {
        "counts": {
            "searches": int(state.get("search_calls", 0)),
            "writes": int(state.get("writer_calls", 0)),
            "redrafts": int(state.get("redraft_calls", 0)),
        },
        "remaining": {
            # 167번: 비교 질문은 재검색 대상이 아니라 남은 검색을 0으로 알린다
            "searches": 0 if _is_comparison(state) else max(0, MAX_SEARCHES - int(state.get("search_calls", 0))),
            "visits": max(0, MAX_SUPERVISOR_TURNS - turns),
            "redrafts": max(0, MAX_REDRAFTS - int(state.get("redraft_calls", 0))),
        },
        # 172번: 점수 미달일 때 거절할지 재검색할지를 가르는 신호(in_domain / not_disclosed_item / skipped_no_key / off)
        "domain_check": str(state.get("domain_check") or ""),
        "search": {k: v for k, v in (state.get("search_report") or {}).items() if k != "chunk_ids"},
        "writer": dict(state.get("writer_report") or {}),
    }


def default_decider(model: str = llm.DEFAULT_SUPERVISOR_MODEL):
    def decide(state: dict[str, Any]):
        return llm.decide_next(
            question=str(state.get("question", "")),
            corp_name=str(state.get("corp_name", "")),
            fiscal_year=int(state.get("fiscal_year") or 0),
            reports=supervisor_reports(state),
            model=model,
        )

    return decide


def supervisor_node(intake, decider, speculation=None):
    """첫 방문에만 입구 검사(intake)를 하고, 방문마다 decider → guard_decision으로 다음 일을 정한다.

    decider가 None이거나 llm 예외를 내면 코드 규칙으로 대신하고 fallback 칸에 원인을 남긴다.
    기록(supervisor_decisions)에는 방문마다 next·reason·LLM이 고른 값·fallback·guard만 남긴다(P19 R7).
    """

    def run(state: dict[str, Any]) -> dict[str, Any]:
        turns = int(state.get("supervisor_turns", 0)) + 1
        updates: dict[str, Any] = {"supervisor_turns": turns}
        if turns == 1:
            updates.update(intake(state))
        merged = {**state, **updates}
        decisions = list(state.get("supervisor_decisions") or [])
        if turns == 1 and (merged.get("target_required") or merged.get("outcome") == "refused"):
            decisions.append({"turn": turns, "next": "end", "reason": "입구 검사", "llm_next": None,
                              "fallback": None, "guard": None, "shortcut": None})
            return {**updates, "supervisor_next": "end", "supervisor_decisions": decisions}

        if turns == 1 and speculation is not None:
            speculation.allow_writer(merged)  # 110번: 입구 통과. 청크가 나오는 대로 작성을 미리 시작한다
        llm_decision, fallback = None, None
        shortcut = decision_shortcut(merged)  # 107번: 정해진 길이면 LLM을 부르지 않는다
        if shortcut is not None:
            pass
        elif decider is None:
            fallback = "no_decider"
        else:
            try:
                llm_decision = decider(merged)
            except LLM_FALLBACK_ERRORS as exc:
                fallback = type(exc).__name__
            if llm_decision is None and fallback is None:
                fallback = "no_decision"
        if shortcut is not None:
            final = guard_decision(merged, shortcut[1])
        elif fallback is None:
            final = guard_decision(merged, llm_decision)
        else:
            final = {**fallback_decision(merged), "guard": None}
        llm_next = getattr(llm_decision, "next", None) if llm_decision is not None else None
        decisions.append({
            "turn": turns, "next": final["next"], "reason": str(final.get("reason") or "")[:200],
            "llm_next": llm_next, "fallback": fallback, "guard": final.get("guard"),
            "shortcut": shortcut[0] if shortcut is not None else None,
            **({"search_query": final["search_query"]} if final.get("search_query") else {}),  # 165번: 재검색 질의
        })
        updates.update({
            "supervisor_next": final["next"],
            "writer_feedback": final.get("writer_feedback") or "",
            "search_query": final.get("search_query") or "",
            "supervisor_decisions": decisions,
        })
        if final["next"] == "refuse":
            updates.update({"outcome": "refused", "refusal_reason": final.get("refusal_reason") or "ITEM_NOT_DISCLOSED",
                            "draft_items": [], "citations": []})
        return updates

    return run


def route_from_supervisor(state: dict[str, Any], *, news_enabled: bool = False) -> str:
    """supervisor 노드가 정한 다음 일(supervisor_next)로 간다. 판단은 노드 안 guard_decision에서 끝났다.

    news_enabled(기본 꺼짐): 업무 관련 거절만 뉴스 노드를 한 번 거쳐 끝난다.
      - 입구 검사에서 '업무 관련'으로 거절된 질문(검색 전)
      - 검색까지 한 뒤 근거가 없어 거절된 질문(ITEM_NOT_DISCLOSED, 11/12 추가: docs/specs/refusal-news-after-search.md).
        주제 판정을 통과한 질문이라 분류 호출 없이 업무 관련으로 본다.
    """

    next_node = state.get("supervisor_next")
    if next_node in ("search_agent", "writer_agent", "approval"):
        return next_node
    if news_enabled and state.get("outcome") == "refused" and state.get("news_search") is None:
        entry_refusal = state.get("refusal_kind") == "work_related" and not state.get("search_calls")
        no_evidence_after_search = state.get("refusal_reason") == "ITEM_NOT_DISCLOSED" and bool(state.get("search_calls"))
        if entry_refusal or no_evidence_after_search:
            return "news_search"  # 뉴스 노드는 supervisor로 돌아오지 않는다
    return END  # refuse·end·되묻기


def counted(node, counter: str, label: str):
    """서브에이전트 어댑터 노드를 감싸 호출 횟수와 경로 기록(supervisor_trace)을 남긴다."""

    def run(state: dict[str, Any]) -> dict[str, Any]:
        output = node(state)
        return {
            **output,
            counter: int(state.get(counter, 0)) + 1,
            "supervisor_trace": [*state.get("supervisor_trace", []), label],
        }

    return run


def writer_call(node):
    """작성 에이전트 호출을 센다. 마지막 검색 결과로 이미 작성했으면 다시 쓰기(redraft_calls)로도 센다."""

    def run(state: dict[str, Any]) -> dict[str, Any]:
        redraft = _latest_written(state)
        output = dict(node(state))
        # 112번: 초안 2개 동시 작성에서 엄격 초안까지 봤으면 다시 쓰기 1회를 쓴 것으로 센다
        redraft_used = bool(output.pop("_redraft_used", False))
        return {
            **output,
            "writer_calls": int(state.get("writer_calls", 0)) + 1,
            "redraft_calls": int(state.get("redraft_calls", 0)) + (1 if redraft or redraft_used else 0),
            "written_search": int(state.get("search_calls", 0)),  # 이 초안이 몇 번째 검색 결과로 쓴 것인가
            "writer_feedback": "",  # 다음 작성에는 넘기지 않는다
            "supervisor_trace": [*state.get("supervisor_trace", []), "writer_agent:redraft" if redraft else "writer_agent"],
        }

    return run
