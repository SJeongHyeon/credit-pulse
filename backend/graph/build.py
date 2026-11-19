"""그래프 조립 (G4).

필수:
  - Supervisor와 서브에이전트 2개 이상 (역할과 컨텍스트를 나눈다)
  - 체크포인트 (재개 가능, DB에 저장)
  - 사람 승인(interrupt) 지점 1곳 이상
권장 순서:
  - D4(11/5) 단일 에이전트 그래프로 기준 점수를 잰다 (질의 라우팅, 문서 채점, 질의 재작성, 재검색)
  - D5(11/6) Supervisor와 서브에이전트로 나누고 같은 평가셋(v2)으로 다시 잰다
시험하기 쉽게 만드는 법:
  - 검색기와 체크포인터를 인자로 받는다: build_graph(retriever=None, checkpointer=None).
    그래야 가짜 검색기와 메모리 체크포인터로 키 없이 시험하고, 요청 수를 셀 수 있다.
  - 재작성 횟수 상한은 상수 하나로 둔다. 시험에서는 상수 대신 리터럴(예: 2)과 비교하고 retrieve 호출 횟수를 센다
    (상수끼리 비교하면 상한을 바꿔도 통과한다. docs/GATES.md 변이 확인).
  - 근거 충분 여부를 어휘 겹침 같은 점수 하나로만 정하지 말고, 기업이나 대상과 기간이 질문과 맞는지 같은 결정적 검사를 따로 둔다
    (정답이 1~2위에 있는데 점수 기준 때문에 거절하거나, 3분기를 물었는데 2분기 공시를 인용하는 실패가 DART 실험에서 나왔다).
  - 기업 목록, 낱말 가중치 같은 코퍼스 의존 값을 코드에 고정하지 않는다. 코퍼스가 커지면 새 대상을 전부 거절하게 된다.
"""

import logging
from functools import partial
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from backend.graph.approval import add_approval_nodes
from backend.graph.domain_check import (
    default_domain_classifier,
    default_refusal_kind_classifier,
    is_prediction_request,
)

from backend.graph.refusal import (
    CorpusCatalog,
    classify_question,
    kst_today,
    load_corpus_catalog,
    requested_fiscal_years,
    requires_filing_year_clarification,
)
from backend.graph import injection_guard
from backend.graph.state import AgentState
from backend.graph.trust_boundary import guarded_retriever
from backend.graph.verifier import verify_draft_items
from backend.memory import RuleStore, SemanticRuleStore
from backend.services import llm, tracing

logger = logging.getLogger(__name__)

REFUSAL_MESSAGES = {
    "OUT_OF_DOMAIN": "사업보고서 코퍼스로는 답변할 수 없는 질문입니다.",
    "CORP_NOT_IN_CORPUS": "요청한 회사는 현재 사업보고서 코퍼스에 없습니다.",
    "PERIOD_NOT_IN_CORPUS": "요청한 기간 또는 보고서 종류는 현재 코퍼스에 없습니다.",
}
# 인젝션 차단 멘트. 기존 REFUSAL_MESSAGES는 그대로 둔다(tests/test_refusal_kind.py가 지킨다).
# 어떤 신호에 걸렸는지는 알리지 않는다(우회 학습 방지).
INJECTION_REFUSAL_MESSAGE = "요청을 처리할 수 없습니다. 회사, 사업연도, 확인할 체크리스트 항목을 적어 심사 업무 질문으로 다시 입력해 주세요."
# 거절 종류별 고정 멘트(docs/specs/refusal-news-search.md). news_search_enabled가 켜진 그래프에서만 쓴다.
# 기존 REFUSAL_MESSAGES는 그대로 둔다(꺼짐일 때 응답이 달라지면 안 된다).
REFUSAL_MESSAGES_V2 = {
    "unrelated": (
        "저는 기업여신 심사 업무를 돕는 에이전트라서 날씨·일상 같은 질문에는 답변드리지 않습니다. "
        "회사명, 사업연도, 확인할 체크리스트 항목을 알려 주시면 사업보고서 근거로 도와드리겠습니다."
    ),
    "work_related_with_news": (
        "요청하신 내용은 현재 사업보고서 코퍼스로는 근거를 찾을 수 없습니다. "
        "참고용으로 관련 최신 뉴스를 함께 안내합니다. 뉴스는 공시 근거가 아니며 심사 판정과 메모 저장에는 사용되지 않습니다."
    ),
    "work_related_news_unavailable": (
        "요청하신 내용은 현재 사업보고서 코퍼스로는 근거를 찾을 수 없고, 뉴스 검색도 지금은 사용할 수 없습니다. "
        "회사명과 사업연도를 확인해 다시 질문해 주세요."
    ),
}
MAX_RETRIEVAL_ROUNDS = 3


def _append_event(state: AgentState, event: str) -> list[str]:
    return [*state.get("node_events", []), event]

def _normalize_query(value: object) -> str:
    return " ".join(str(value).split())


def _route_after_scorer(state: AgentState) -> str:
    if (
        state.get("retrieval_empty")
        or state.get("retrieval_sufficient")
        or int(state.get("retrieval_rounds", 0)) >= MAX_RETRIEVAL_ROUNDS
    ):
        return "writer"
    return "rewriter"


def _route_after_rewriter(state: AgentState) -> str:
    if state.get("rewrite_changed"):
        return "retriever"
    return "writer"

def _event_node(name: str):
    def run(state: AgentState) -> dict:
        return {"node_events": _append_event(state, name)}

    return run


