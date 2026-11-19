import json
import math

import pytest

from backend.rag.evaluation import (
    RetrievalEvaluationError,
    compute_retrieval_metrics,
    evaluate_files,
)
from backend.rag.retrieve import (
    RetrievalContractError,
    RetrieverNotConfigured,
    ScoredChunk,
    SearchFilters,
    retrieve,
)


def chunk(*, rank=1, corp_code="00126380", fiscal_year=2025, chapter="II"):
    return ScoredChunk(
        chunk_id=f"chunk-{rank}",
        corpus_id="corpus-123456789012345678901234",
        rcept_no="20260312000123",
        corp_code=corp_code,
        corp_name="삼성전자",
        fiscal_year=fiscal_year,
        chapter=chapter,
        section_path=("II. 사업의 내용",),
        note_no=None,
        table_title=None,
        ordinal=rank,
        content=f"검색 결과 본문 {rank}",
        content_sha256=str(rank) * 64,
        score=1.0 / rank,
        rank=rank,
        retrieval_method="vector",
    )


class Backend:
    def __init__(self, rows):
        self.rows = rows
        self.called = False

    def search(self, question, *, filters, top_k):
        self.called = True
        return self.rows


def test_retrieve_preserves_contract_fields():
    backend = Backend([chunk()])
    filters = SearchFilters(corp_code="00126380", fiscal_year=2025)

    rows = retrieve("  위험 요인  ", filters=filters, backend=backend)

    assert backend.called
    assert rows[0]["chunk_id"] == "chunk-1"
    assert rows[0]["retrieval_method"] == "vector"
    assert rows[0]["section_path"] == ["II. 사업의 내용"]


def test_retrieve_rejects_filter_violation_instead_of_post_filtering():
    backend = Backend([chunk(corp_code="99999999")])
    filters = SearchFilters(corp_code="00126380", fiscal_year=2025)

    with pytest.raises(RetrievalContractError, match="필터"):
        retrieve("위험 요인", filters=filters, backend=backend)


@pytest.mark.parametrize("question,top_k", [(" ", 10), ("질문", 0), ("질문", 101)])
def test_retrieve_rejects_invalid_input_before_backend(question, top_k):
    backend = Backend([])
    filters = SearchFilters(corp_code="00126380", fiscal_year=2025)

    with pytest.raises(RetrievalContractError):
        retrieve(question, filters=filters, top_k=top_k, backend=backend)

    assert not backend.called


def test_retrieve_requires_injected_backend():
    filters = SearchFilters(corp_code="00126380", fiscal_year=2025)

    with pytest.raises(RetrieverNotConfigured):
        retrieve("위험 요인", filters=filters)


def test_metrics_compute_recall_and_mrr_separately():
    dataset = [
        {"id": 1, "kind": "relevant", "gold_chunks": ["gold-a"]},
        {"id": 2, "kind": "relevant", "gold_chunks": ["gold-b"]},
        {"id": 3, "kind": "irrelevant", "gold_chunks": []},
    ]
    results = [
        {"id": 1, "retrieved_chunks": ["wrong", "gold-a"]},
        {"id": 2, "retrieved_chunks": ["wrong"]},
        {"id": 3, "retrieved_chunks": []},
    ]

    metrics = compute_retrieval_metrics(dataset, results, k=10)

    assert metrics["relevant_questions"] == 2
    assert metrics["irrelevant_questions"] == 1
    assert metrics["hit_at_10"] == 0.5
    assert metrics["mrr_at_10"] == 0.25


def test_metrics_separate_hit_from_recall_and_rank_with_ndcg():
    dataset = [
        {"id": 1, "kind": "relevant", "gold_chunks": ["gold-a", "gold-b"]},
        {"id": 2, "kind": "relevant", "gold_chunks": ["gold-c"]},
    ]
    results = [
        {"id": 1, "retrieved_chunks": ["x", "gold-a", "y", "z", "w", "gold-b"]},
        {"id": 2, "retrieved_chunks": ["gold-c"]},
    ]

    metrics = compute_retrieval_metrics(dataset, results, k=5)

    # 1번은 정답 둘 중 하나만 상위 5개에 있다: Hit는 1, Recall은 0.5
    first = metrics["per_question"][0]
    assert first["hit"] is True
    assert first["gold_count"] == 2
    assert first["recall"] == 0.5
    assert first["ndcg"] == pytest.approx((1 / math.log2(3)) / (1 + 1 / math.log2(3)))
    assert metrics["hit_at_5"] == 1.0
    assert metrics["recall_at_5"] == 0.75
    assert metrics["ndcg_at_5"] == pytest.approx((first["ndcg"] + 1.0) / 2)


