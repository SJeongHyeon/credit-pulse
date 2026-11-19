"""검색 뒤 '근거 없음' 거절에도 참고 뉴스를 붙인다 (docs/specs/refusal-news-after-search.md AC-A1~A5).
키·네트워크 없이 가짜만 쓴다. 호출 상한은 리터럴 1·5와 비교하고 호출 횟수를 센다(docs/GATES.md 변이 확인)."""
import urllib.error

import pytest
from langgraph.checkpoint.memory import MemorySaver

from backend.graph.build import REFUSAL_MESSAGES_V2, build_supervisor_graph
from backend.graph.refusal import CorpusCatalog, CorpusTarget
from backend.services import news_search

CATALOG = CorpusCatalog(
    corp_names=frozenset({"오뚜기"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset({CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}),
)
CHUNK = {"chunk_id": "c1", "corp_code": "00000001", "fiscal_year": 2025, "content": "차입금 공시",
         "rcept_no": "r", "chapter": "NOTES", "note_no": "1"}
IN_CORPUS_NO_EVIDENCE = "오뚜기의 FY2025 사업보고서를 바탕으로 최근 반도체 투자 이슈를 알려주세요."
NORMAL = "오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요."
NEW_KEYS = {"refusal_kind", "news_items", "news_search", "news_calls"}


def _item(n):
    return {"title": f"제목 {n}", "description": "d", "link": f"https://n/{n}", "originallink": f"https://o{n}.kr/{n}",
            "pub_date": "Mon, 10 Nov 2026 09:00:00 +0900", "source_domain": f"o{n}.kr"}


class Rig:
    """검색 결과가 비어(evidence=False) 근거 없음으로 거절되는 그래프. kind 분류기는 호출되면 센다."""

    def __init__(self, *, evidence=False, news=None, enabled=True, checkpointer=None):
        self.retriever_calls, self.writer_calls, self.kind_questions, self.news_questions = [], [], [], []
        news_result = news if news is not None else {"status": "ok", "items": [_item(i) for i in range(7)], "calls": 1}

        def retriever(state):
            self.retriever_calls.append(1)
            return [CHUNK] if evidence else []

        def writer(state, chunks):
            self.writer_calls.append(1)
            return [{"item": state["question"], "verdict": "applicable", "reason": "차입금 공시", "chunk_ids": ["c1"]}]

        def kind_clf(question):
            self.kind_questions.append(question)
            return "work_related"

        def news_fn(question):
            self.news_questions.append(question)
            return news_result

        self.graph = build_supervisor_graph(
            retriever=retriever, draft_writer=writer, memo_saver=lambda *a, **k: None,
            corpus_catalog=CATALOG, domain_classifier=lambda question: True,  # 주제 안(in-domain)
            checkpointer=checkpointer, news_search_enabled=enabled,
            refusal_kind_classifier=kind_clf if enabled else None, news_search=news_fn if enabled else None,
            speculative=False, search_grading_enabled=False,
        )

    def run(self, question, **kw):
        return self.graph.invoke({"question": question}, **kw)


# ── AC-A1 ────────────────────────────────────────────────


def test_a1_refused_after_search_calls_news_exactly_once():
    rig = Rig()

    result = rig.run(IN_CORPUS_NO_EVIDENCE)

    assert result["outcome"] == "refused" and result["refusal_reason"] == "ITEM_NOT_DISCLOSED"
    assert len(rig.retriever_calls) >= 1  # 검색을 한 뒤의 거절이다
    assert len(rig.news_questions) == 1 and result["news_calls"] == 1
    assert len(result["news_items"]) == 5  # 가짜 클라이언트가 7건을 돌려줘도 5건
    assert result["news_search"] == "ok" and result["refusal_kind"] == "work_related"
    assert result["refusal_message"] == REFUSAL_MESSAGES_V2["work_related_with_news"]
    assert result["citations"] == [] and result["draft_items"] == []
    assert len(rig.kind_questions) == 0  # 주제 판정을 이미 통과해서 분류 호출이 없다
    assert len(rig.writer_calls) == 0


# ── AC-A2 ────────────────────────────────────────────────


@pytest.mark.parametrize("error", [
    urllib.error.HTTPError("https://openapi.naver.com", 429, "Too Many Requests", {}, None),
    TimeoutError("timed out"),
])
def test_a2_news_error_falls_back_once_without_retry(monkeypatch, error):
    monkeypatch.setenv("NAVER_HUB_CLIENT_ID", "test-id")
    monkeypatch.setenv("NAVER_HUB_CLIENT_SECRET", "test-secret")
    http_calls = []

    def failing(url, headers, timeout):
        http_calls.append(url)
        raise error

    monkeypatch.setattr(news_search, "_default_http_get", failing)
    graph = build_supervisor_graph(  # 뉴스 클라이언트를 주입하지 않아 기본 클라이언트(위에서 가짜 HTTP로 바꿈)를 쓴다
        retriever=lambda s: [], draft_writer=lambda s, c: [], memo_saver=lambda *a, **k: None,
        corpus_catalog=CATALOG, domain_classifier=lambda question: True,
        news_search_enabled=True, refusal_kind_classifier=lambda q: "work_related",
        speculative=False, search_grading_enabled=False,
    )

    result = graph.invoke({"question": IN_CORPUS_NO_EVIDENCE})

    assert len(http_calls) == 1  # 재시도 없음
    assert result["news_search"] == "error" and result["news_calls"] == 1
    assert result["refusal_message"] == REFUSAL_MESSAGES_V2["work_related_news_unavailable"]
    assert result["outcome"] == "refused" and result["citations"] == []


def test_a2_zero_news_items_uses_unavailable_message():
    rig = Rig(news={"status": "ok", "items": [], "calls": 1})

    result = rig.run(IN_CORPUS_NO_EVIDENCE)

    assert result["news_items"] == [] and result["news_calls"] == 1
    assert result["refusal_message"] == REFUSAL_MESSAGES_V2["work_related_news_unavailable"]


# ── AC-A3 ────────────────────────────────────────────────


def test_a3_answerable_question_never_calls_news():
    rig = Rig(evidence=True)

    result = rig.run(NORMAL)

    assert result["outcome"] != "refused"
    assert len(rig.news_questions) == 0 and int(result.get("news_calls") or 0) == 0
    assert not result.get("news_items")


# ── AC-A4 ────────────────────────────────────────────────


def test_a4_flag_off_keeps_old_refusal_and_new_keys_absent():
    rig = Rig(enabled=False)

    result = rig.run(IN_CORPUS_NO_EVIDENCE)

    assert result["outcome"] == "refused" and result["refusal_reason"] == "ITEM_NOT_DISCLOSED"
    assert len(rig.news_questions) == 0
    assert NEW_KEYS.isdisjoint(result.keys())
    assert result.get("refusal_message") not in REFUSAL_MESSAGES_V2.values()  # 새 멘트가 아니다


# ── AC-A5 ────────────────────────────────────────────────


def test_a5_news_does_not_leak_into_the_next_question_of_the_same_thread():
    state = {"configurable": {"thread_id": "t-after-search"}}
    first = Rig(checkpointer=MemorySaver())

    refused = first.run(IN_CORPUS_NO_EVIDENCE, config=state)
    assert len(refused["news_items"]) == 5

    second = Rig(evidence=True, checkpointer=first.graph.checkpointer)  # 같은 스레드에 정상 질문을 이어서 던진다
    answered = second.run(NORMAL, config=state)

    assert answered["outcome"] != "refused"
    assert not answered.get("news_items") and answered.get("news_search") is None