def _verifier_node(state: AgentState) -> dict:
    verification_result = verify_draft_items(
        list(state.get("draft_items", [])),
        list(state.get("retrieved_chunks", [])),
        question=str(state.get("question", "")),  # 81번: 질문의 기간 표현은 대조하지 않음
    )
    return {
        **verification_result,
        "node_events": _append_event(state, "verifier"),
    }


def _cited_chunk_ids(draft: dict, chunks: list[dict]) -> list[str]:
    """69번: 작성기가 고른 chunk_ids에, evidence quote가 그대로 들어 있는 청크를 더한다.

    quote가 적힌 chunk_id가 틀려도 출처는 원문에서 찾은 위치로 정한다(특강 문서 파싱 05강).
    어느 청크에도 없는 quote는 그 evidence만 버린다(인용 범위는 63번처럼 청크 단위).
    """

    from backend.graph.verifier import _normalize_text

    cited = [str(chunk_id) for chunk_id in draft.get("chunk_ids", [])]
    contents = [(str(c["chunk_id"]), _normalize_text(c.get("content", ""))) for c in chunks]
    for ev in draft.get("evidence") or []:
        quote = _normalize_text(str(ev.get("quote", "")))
        if not quote:
            continue
        claimed = str(ev.get("chunk_id"))
        found = next((cid for cid, text in contents if cid == claimed and quote in text), None)
        found = found or next((cid for cid, text in contents if quote in text), None)
        if found is not None and found not in cited:
            cited.append(found)
    return cited


def _drop_unsafe_rules(rules: list[dict]) -> list[dict]:
    """읽기 문: 이미 저장됐거나 우회로 들어온 악성 규칙을 작성기 입력에서 뺀다(docs/specs/rule-injection-guard.md)."""

    safe = []
    for rule in rules:
        unsafe, signals = injection_guard.rule_is_unsafe(str(rule.get("instruction", "")))
        if unsafe:
            logger.warning("인젝션 의심 규칙을 적용하지 않았습니다: rule_id=%s signals=%s", rule.get("rule_id"), ",".join(signals))
            continue
        safe.append(rule)
    return safe


def _load_rules_node(
    rule_store: RuleStore | None,
    memory_store: SemanticRuleStore | None = None,
):
    def run(state: AgentState) -> dict:
        if memory_store is not None:
            return {
                "applied_rules": _drop_unsafe_rules(memory_store.search_rules(
                    str(state.get("user_id", "")), str(state.get("question", ""))
                ))
            }
        if rule_store is None:
            return {"applied_rules": []}
        return {"applied_rules": _drop_unsafe_rules(rule_store.list_rules(str(state.get("user_id", ""))))}

    return run


def _writer_node(draft_writer):
    def run(state: AgentState) -> dict:
        updates = {"node_events": _append_event(state, "writer")}
        question = str(state.get("question", "")).strip()
        checklist_items = list(state.get("checklist_items", [])) or ([question] if question else [])
        if state.get("retrieval_sufficient") is not False:
            chunks = list(state.get("retrieved_chunks", []))
            if not checklist_items:
                return updates
            selected = {
                str(item["item"]): item
                for item in draft_writer({**state, "checklist_items": checklist_items}, chunks)
                if str(item.get("item")) in checklist_items
            }
            chunks_by_id = {str(chunk["chunk_id"]): chunk for chunk in chunks}
            draft_items = []
            citations = []
            for item in checklist_items:
                draft = selected.get(item)
                if draft is None:
                    draft_items.append({
                        "item": item,
                        "verdict": "needs_check",
                        "reason": "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요.",
                        "citations": [],
                    })
                    continue
                item_citations = [
                    {
                        "chunk_id": chunk["chunk_id"],
                        "rcept_no": chunk.get("rcept_no"),
                        "chapter": chunk.get("chapter"),
                        "note_no": chunk.get("note_no"),
                        "quote": chunk.get("content", ""),
                    }
                    for chunk_id in _cited_chunk_ids(draft, chunks)
                    if (chunk := chunks_by_id.get(str(chunk_id))) is not None
                ]
                verdict = str(draft.get("verdict", "needs_check"))
                if verdict not in {"applicable", "not_applicable", "needs_check", "not_disclosed"}:
                    verdict = "needs_check"
                    item_citations = []
                if verdict == "not_disclosed":
                    item_citations = []  # 공시 안 됨은 판정 근거가 아니라 거절 사유라 인용하지 않는다
                if verdict in {"applicable", "not_applicable"} and not item_citations:
                    verdict = "needs_check"
                    item_citations = []
                    draft["reason"] = "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요."
                item_draft = {
                    "item": item,
                    "verdict": verdict,
                    "reason": str(draft.get("reason", "")),
                    "citations": item_citations,
                }
                draft_items.append(item_draft)
                citations.extend(item_citations)
            # 72번: 주제 판정(질문만 봄)과 작성기(청크를 봄)가 모두 '공시 안 됨'일 때만 거절한다(SPEC §3.3).
            # 70·71번에서 두 판정이 잘못 거절한 문항이 서로 겹치지 않았다.
            if (
                state.get("domain_check") == "not_disclosed_item"
                and draft_items
                and all(d["verdict"] == "not_disclosed" for d in draft_items)
            ):
                return {
                    **updates, "node_events": _append_event(state, "writer:not_disclosed"),
                    "outcome": "refused", "refusal_reason": "ITEM_NOT_DISCLOSED",
                    "draft_items": [], "citations": [],
                }
            # 한쪽 신호뿐이면 답하고, '공시 안 됨' 항목은 needs_check로 내린다(승인 판정 값 3종 그대로).
            for d in draft_items:
                if d["verdict"] == "not_disclosed":
                    d["verdict"] = "needs_check"
                    d["reason"] = "공시에서 해당 항목을 찾지 못함: " + d["reason"]
            return {**updates, "outcome": "answered", "refusal_reason": None,
                    "draft_items": draft_items, "citations": citations}
        updates["citations"] = []
        # 60번: 검색 결과가 0건이거나, 재검색 상한까지 채점(회사·연도 검사·LLM 채점)을 통과한 근거가 0건이면
        # '공시 없음'으로 거절한다(SPEC §3.3 "검색 후 근거 0일 때만"). 근거가 1건이라도 남으면 needs_check로 답한다.
        if state.get("retrieval_empty") or not state.get("retrieved_chunks"):
            return {
                **updates, "outcome": "refused",
                "refusal_reason": "ITEM_NOT_DISCLOSED", "draft_items": [],
            }
        return {
            **updates, "outcome": "answered", "refusal_reason": None,
            "draft_items": [
                {"item": item, "verdict": "needs_check",
                 "reason": "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요.",
                 "citations": []}
                for item in checklist_items
            ],
        }
    return run


