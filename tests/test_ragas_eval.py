"""RAGAS 참고 지표 스크립트(151번)의 문항 고르기와 요약. ragas·외부 호출 없이 본다."""

import pytest

from backend.services import llm
from scripts import run_ragas_eval as ragas_eval

DATASET = [
    {"id": 1, "kind": "relevant", "input": "A사의 2025년 차입금은?"},
    {"id": 2, "kind": "relevant", "input": "두 회사를 비교해 주세요."},
    {"id": 3, "kind": "irrelevant", "input": "오늘 날씨는?"},
    {"id": 4, "kind": "relevant", "input": "A사의 소송은 몇 건인가요?"},
]
CHUNKS = {"chunk-a": "차입금 100", "chunk-b": "차입금 주석"}


def _answered(row_id, items):
    return {"id": row_id, "outcome": "answered", "draft_items": items}


def test_only_relevant_answers_with_citations_are_scored():
    """거절·되묻기·인용 없는 '확인 필요'는 RAGAS에 넣지 않고 사유와 함께 뺀다(Faithfulness가 정직한 거절을 0점으로 매김)."""

    results = [
        _answered(1, [{"verdict": "applicable", "reason": "차입금은 100입니다.",
                       "citations": [{"chunk_id": "chunk-a"}, {"chunk_id": "chunk-b"}, {"chunk_id": "chunk-a"}]}]),
        _answered(2, []),  # 되묻기
        {"id": 3, "outcome": "refused", "draft_items": []},
        _answered(4, [{"verdict": "needs_check", "reason": "근거 부족", "citations": []}]),
    ]

    samples, skipped = ragas_eval.build_samples(DATASET, results, CHUNKS)

    assert [s["id"] for s in samples] == [1]
    assert samples[0]["user_input"] == "A사의 2025년 차입금은?"
    assert samples[0]["response"] == "차입금은 100입니다."
    assert samples[0]["retrieved_contexts"] == ["차입금 100", "차입금 주석"]  # 같은 청크는 한 번만
    assert skipped == [
        {"id": 2, "reason": "no_cited_answer"},
        {"id": 3, "reason": "irrelevant_question"},
        {"id": 4, "reason": "no_cited_answer"},
    ]


def test_a_cited_chunk_missing_from_the_chunk_file_stops_the_run():
    results = [_answered(1, [{"reason": "답", "citations": [{"chunk_id": "chunk-zz"}]}])]

    with pytest.raises(KeyError):
        ragas_eval.build_samples(DATASET, results, CHUNKS)


def test_summary_keeps_unscored_questions_out_of_the_mean():
    records = [
        {"id": 1, "answer_relevancy": 0.8, "faithfulness": 1.0},
        {"id": 4, "answer_relevancy": 0.4, "faithfulness": None},
    ]

    summary = ragas_eval.summarize(records, [{"id": 2, "reason": "no_cited_answer"}, {"id": 3, "reason": "irrelevant_question"}])

    assert summary["scored_questions"] == 2
    assert summary["skipped"] == {"no_cited_answer": 1, "irrelevant_question": 1}
    assert summary["answer_relevancy"] == {"n": 2, "mean": 0.6, "min": 0.4, "below_0_5": [4], "not_scored": []}
    assert summary["faithfulness"] == {"n": 1, "mean": 1.0, "min": 1.0, "below_0_5": [], "not_scored": [4]}


def test_eval_judge_text_needs_a_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(llm.ExternalApiKeyMissing):
        llm.eval_judge_text("질문", model="openai/gpt-4.1")
