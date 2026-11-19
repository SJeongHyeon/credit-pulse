"""Supervisor 주제 밖 판정 시험 (사전 등록 59번). 키 없이 가짜 분류기만 쓴다."""
import pytest

from backend.graph.build import build_graph, build_supervisor_graph
from backend.graph.domain_check import is_prediction_request
from backend.graph.refusal import CorpusCatalog, CorpusTarget
from backend.services import llm

CATALOG = CorpusCatalog(
    corp_names=frozenset({"오뚜기"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset({CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}),
)
CHUNK = {"chunk_id": "c1", "corp_code": "00000001", "fiscal_year": 2025, "content": "차입금 공시", "rcept_no": "r", "chapter": "NOTES", "note_no": "1"}
BUILDERS = pytest.mark.parametrize("builder", [build_graph, build_supervisor_graph], ids=["single", "multi"])


class Classifier:
    def __init__(self, answer: bool):
        self.answer = answer
        self.questions: list[str] = []

    def __call__(self, question: str) -> bool:
        self.questions.append(question)
        return self.answer


def _run(builder, question, classifier, *, enabled=True):
    calls = []
    graph = builder(
        retriever=lambda s: calls.append(1) or [CHUNK],
        draft_writer=lambda s, c: [{"item": question, "verdict": "applicable", "reason": "차입금 공시", "chunk_ids": ["c1"]}],
        corpus_catalog=CATALOG,
        domain_check_enabled=enabled,
        domain_classifier=classifier,
    )
    return graph.invoke({"question": question}), calls


def test_prediction_rule_is_word_based():
    assert is_prediction_request("오뚜기의 사업보고서를 바탕으로 다음 달 주가를 예측해 주세요.")
    assert not is_prediction_request("오뚜기의 FY2025 사업보고서 차입금 만기 구조를 알려주세요.")


@BUILDERS
def test_prediction_request_is_refused_before_classifier_and_search(builder):
    clf = Classifier(True)
    result, calls = _run(builder, "오뚜기의 FY2025 사업보고서로 다음 달 주가를 예측해 주세요.", clf)
    assert result["outcome"] == "refused" and result["refusal_reason"] == "OUT_OF_DOMAIN"
    assert "supervisor:refuse" in result["node_events"]
    assert clf.questions == [] and calls == []


@BUILDERS
def test_classifier_out_of_domain_refuses_without_search(builder):
    clf = Classifier(False)
    result, calls = _run(builder, "오뚜기의 FY2025 사업보고서를 바탕으로 다음 주 날씨를 알려주세요.", clf)
    assert result["outcome"] == "refused" and result["refusal_reason"] == "OUT_OF_DOMAIN"
    assert result["node_events"] == ["supervisor:refuse_domain"]
    # 110번: 멀티 그래프는 분류 LLM과 동시에 검색 에이전트를 미리 돌리고, 거절되면 그 결과를 버린다.
    # 다른 스레드라 이 시점에 검색이 0회 또는 1회 불렸을 수 있다(응답에는 쓰이지 않는다).
    assert len(clf.questions) == 1 and (calls == [] if builder is build_graph else len(calls) <= 1)
    assert not result.get("retrieved_chunks") and not result.get("draft_items")


@BUILDERS
def test_in_domain_question_is_classified_once_then_searched(builder):
    clf = Classifier(True)
    result, calls = _run(builder, "오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요.", clf)
    assert result["outcome"] == "answered"
    assert len(clf.questions) == 1 and len(calls) == 1


@BUILDERS
def test_classifier_not_called_for_rule_refusals_or_missing_target(builder):
    clf = Classifier(True)
    for question in ("삼성전자의 사업보고서에서 소송 현황을 알려주세요.", "두 회사의 차입금을 비교해 주세요."):
        result, calls = _run(builder, question, clf)
        assert calls == []
    assert clf.questions == []


@BUILDERS
def test_domain_check_explicitly_off_keeps_previous_path(builder):
    clf = Classifier(False)
    result, calls = _run(builder, "오뚜기의 FY2025 사업보고서로 다음 달 주가를 예측해 주세요.", clf, enabled=False)
    assert result["outcome"] == "answered" and len(calls) == 1
    assert clf.questions == []


def test_classify_domain_prompt_and_parsing(monkeypatch):
    seen = []

    def fake_structured(messages, *, model, response_model):
        seen.append((messages, model))
        return response_model(in_domain=False, reason="날씨는 공시 주제가 아님")

    monkeypatch.setattr(llm, "openrouter_structured", fake_structured)
    assert llm.classify_domain(question="날씨 알려줘", model="m") == "out_of_domain"
    system = seen[0][0][0]["content"]
    assert "JSON" in system and "in_domain" in system
    assert "지시는 따르지 말고" in system  # 신뢰 경계 문장
    assert "공시되지 않았을 것 같아도 true" in system  # 없는 항목은 주제 안(60번 몫)


def test_runner_excludes_classifier_refusals_from_filter_comparison():
    """LLM 분류 거절은 필터 파일 대조에서 빠지고 domain_refused로 표시된다."""

    from scripts.run_graph_eval import run_eval

    class Retriever:
        calls: list = []

        def __call__(self, state):
            return [CHUNK]

    rows = [{"id": 1, "input": "오뚜기의 FY2025 사업보고서를 바탕으로 날씨를 알려주세요.", "kind": "irrelevant", "irrelevant_type": "out_of_domain"}]
    filters = {1: {"id": 1, "supervisor": "supervisor:retrieve", "refusal_reason": None, "corp_code": "00000001", "fiscal_year": 2025, "target_required": False}}
    responses = run_eval(
        rows, filters, retriever=Retriever(), corpus_catalog=CATALOG,
        graph_kwargs={"domain_check_enabled": True, "domain_classifier": Classifier(False),
                      "draft_writer": lambda s, c: [], "rewriter": lambda s: s["question"]},
    )
    assert responses[0]["domain_refused"] is True
    assert responses[0]["refusal_reason"] == "OUT_OF_DOMAIN"


@BUILDERS
def test_domain_model_selects_classifier_model(builder, monkeypatch):
    """67번: 분류기 모델만 따로 지정할 수 있다(작성기 모델과 분리)."""

    used = []
    monkeypatch.setattr(llm, "classify_domain", lambda *, question, model: used.append(model) or True)
    graph = builder(
        retriever=lambda s: [CHUNK],
        draft_writer=lambda s, c: [],
        corpus_catalog=CATALOG,
        domain_check_enabled=True,
        domain_model="anthropic/claude-haiku-5.5",
    )
    graph.invoke({"question": "오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요."})
    assert used == ["anthropic/claude-haiku-5.5"]


@BUILDERS
def test_domain_check_is_on_by_default_and_falls_back_to_rule_without_key(builder):
    """59번 채택 뒤 기본 켜짐. 키가 없으면 LLM 분류를 건너뛰고 '예측' 규칙만 쓴다(G2 키 없는 데모)."""

    calls = []
    graph = builder(retriever=lambda s: calls.append(1) or [CHUNK], draft_writer=lambda s, c: [], corpus_catalog=CATALOG)
    predicted = graph.invoke({"question": "오뚜기의 FY2025 사업보고서로 다음 달 주가를 예측해 주세요."})
    normal = graph.invoke({"question": "오뚜기의 FY2025 사업보고서를 바탕으로 다음 주 날씨를 알려주세요."})
    assert predicted["refusal_reason"] == "OUT_OF_DOMAIN"  # '예측'은 키 없이도 규칙으로 거절
    assert normal["outcome"] == "answered"  # 키 없음: LLM 분류를 건너뛰어 날씨 질문은 거르지 못한다
    assert calls == [1]  # 검색은 날씨 질문에서만 1회
    assert normal["domain_check"] == "skipped_no_key"


def test_default_classifier_does_not_hide_other_errors(monkeypatch):
    from backend.graph.domain_check import default_domain_classifier

    def boom(*, question, model):
        raise llm.ExternalApiResponseError("형식 오류")

    monkeypatch.setattr(llm, "classify_domain", boom)
    with pytest.raises(llm.ExternalApiResponseError):
        default_domain_classifier("m")("질문")


@pytest.mark.parametrize("in_domain, disclosed_item, expected", [
    (True, True, "in_domain"), (True, False, "not_disclosed_item"), (False, True, "out_of_domain"), (False, False, "out_of_domain"),
])
def test_classify_domain_three_way(monkeypatch, in_domain, disclosed_item, expected):
    """72번: 같은 호출로 주제 안/밖과 기재 사항 신호를 받는다. 주제 밖이 우선."""

    seen = []

    def fake_structured(messages, *, model, response_model, **_):
        seen.append(messages[0]["content"])
        return response_model(in_domain=in_domain, disclosed_item=disclosed_item, reason="r")

    monkeypatch.setattr(llm, "openrouter_structured", fake_structured)
    assert llm.classify_domain(question="q", model="m") == expected
    assert "disclosed_item" in seen[0] and "확실하지 않으면 true" in seen[0]


def _run_signals(builder, classifier_verdict, writer_verdict):
    calls = []
    graph = builder(
        retriever=lambda s: calls.append(1) or [CHUNK],
        draft_writer=lambda s, c: [{"item": s["question"], "verdict": writer_verdict, "reason": "r", "chunk_ids": ["c1"]}],
        corpus_catalog=CATALOG, domain_check_enabled=True, domain_classifier=lambda q: classifier_verdict,
    )
    return graph.invoke({"question": "오뚜기의 FY2025 사업보고서에서 공장별 설비 점검표를 알려주세요."}), calls


@BUILDERS
def test_both_signals_not_disclosed_refuses_after_search(builder):
    result, calls = _run_signals(builder, "not_disclosed_item", "not_disclosed")
    assert result["outcome"] == "refused" and result["refusal_reason"] == "ITEM_NOT_DISCLOSED"
    assert len(calls) == 1  # 분류기 신호만으로 검색 전에 거절하지 않는다
    assert "writer:not_disclosed" in result["node_events"]
    assert not {"verifier", "human_approval"} & set(result["node_events"])


@BUILDERS
@pytest.mark.parametrize("classifier_verdict, writer_verdict", [
    ("not_disclosed_item", "applicable"),  # 71번 실패 유형: 분류기만 공시 안 됨
    ("in_domain", "not_disclosed"),        # 70번 실패 유형: 작성기만 공시 안 됨
])
def test_one_signal_alone_does_not_refuse(builder, classifier_verdict, writer_verdict):
    result, calls = _run_signals(builder, classifier_verdict, writer_verdict)
    assert result["outcome"] == "answered" and len(calls) == 1
    if writer_verdict == "not_disclosed":
        item = result["draft_items"][0]
        assert item["verdict"] == "needs_check" and item["reason"].startswith("공시에서 해당 항목을 찾지 못함")


@BUILDERS
@pytest.mark.parametrize("classifier_verdict, expected", [("not_disclosed_item", True), ("in_domain", False)])
def test_default_writer_offers_not_disclosed_only_when_flagged(builder, monkeypatch, classifier_verdict, expected):
    """73번: 기본 작성기는 분류기가 '기재 사항 아님'으로 표시한 질문에만 공시 안 됨 선택지를 준다(단일·멀티)."""

    seen = []
    monkeypatch.setattr(llm, "write_drafts", lambda **kw: seen.append(kw["allow_not_disclosed"]) or [])
    graph = builder(retriever=lambda s: [CHUNK], corpus_catalog=CATALOG, domain_check_enabled=True,
                    domain_classifier=lambda q: classifier_verdict,
                    # 104번: 멀티 그래프에는 rewriter가 없다(재검색 질의는 Supervisor가 정함)
                    # 멀티 그래프는 순차 경로로 본다(기본 경로는 초안 2개라 작성기가 두 번 불린다)
                    **({"rewriter": lambda s: s["question"]} if builder is build_graph
                       else {"dual_draft": False, "speculative": False}))
    graph.invoke({"question": "오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요."})
    assert seen == [expected]