def _route_after_writer(state: AgentState) -> str:
    return END if state.get("outcome") == "refused" else "verifier"


NEWS_TARGET_REASONS = frozenset({"CORP_NOT_IN_CORPUS", "PERIOD_NOT_IN_CORPUS"})


def _out_of_domain_refusal(state: AgentState, question: str, kind_classifier) -> dict:
    """주제 밖(classify_domain) 거절. kind_classifier가 있으면(뉴스 분기 켜짐) 거절 종류를 한 번 더 가른다.

    unrelated: 고정 멘트로 종료. work_related: 뉴스 노드가 이어받는다(refusal_kind만 남김).
    분류기가 None(키 없음)을 돌려주면 종류를 모르므로 뉴스 없이 폴백 멘트와 news_search=skipped_no_key를 남긴다.
    """

    refusal = {
        "outcome": "refused",
        "refusal_reason": "OUT_OF_DOMAIN",
        "refusal_message": REFUSAL_MESSAGES["OUT_OF_DOMAIN"],
        "citations": [],
        "draft_items": [],
        "domain_check": "out_of_domain",
        "node_events": _append_event(state, "supervisor:refuse_domain"),
    }
    if kind_classifier is None:
        return refusal
    kind = kind_classifier(question)
    if kind == "unrelated":
        return {**refusal, "refusal_kind": "unrelated", "refusal_message": REFUSAL_MESSAGES_V2["unrelated"]}
    if kind == "work_related":
        return {**refusal, "refusal_kind": "work_related"}
    return {
        **refusal,
        "refusal_kind": None,
        "news_search": "skipped_no_key",
        "refusal_message": REFUSAL_MESSAGES_V2["work_related_news_unavailable"],
    }


