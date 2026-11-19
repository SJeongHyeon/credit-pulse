"""비교 질문 회사별 분리 시험 (사전 등록 63번, SPEC §5.3). 키 없이 가짜 검색기·작성기만 쓴다."""
import pytest

from backend.graph.build import build_graph, build_supervisor_graph
from backend.graph.refusal import CorpusCatalog, CorpusTarget
from backend.services import llm

CATALOG = CorpusCatalog(
    corp_names=frozenset({"LG화학", "코웨이", "LG"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset({
        CorpusTarget(corp_name="LG화학", corp_code="00356361", fiscal_year=2025),
        CorpusTarget(corp_name="코웨이", corp_code="00101488", fiscal_year=2025),
        CorpusTarget(corp_name="LG", corp_code="00120021", fiscal_year=2025),
    }),
)
BUILDERS = pytest.mark.parametrize("builder", [build_graph, build_supervisor_graph], ids=["single", "multi"])


def _chunk(corp_code, corp_name, n):
    return {"chunk_id": f"{corp_code}-{n}", "corp_code": corp_code, "corp_name": corp_name, "fiscal_year": 2025,
            "content": f"{corp_name} 외화 위험 {n}", "rcept_no": "r", "chapter": "NOTES", "note_no": "1"}


class Retriever:
    def __init__(self):
        self.filters = []

    def __call__(self, state):
        f = state["filters"]
        self.filters.append(f["corp_code"])
        name = {"00356361": "LG화학", "00101488": "코웨이", "00120021": "LG"}[f["corp_code"]]
        return [_chunk(f["corp_code"], name, n) for n in (1, 2)]


def test_comparison_targets_keep_question_order_and_drop_contained_names():
    targets = CATALOG.resolve_comparison_targets("LG화학과 코웨이의 외화 위험 노출을 비교해 주세요.")
    assert [t.corp_name for t in targets] == ["LG화학", "코웨이"]  # 'LG'는 'LG화학'에 포함돼 빠진다
    assert CATALOG.resolve_comparison_targets("LG화학의 FY2025 사업보고서 외화 위험") == []


@BUILDERS
def test_comparison_question_searches_each_company_and_keeps_both(builder):
    retriever, seen = Retriever(), []

    def writer(state, chunks):
        seen.append(chunks)
        return [{"item": state["checklist_items"][0], "verdict": "applicable", "reason": "외화 위험 공시",
                 "chunk_ids": [chunks[0]["chunk_id"], chunks[1]["chunk_id"]]}]

    result = builder(retriever=retriever, draft_writer=writer, corpus_catalog=CATALOG).invoke(
        {"question": "LG화학과 코웨이의 연결 기준 외화 위험 노출을 비교해 주세요."}
    )
    assert retriever.filters == ["00356361", "00101488"]  # 회사마다 한 번
    assert [c["chunk_id"] for c in seen[0]] == ["00356361-1", "00101488-1", "00356361-2", "00101488-2"]  # 번갈아 합침
    assert {c["chunk_id"] for c in result["citations"]} == {"00356361-1", "00101488-1"}
    assert result["filters"]["corp_code"] == "00356361"  # 대상은 첫 회사 그대로


@BUILDERS
def test_single_company_question_is_unchanged(builder):
    retriever = Retriever()
    result = builder(retriever=retriever, draft_writer=lambda s, c: [], corpus_catalog=CATALOG).invoke(
        {"question": "코웨이의 FY2025 사업보고서 외화 위험을 알려주세요."}
    )
    assert retriever.filters == ["00101488"]
    assert result["comparison_targets"] == []


@BUILDERS
def test_comparison_without_company_names_asks_back_without_search(builder):
    retriever = Retriever()
    result = builder(retriever=retriever, corpus_catalog=CATALOG).invoke({"question": "두 회사의 차입금 만기 구조를 비교해 주세요."})
    assert retriever.filters == []
    assert result["target_required"] is True
    assert "비교할 회사 이름" in result["refusal_message"]


def test_writer_input_labels_company_only_when_chunks_span_companies(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "openrouter_structured", lambda messages, **kw: (seen.append(messages), llm._DraftResponse(draft_items=[]))[1])
    llm.write_drafts(question="q", checklist_items=["x"], chunks=[_chunk("00356361", "LG화학", 1)])
    llm.write_drafts(question="q", checklist_items=["x"], chunks=[_chunk("00356361", "LG화학", 1), _chunk("00101488", "코웨이", 1)])
    assert "corp_name" not in seen[0][1]["content"]  # 회사 1곳: 입력이 이전과 같다
    assert "corp_name" in seen[1][1]["content"] and "코웨이" in seen[1][1]["content"]