@pytest.mark.parametrize(
    "results,match",
    [
        ([{"id": 1, "retrieved_chunks": []}, {"id": 1, "retrieved_chunks": []}], "중복"),
        ([{"id": 2, "retrieved_chunks": []}], "알 수 없는"),
        ([], "결과가 없는"),
    ],
)
def test_metrics_reject_invalid_result_ids(results, match):
    dataset = [{"id": 1, "kind": "relevant", "gold_chunks": ["gold"]}]

    with pytest.raises(RetrievalEvaluationError, match=match):
        compute_retrieval_metrics(dataset, results)


def test_evaluate_files_records_input_hashes(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    results = tmp_path / "results.jsonl"
    output = tmp_path / "metrics.json"
    dataset.write_text(
        json.dumps({"id": 1, "kind": "relevant", "gold_chunks": ["gold"]}) + "\n",
        encoding="utf-8",
    )
    results.write_text(
        json.dumps({"id": 1, "retrieved_chunks": ["gold"]}) + "\n",
        encoding="utf-8",
    )

    payload = evaluate_files(dataset, results, output, k=5)

    assert payload["hit_at_5"] == 1.0
    assert payload["mrr_at_5"] == 1.0
    assert len(payload["dataset_sha256"]) == 64
    assert json.loads(output.read_text(encoding="utf-8"))["results_sha256"] == payload[
        "results_sha256"
    ]
    assert "chunks_sha256" not in payload  # 청크 파일 없이 재면(v1) 출력 필드가 예전과 같다


def _span(item, text, rcept="20260101000001"):
    return {"item": item, "text": text, "locations": [{"rcept_no": rcept}]}


# v2 span 정본: A사(…001) 문서의 청크와, 같은 문자열을 가진 B사(…002) 문서의 청크
SPAN_CHUNKS = {
    "20260101000001": [
        ("c-loan-ii", "II장 위험관리 단기차입금 | 237,000,000 차입처 우리은행"),
        ("c-loan-note", "연결주석 16 단기차입금 | 237,000,000"),
        ("c-bond", "사채 | 519,650 만기 2026.04.09"),
        ("c-other", "관계없는 본문"),
    ],
    "20260101000002": [("b-same-text", "단기차입금 | 237,000,000")],
}
SPAN_DATASET = [
    {
        "id": 1,
        "kind": "relevant",
        "gold_chunks": ["c-loan-ii"],
        "gold_spans": [
            _span("단기차입금", "단기차입금 | 237,000,000"),
            _span("사채", "사채 | 519,650"),
        ],
    }
]


def test_span_recall_counts_items_not_chunks():
    # 단기차입금 item은 두 위치(II장·주석) 중 하나만 찾아도 찾은 것, 사채 item은 못 찾음 → 1/2
    results = [{"id": 1, "retrieved_chunks": ["c-other", "c-loan-note"]}]

    metrics = compute_retrieval_metrics(
        SPAN_DATASET, results, k=10, chunks_by_rcept=SPAN_CHUNKS
    )

    question = metrics["per_question"][0]
    assert metrics["recall_at_10"] == pytest.approx(0.5)
    assert question["gold_items"] == 2 and question["items_found"] == 1
    assert question["first_gold_rank"] == 2
    assert question["gold_count"] == 3  # 정답 청크 = 두 item의 청크 합집합(다른 회사 청크 제외)
    assert metrics["mrr_at_10"] == pytest.approx(0.5)


def test_span_recall_ignores_same_text_in_other_company():
    results = [{"id": 1, "retrieved_chunks": ["b-same-text"]}]

    metrics = compute_retrieval_metrics(
        SPAN_DATASET, results, k=10, chunks_by_rcept=SPAN_CHUNKS
    )

    assert metrics["hit_at_10"] == 0.0
    assert metrics["recall_at_10"] == 0.0


def test_span_dataset_requires_chunk_file():
    results = [{"id": 1, "retrieved_chunks": ["c-loan-ii"]}]

    with pytest.raises(RetrievalEvaluationError, match="--chunks"):
        compute_retrieval_metrics(SPAN_DATASET, results, k=10)


def test_span_item_without_holder_is_reported_unreachable():
    dataset = [
        {
            **SPAN_DATASET[0],
            "gold_spans": [*SPAN_DATASET[0]["gold_spans"], _span("잘린 span", "청크 경계에 걸친 문장")],
        }
    ]
    results = [{"id": 1, "retrieved_chunks": ["c-loan-ii", "c-bond"]}]

    metrics = compute_retrieval_metrics(dataset, results, k=10, chunks_by_rcept=SPAN_CHUNKS)

    question = metrics["per_question"][0]
    assert question["items_unreachable"] == ["잘린 span"]
    assert metrics["recall_at_10"] == pytest.approx(2 / 3)  # 잘린 item도 분모에 남아 점수를 깎는다