def _supervisor_node(
    corpus_catalog: CorpusCatalog, domain_classifier=None, prefetch_search=None, cancel_prefetch=None,
    kind_classifier=None, news_enabled: bool = False, injection_guard_enabled: bool = True,
):
    """prefetch_search(110번): 주제 분류 LLM을 부르기 직전에 불린다. 입구 검사 결과를 합친 State를 받아 첫 검색을 미리 시작한다."""

    def route(state: AgentState) -> dict:
        if state.get("outcome") == "refused":
            return {"node_events": _append_event(state, "supervisor:refuse")}

        question = str(state.get("question", ""))
        raw_review_target = state.get("review_target")
        review_target = None
        if raw_review_target is not None:
            if not isinstance(raw_review_target, dict):
                raise ValueError("review_target은 딕셔너리여야 합니다")
            corp_code = raw_review_target.get("corp_code")
            corp_name = raw_review_target.get("corp_name")
            fiscal_year = raw_review_target.get("fiscal_year")
            if not isinstance(corp_code, str) or not isinstance(corp_name, str) or not isinstance(fiscal_year, int):
                raise ValueError("review_target에는 corp_code, corp_name, fiscal_year가 필요합니다")
            review_target = next(
                (
                    target
                    for target in corpus_catalog.targets
                    if (target.corp_code, target.corp_name, target.fiscal_year)
                    == (corp_code, corp_name, fiscal_year)
                ),
                None,
            )
            if review_target is None:
                raise ValueError("review_target이 현재 코퍼스에 없습니다")
        reference_date = kst_today()
        if review_target is None and requires_filing_year_clarification(question):
            return {
                "outcome": "answered",
                "refusal_reason": None,
                "refusal_message": (
                    "접수연도만으로는 사업연도를 확정할 수 없습니다. "
                    "FY2025처럼 조회할 사업연도를 지정해 주세요."
                ),
                "draft_items": [],
                "citations": [],
                "target_required": True,
                "node_events": _append_event(state, "supervisor:target_required"),
            }

        refusal_reason = classify_question(
            question, corpus_catalog, reference_date=reference_date
        )
        # 59번: 주제 판정을 켠 그래프에서만 SPEC §1 범위 밖 '예측' 요청을 규칙으로 거른다.
        if not refusal_reason and domain_classifier is not None and is_prediction_request(question):
            refusal_reason = "OUT_OF_DOMAIN"
        if refusal_reason:
            refusal = {
                "outcome": "refused",
                "refusal_reason": refusal_reason,
                "refusal_message": REFUSAL_MESSAGES[refusal_reason],
                "citations": [],
                "draft_items": [],
                "node_events": _append_event(state, "supervisor:refuse"),
            }
            if news_enabled and refusal_reason in NEWS_TARGET_REASONS:
                # 코퍼스 밖 회사·기간은 업무 관련이 명백하다: 분류 호출 없이 뉴스 대상. '예측' 거절은 대상이 아니다.
                refusal["refusal_kind"] = "work_related"
            return refusal

        years = requested_fiscal_years(question, reference_date=reference_date)
        if review_target is None and len(years) > 1:
            return {
                "outcome": "answered",
                "refusal_reason": None,
                "refusal_message": (
                    "현재는 한 번에 한 사업연도만 조회합니다. "
                    "조회할 사업연도 하나를 지정해 주세요."
                ),
                "draft_items": [],
                "citations": [],
                "target_required": True,
                "node_events": _append_event(state, "supervisor:target_required"),
            }

        retrieval_query = " ".join(
            str(state.get("retrieval_query") or state.get("question", "")).split()
        )
        target = review_target or corpus_catalog.resolve_target(question, reference_date=reference_date)
        if target is None:
            # 뉴스 분기 켜짐: 회사가 없는 질문도 날씨·잡담이면 거절한다(그렇지 않으면 '회사명을 알려 달라'고만 되묻는다).
            if kind_classifier is not None and domain_classifier is not None:
                verdict = domain_classifier(question)
                if verdict is False or verdict == "out_of_domain":
                    return _out_of_domain_refusal(state, question, kind_classifier)
            # 63번: 회사 이름 없이 비교를 물으면 비교할 회사를 되묻는다(검색하지 않음).
            asks_comparison = "비교" in question or "두 회사" in question
            return {
                "outcome": "answered",
                "refusal_reason": None,
                "refusal_message": (
                    "비교할 회사 이름과 사업연도를 모두 적어 다시 질문해 주세요."
                    if asks_comparison
                    else "회사명과 사업연도를 포함해 다시 질문해 주세요."
                ),
                "draft_items": [],
                "citations": [],
                "target_required": True,
                "node_events": _append_event(state, "supervisor:target_required"),
            }
        # 63번: 질문에 회사가 2~3곳이면 회사별로 검색한다(SPEC §5.3). 대상(filters)은 첫 회사 그대로 둔다.
        comparison = (
            (review_target,)
            if review_target is not None
            else corpus_catalog.resolve_comparison_targets(question, reference_date=reference_date)
        )
        comparison_targets = [
            {"corp_code": t.corp_code, "corp_name": t.corp_name, "fiscal_year": t.fiscal_year} for t in comparison
        ]
        filters = {"corp_code": target.corp_code, "fiscal_year": target.fiscal_year}
        # 59번: 규칙을 통과하고 대상이 확정된 질문만 LLM 분류 1회. 비결정적 거절이라 이벤트를 따로 둔다.
        domain_check = "off"
        if domain_classifier is not None:
            if prefetch_search is not None:  # 110번: 분류 LLM을 기다리는 동안 검색 에이전트를 미리 돌린다
                prefetch_search({
                    **state, "retrieval_query": retrieval_query, "comparison_targets": comparison_targets,
                    "corp_code": target.corp_code, "corp_name": target.corp_name,
                    "fiscal_year": target.fiscal_year, "filters": filters,
                })
            verdict = domain_classifier(question)
            if verdict is False or verdict == "out_of_domain":
                if cancel_prefetch is not None:
                    cancel_prefetch()  # 미리 시작한 검색을 이 질문 안에서 끝내고 버린다
                return _out_of_domain_refusal(state, question, kind_classifier)
            # None: 키가 없어 LLM 분류를 건너뜀(규칙만 적용). 조용히 넘기지 않고 상태에 남긴다.
            if verdict is None:
                domain_check = "skipped_no_key"
            elif verdict == "not_disclosed_item":
                # 72번: 분류기 신호만으로는 거절하지 않는다(71번: 표준 기재 사항도 거절). 작성기 신호와 함께 본다.
                domain_check = "not_disclosed_item"
            else:
                domain_check = "in_domain"
        return {
            "retrieval_query": retrieval_query,
            "refusal_message": None,
            "comparison_targets": comparison_targets,
            "domain_check": domain_check,
            "corp_code": target.corp_code,
            "corp_name": target.corp_name,
            "fiscal_year": target.fiscal_year,
            "filters": filters,
            "target_required": False,
            "node_events": _append_event(state, "supervisor:retrieve"),
        }

    def run(state: AgentState) -> dict:
        """입구 맨 앞: 질문 속 인젝션(멀티턴 누적 포함)을 코드로 거른다. LLM·검색 호출 전이다(Spec multi-turn-injection-defense)."""

        if not injection_guard_enabled:
            return route(state)
        verdict = injection_guard.assess(str(state.get("question", "")), state.get("injection_risk"))
        if verdict.blocked:
            # 질문 본문과 걸린 신호 상세는 응답에 싣지 않는다(우회 학습 방지). 로그에는 신호 이름·점수만 남긴다.
            logger.warning(
                "인젝션 의심 차단 review=%s signals=%s turn_score=%s risk=%s",
                state.get("review_id"), ",".join(verdict.signals), verdict.turn_score, verdict.risk,
            )
            return {
                "outcome": "refused",
                "refusal_reason": "INJECTION_SUSPECTED",
                "refusal_message": INJECTION_REFUSAL_MESSAGE,
                "refusal_kind": None,
                "citations": [],
                "draft_items": [],
                "injection_risk": verdict.state,
                "node_events": _append_event(state, "supervisor:refuse_injection"),
            }
        updates = route(state)
        if verdict.state is not None:
            updates = {**updates, "injection_risk": verdict.state}
        return updates

    return run


def _refusal_kind_classifier(enabled: bool, domain_classifier, classifier, model: str):
    """뉴스 분기(기본 꺼짐): 끄면 None. 거절 종류 분류는 주제 판정이 켜져 있을 때만 의미가 있다."""

    if not enabled or domain_classifier is None:
        return None
    return classifier or default_refusal_kind_classifier(model)


def _default_news_search(corp_names=()):
    """기본 뉴스 검색. 질문에 서비스 코퍼스 회사명이 있으면 그 이름이 제목 또는 요약에 있는 기사만 남긴다
    (다른 계열사 기사 혼입 방지, docs/evidence/news_company_filter_2026-11-12.md). 이름이 없으면 거르지 않는다."""

    def search(question: str) -> dict:
        from backend.services import news_search as news

        names = news.find_company_names(question, corp_names)
        return news.search_news(news.build_news_query(question), require_company_names=names)

    return search


def _news_search_node(search):
    """업무 관련 거절에 뉴스 참고 항목을 붙이고 끝낸다. 질문당 search 1회(재시도 없음). 뉴스는 공시 근거가 아니다."""

    def run(state: AgentState) -> dict:
        result = search(str(state.get("question", "")))
        items = list(result.get("items") or [])[:5]
        status = str(result.get("status"))
        message = (
            REFUSAL_MESSAGES_V2["work_related_with_news"]
            if status == "ok" and items
            else REFUSAL_MESSAGES_V2["work_related_news_unavailable"]
        )
        return {
            "refusal_kind": "work_related",  # 검색 뒤 근거 없음 거절은 여기서 처음 정해진다(입구 거절은 이미 같은 값)
            "news_items": items,
            "news_search": status,
            "news_calls": int(state.get("news_calls", 0)) + int(result.get("calls", 0)),
            "refusal_message": message,
            "citations": [],
            "draft_items": [],
            "node_events": _append_event(state, "news_search"),
        }

    return run


def _domain_classifier(enabled: bool, classifier, model: str):
    """59번 주제 판정(기본 켜짐): 끄면 None(D4~64번과 같은 경로), 켜면 주입값 또는 llm 기본 분류기."""

    if not enabled:
        return None
    return classifier or default_domain_classifier(model)


def _route_after_supervisor(state: AgentState) -> str:
    if state.get("outcome") == "refused" or state.get("target_required"):
        return END
    if int(state.get("retrieval_rounds", 0)) >= MAX_RETRIEVAL_ROUNDS:
        return "writer"
    return "retriever"


def _retriever_node(retriever):
    def run(state: AgentState) -> dict:
        retrieval_rounds = int(state.get("retrieval_rounds", 0)) + 1
        updates = {
            "retrieval_rounds": retrieval_rounds,
            "node_events": _append_event(
                state, f"retriever:round={retrieval_rounds}"
            ),
        }

        if retriever is not None:
            targets = list(state.get("comparison_targets") or [])
            if len(targets) >= 2:
                chunks = _search_each_company(retriever, state, targets)
            else:
                chunks = retriever(state)
            if chunks is not None:
                previous = state.get("retrieved_chunks", []) if retrieval_rounds > 1 else []
                updates["retrieved_chunks"] = chunks or previous

        return updates

    return run


def _search_each_company(retriever, state: AgentState, targets: list[dict]) -> list[dict]:
    """63번: 같은 질의로 회사마다 한 번씩 검색하고, 회사별 순위를 지키며 번갈아 합친다.

    120번: 회사별 검색을 동시에 실행한다(차례로 하면 회사 수만큼 검색 시간이 든다). 결과는 targets 순서대로
    모으므로 합친 청크와 순서는 차례로 실행한 것과 같다. 스레드에는 contextvars를 복사해 넘긴다(요청 마감·사용량·추적).
    """

    def search(target: dict) -> list[dict]:
        filters = {"corp_code": target["corp_code"], "fiscal_year": target["fiscal_year"]}
        return list(retriever({**state, "filters": filters}) or [])

    if len(targets) < 2:
        per_company = [search(target) for target in targets]
    else:
        import contextvars
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(targets), thread_name_prefix="company-search") as executor:
            futures = [executor.submit(contextvars.copy_context().run, search, target) for target in targets]
            per_company = [future.result() for future in futures]  # 하나라도 실패하면 그 예외를 그대로 올린다
    merged, seen = [], set()
    for rank in range(max(map(len, per_company), default=0)):
        for chunks in per_company:
            if rank < len(chunks) and chunks[rank]["chunk_id"] not in seen:
                seen.add(chunks[rank]["chunk_id"])
                merged.append(chunks[rank])
    return merged


def _scorer_node(scorer, batch_grader, llm_grading_enabled: bool):
    def run(state: AgentState) -> dict:
        retrieved_chunks = list(state.get("retrieved_chunks", []))
        # 회사·연도 결정적 검사. 비교 질문(63번)은 비교 대상 회사들의 청크를 모두 남긴다.
        allowed = {
            (target["corp_code"], target["fiscal_year"])
            for target in state.get("comparison_targets") or []
        } or {(state.get("corp_code"), state.get("fiscal_year"))}
        valid_chunks = [
            chunk
            for chunk in retrieved_chunks
            if (chunk.get("corp_code"), chunk.get("fiscal_year")) in allowed
        ]
        retrieval_empty = not retrieved_chunks
        if llm_grading_enabled and batch_grader is not None and valid_chunks:
            relevant_ids = set(batch_grader(state, valid_chunks))
            valid_chunks = [
                chunk for chunk in valid_chunks if chunk.get("chunk_id") in relevant_ids
            ]

        scorer_state = {**state, "retrieved_chunks": valid_chunks}
        sufficient = (
            bool(scorer(scorer_state))
            if scorer is not None
            else bool(valid_chunks)
        )
        should_write = (
            retrieval_empty
            or sufficient
            or int(state.get("retrieval_rounds", 0)) >= MAX_RETRIEVAL_ROUNDS
        )
        event = "scorer:write" if should_write else "scorer:rewrite"

        return {
            "retrieval_sufficient": sufficient,
            "retrieval_empty": retrieval_empty,
            "retrieved_chunks": valid_chunks,
            "node_events": _append_event(state, event),
        }

    return run

def _rewriter_node(rewriter):
    def run(state: AgentState) -> dict:
        current_query = _normalize_query(
            state.get("retrieval_query") or state.get("question", "")
        )
        rewritten_query = (
            current_query
            if rewriter is None
            else _normalize_query(rewriter(state))
        )
        if (
            str(state.get("corp_name", "")) not in rewritten_query
            or str(state.get("fiscal_year", "")) not in rewritten_query
        ):
            return {
                "retrieval_query": current_query,
                "rewrite_changed": False,
                "node_events": _append_event(state, "rewriter:target_lost"),
            }
        rewrite_changed = rewritten_query != current_query
        event = (
            f"rewriter:round={state.get('retrieval_rounds', 0)}"
            if rewrite_changed
            else "rewriter:unchanged"
        )

        return {
            "retrieval_query": rewritten_query,
            "rewrite_changed": rewrite_changed,
            "node_events": _append_event(state, event),
        }

    return run

def build_graph(
    retriever=None,
    checkpointer=None,
    scorer=None,
    rewriter=None,
    corpus_catalog: CorpusCatalog | None = None,
    batch_grader=None,
    llm_grading_enabled: bool = False,
    generation_model: str = llm.DEFAULT_GENERATION_MODEL,
    judge_model: str = llm.DEFAULT_JUDGE_MODEL,
    draft_writer=None,
    memo_saver=None,
    memory_store: SemanticRuleStore | None = None,
    approval_enabled: bool = False,
    domain_check_enabled: bool = True,
    domain_classifier=None,
    domain_model: str | None = None,
    injection_guard_enabled: bool = True,  # 질문 속 인젝션·멀티턴 누적 가드(docs/specs/multi-turn-injection-defense.md)
):
    if approval_enabled and checkpointer is None:
        # 승인 대기를 메모리에만 두면 재시작 때 사라진다(2주09). 체크포인터 없이 켜지 않는다.
        raise ValueError("approval_enabled에는 checkpointer가 필요합니다")
    if generation_model == judge_model:
        raise ValueError("생성 모델과 judge 모델은 달라야 합니다")
    if corpus_catalog is None:
        corpus_catalog = load_corpus_catalog(Path("data/manifest.jsonl"))
    if llm_grading_enabled and batch_grader is None:
        def batch_grader(state, chunks):
            return llm.grade_chunks(
                question=str(state.get("question", "")),
                chunks=chunks,
                checklist_items=list(state.get("checklist_items", [])),
                model=judge_model,
            )
    if rewriter is None:
        def rewriter(state):
            return llm.rewrite_query(
                question=str(state.get("retrieval_query") or state.get("question", "")),
                corp_name=str(state["corp_name"]),
                fiscal_year=int(state["fiscal_year"]),
                model=generation_model,
            )
    if draft_writer is None:
        def draft_writer(state, chunks):
            return llm.write_drafts(
                question=str(state.get("question", "")),
                checklist_items=list(state.get("checklist_items", [])),
                chunks=chunks,
                model=generation_model,
                # 73번: 주제 판정이 '기재 사항 아님'으로 표시한 질문에만 '공시 안 됨' 선택지를 준다
                allow_not_disclosed=state.get("domain_check") == "not_disclosed_item",
            )

    graph = StateGraph(AgentState)

    graph.add_node("load_rules", _load_rules_node(None, memory_store))
    graph.add_node("supervisor", _supervisor_node(corpus_catalog, _domain_classifier(domain_check_enabled, domain_classifier, domain_model or generation_model), injection_guard_enabled=injection_guard_enabled))
    # 89번: 검색 결과의 무결성·지시형 문장을 작성기 전에 거른다(backend/graph/trust_boundary.py)
    graph.add_node("retriever", _retriever_node(guarded_retriever(retriever)))
    graph.add_node(
        "scorer", _scorer_node(scorer, batch_grader, llm_grading_enabled)
    )
    graph.add_node("rewriter", _rewriter_node(rewriter))
    graph.add_node("writer", _writer_node(draft_writer))
    graph.add_node("verifier", _verifier_node)
    approval_start = add_approval_nodes(
        graph,
        memo_saver=memo_saver,
        approval_enabled=approval_enabled,
        memory_store=memory_store,
    )

    graph.add_edge(START, "load_rules")
    graph.add_edge("load_rules", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        {"retriever": "retriever", "writer": "writer", END: END},
    )
    graph.add_edge("retriever", "scorer")
    graph.add_conditional_edges(
        "scorer",
        _route_after_scorer,
        {"writer": "writer", "rewriter": "rewriter"},
    )
    graph.add_conditional_edges(
        "rewriter",
        _route_after_rewriter,
        {"retriever": "retriever", "writer": "writer"},
    )
    graph.add_conditional_edges(
        "writer", _route_after_writer, {"verifier": "verifier", END: END}
    )
    graph.add_edge("verifier", approval_start)

    return graph.compile(checkpointer=checkpointer)


def build_supervisor_graph(
    retriever=None,
    checkpointer=None,
    scorer=None,
    corpus_catalog: CorpusCatalog | None = None,
    generation_model: str = llm.DEFAULT_WRITER_MODEL,  # 122번: 작성(초안·다시 쓰기) 모델
    judge_model: str = llm.DEFAULT_JUDGE_MODEL,
    draft_writer=None,
    memo_saver=None,
    approval_enabled: bool = False,
    domain_check_enabled: bool = True,
    domain_classifier=None,
    domain_model: str | None = None,  # 주제 분류·거절 종류 분류 모델. None이면 llm.DEFAULT_GENERATION_MODEL(122번: 작성 모델을 따라가지 않음)
    rule_store: RuleStore | None = None,
    memory_store: SemanticRuleStore | None = None,
    writer_reasoning: dict | None = None,  # 97번: 작성 호출의 OpenRouter 추론 설정. None이면 모델의 기본값(llm.default_writer_reasoning)
    writer_reason_numbers: bool = False,  # 98번: 작성기 지시문에 'reason에 근거 숫자 적기' 문장
    supervisor_decider=None,  # 104번: 시험용 주입(state) -> llm.SupervisorDecision | dict | None
    supervisor_model: str = llm.DEFAULT_SUPERVISOR_MODEL,
    search_grader=None,  # 104번: 시험용 주입(state, chunks) -> {"relevant_chunk_ids", "sufficient", "missing_items"}
    search_grading_enabled: bool = True,
    rerank_min_score: float | None = None,  # 103번 τ. None이면 검색기 조합의 설정값(없으면 게이트 없음)
    graph_low_score_review: bool | None = None,  # 그래프 이웃이 저점수 1위인 경우 채점기로 재검토
    search_grader_model: str = llm.DEFAULT_SEARCH_GRADER_MODEL,  # 검색 채점 모델(109번 gpt-4.1-mini → 125번 gemini-3.5-flash-lite)
    speculative: bool = True,  # 110번: 주제 분류·채점을 기다리는 동안 검색·작성 에이전트를 미리 돌린다
    dual_draft: bool = True,  # 112번: 초안 2개(보통 + 엄격 지시)를 동시에 만들어 검증을 통과한 것을 쓴다
    grade_reorder: bool = False,  # 117번(미채택, 기본 꺼짐): 채점이 맞다고 본 청크를 작성기 입력 앞쪽에
    # 125·126번: 채점 결과를 작성기에 넘긴다(고른 청크를 앞에). 채점이 부족하다고 하면 빠진 항목을 작성기에 알린다(재검색은 159번에서 없앴다)
    grade_to_writer: bool = True,
    news_search_enabled: bool = False,  # 거절 질문 뉴스 참고 첨부(기본 꺼짐, docs/specs/refusal-news-search.md)
    refusal_kind_classifier=None,  # 시험용 주입: question -> "unrelated" | "work_related" | None(키 없음)
    news_search=None,  # 시험용 주입: question -> {"status", "items", "calls"}
    first_verified_draft: bool = True,  # 120번: 먼저 끝나 검증을 통과한 엄격 초안을 쓴다(writer_agent.py)
    injection_guard_enabled: bool = True,  # 질문 속 인젝션·멀티턴 누적 가드(docs/specs/multi-turn-injection-defense.md)
):
    """D5 Supervisor + 서브에이전트(검색, 작성·검증). 104번 구조(SPEC §5.3).

    supervisor가 방문마다 LLM(supervisor_decider, 기본 llm.decide_next)으로 다음 일을 고르고 코드 안전장치가 고친다.
    여기서는 조립만 한다. 판단은 backend/graph/supervisor_flow.py, 서브그래프·인계는 backend/graph/agents/에 있다.
    """
    # agents 모듈이 이 파일의 노드 함수를 import하므로, 순환 import를 피하려고 함수 안에서 불러온다.
    from backend.graph.agents.search_agent import (
        SEARCH_INPUT_KEYS, build_search_agent, search_agent_node, select_search_input,
    )
    from backend.graph.agents.writer_agent import (
        STRICT_DRAFT_FEEDBACK, build_writer_agent, select_writer_input, writer_agent_node,
    )
    from backend.graph.speculation import Speculation
    from backend.graph.supervisor_flow import (
        counted,
        default_decider,
        news_turn_start_node,
        route_from_supervisor,
        supervisor_node,
        turn_start_node,
        writer_call,
    )

    if approval_enabled and checkpointer is None:
        raise ValueError("approval_enabled에는 checkpointer가 필요합니다")
    if generation_model == judge_model:
        raise ValueError("생성 모델과 judge 모델은 달라야 합니다")
    if corpus_catalog is None:
        corpus_catalog = load_corpus_catalog(Path("data/manifest.jsonl"))
    if rerank_min_score is None:
        # 103번: τ는 검색 조합(리랭커)마다 다르다. 조합에 값이 없으면 게이트 없이 돈다.
        rerank_min_score = getattr(getattr(retriever, "combo", None), "rerank_top1_min_score", None)
    if graph_low_score_review is None:
        graph_low_score_review = bool(getattr(retriever, "graph_low_score_review", False))
    domain_model = domain_model or llm.DEFAULT_GENERATION_MODEL
    if writer_reasoning is None:
        writer_reasoning = llm.default_writer_reasoning(generation_model)
    classifier = _domain_classifier(domain_check_enabled, domain_classifier, domain_model)
    strict_feedback = STRICT_DRAFT_FEEDBACK if dual_draft else None  # 112번: 초안 2개 동시 작성
    # 110번: 미리 실행(speculation.py). 주제 분류 LLM이 있을 때만 쓴다(기다릴 것이 없으면 겹칠 것도 없다).
    speculation = None
    if speculative and retriever is not None and classifier is not None:
        speculation = Speculation(
            run_search=lambda handoff: search_agent.invoke(handoff),
            run_writer=lambda handoff: writer_agent.invoke(handoff),
            writer_input=select_writer_input,
            strict_feedback=strict_feedback,
        )
    search_agent = build_search_agent(
        on_chunks=None if speculation is None else (lambda sub_state: speculation.on_chunks(
            {key: sub_state[key] for key in SEARCH_INPUT_KEYS if key in sub_state},
            {key: sub_state[key] for key in ("retrieved_chunks", "retrieval_sufficient", "retrieval_empty", "grader_missing_items")
             if key in sub_state},
        )),
        grade_reorder=grade_reorder,
        grade_to_writer=grade_to_writer,
        retriever=guarded_retriever(retriever),  # 89번: 신뢰 경계(trust_boundary.py)
        scorer=scorer,
        grader=search_grader,
        judge_model=search_grader_model,
        grading_enabled=search_grading_enabled,
        rerank_min_score=rerank_min_score,
        graph_low_score_review=graph_low_score_review,
    )
    writer_agent = build_writer_agent(
        draft_writer=draft_writer,
        generation_model=generation_model,
        writer_reasoning=writer_reasoning,
        writer_reason_numbers=writer_reason_numbers,
    )
    decider = supervisor_decider if supervisor_decider is not None else default_decider(supervisor_model)
    graph = StateGraph(AgentState)
    intake = _supervisor_node(
        corpus_catalog, classifier,
        # 113번: 주제 판정을 가장 흔한 값(in_domain)으로 가정해 작성도 분류보다 먼저 시작한다
        None if speculation is None else (lambda pre_state: speculation.start_search(
            select_search_input(pre_state), {**pre_state, "domain_check": "in_domain"})),
        None if speculation is None else speculation.cancel,
        _refusal_kind_classifier(news_search_enabled, classifier, refusal_kind_classifier, domain_model),
        news_enabled=news_search_enabled,
        injection_guard_enabled=injection_guard_enabled,
    )

    def turn_start(state):
        if speculation is not None:
            speculation.reset()  # 앞 질문에서 쓰이지 않은 미리 실행은 버린다
        # 뉴스 분기가 꺼져 있으면 새 키를 쓰지 않는다(State가 기존과 한 글자도 다르지 않게)
        return news_turn_start_node(state) if news_search_enabled else turn_start_node(state)

    # Langfuse 계층(docs/specs/trace-hierarchy.md): 질문 → load_rules, supervisor → 에이전트 → 노드 → 외부 호출.
    # 아래 tracing.* 래퍼는 기록만 한다(노드 함수의 인자·반환값·예외는 그대로, 꺼져 있으면 통과).
    # search_agent·writer_agent 관측은 어댑터 노드와 speculation이 supervisor 아래에 명시적으로 연다.
    graph.add_node("turn_start", turn_start)
    graph.add_node("supervisor", tracing.under_supervisor(supervisor_node(intake, decider, speculation)))
    graph.add_node("load_rules", tracing.node(
        "load_rules", _load_rules_node(rule_store, memory_store),
        summarize=lambda result: {"applied_rules": len(result.get("applied_rules") or [])}))
    graph.add_node("search_agent", counted(search_agent_node(search_agent, speculation), "search_calls", "search_agent"))
    graph.add_node("writer_agent", writer_call(writer_agent_node(
        writer_agent, speculation, strict_feedback, first_verified=first_verified_draft)))
    # 승인·저장 노드는 단일 그래프와 같은 것을 쓴다(backend/graph/approval.py). 승인은 부모 그래프에 둔다.
    approval_start = add_approval_nodes(
        graph,
        memo_saver=memo_saver,
        approval_enabled=approval_enabled,
        memory_store=memory_store,
        traced=True,
    )

    graph.add_edge(START, "turn_start")
    graph.add_edge("turn_start", "load_rules")
    graph.add_edge("load_rules", "supervisor")
    # 허브 구조: 서브에이전트는 끝나면 supervisor로 돌아오고, supervisor가 정한 다음 일(supervisor_next)로 간다.
    graph.add_conditional_edges(
        "supervisor",
        partial(route_from_supervisor, news_enabled=news_search_enabled),
        {"search_agent": "search_agent", "writer_agent": "writer_agent", "approval": approval_start, END: END,
         **({"news_search": "news_search"} if news_search_enabled else {})},
    )
    if news_search_enabled:
        # 업무 관련 입구 거절만 뉴스 노드를 한 번 거쳐 끝난다(supervisor로 돌아오지 않는다).
        graph.add_node("news_search", tracing.node(
            "news_search", _news_search_node(news_search or _default_news_search(corpus_catalog.corp_names)), as_type="tool",
            group=tracing.SUPERVISOR,
            summarize=lambda result: {"news_search": result.get("news_search"), "items": len(result.get("news_items") or [])}))
        graph.add_edge("news_search", END)
    graph.add_edge("search_agent", "supervisor")
    graph.add_edge("writer_agent", "supervisor")

    return graph.compile(checkpointer=checkpointer)
