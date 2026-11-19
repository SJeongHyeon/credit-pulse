import json
import pytest
from evals.response_metrics import calculate_response_metrics, main


def test_calculate_response_metrics_counts_correct_refusal():
    dataset_rows = [
        {
            "id": 16,
            "kind": "irrelevant",
            "irrelevant_type": "out_of_domain",
            "gold_chunks": [],
        }
    ]
    response_rows = [
        {
            "id": 16,
            "outcome": "refused",
            "refusal_reason": "OUT_OF_DOMAIN",
            "citations": [],
            "draft_items": [],
        }
    ]

    metrics = calculate_response_metrics(dataset_rows, response_rows)

    assert metrics["correct_refusal"] == {
        "numerator": 1,
        "denominator": 1,
        "rate": 1.0,
    }

def test_calculate_response_metrics_counts_over_refusal():
    dataset_rows = [
        {
            "id": 1,
            "kind": "relevant",
            "gold_chunks": ["chunk-a"],
        }
    ]
    response_rows = [
        {
            "id": 1,
            "outcome": "refused",
            "refusal_reason": "OUT_OF_DOMAIN",
            "citations": [],
            "draft_items": [],
        }
    ]

    metrics = calculate_response_metrics(dataset_rows, response_rows)

    assert metrics["over_refusal"] == {
        "numerator": 1,
        "denominator": 1,
        "rate": 1.0,
    }

def test_calculate_response_metrics_counts_hallucination_risk():
    dataset_rows = [
        {
            "id": 16,
            "kind": "irrelevant",
            "irrelevant_type": "out_of_domain",
            "gold_chunks": [],
        }
    ]
    response_rows = [
        {
            "id": 16,
            "outcome": "answered",
            "refusal_reason": None,
            "citations": [{"chunk_id": "chunk-incorrect"}],
            "draft_items": [],
        }
    ]

    metrics = calculate_response_metrics(dataset_rows, response_rows)

    assert metrics["hallucination_risk"] == {
        "numerator": 1,
        "denominator": 1,
        "rate": 1.0,
    }

def test_calculate_response_metrics_counts_correct_citation():
    dataset_rows = [
        {
            "id": 1,
            "kind": "relevant",
            "gold_chunks": ["chunk-a"],
        }
    ]
    response_rows = [
        {
            "id": 1,
            "outcome": "answered",
            "refusal_reason": None,
            "citations": [{"chunk_id": "chunk-a"}],
            "draft_items": [],
        }
    ]

    metrics = calculate_response_metrics(dataset_rows, response_rows)

    assert metrics["correct_citation"] == {
        "numerator": 1,
        "denominator": 1,
        "rate": 1.0,
    }

def test_calculate_response_metrics_counts_wrong_citation():
    dataset_rows = [
        {
            "id": 1,
            "kind": "relevant",
            "gold_chunks": ["chunk-a"],
        }
    ]
    response_rows = [
        {
            "id": 1,
            "outcome": "answered",
            "refusal_reason": None,
            "citations": [{"chunk_id": "chunk-wrong"}],
            "draft_items": [],
        }
    ]

    metrics = calculate_response_metrics(dataset_rows, response_rows)

    assert metrics["wrong_citation"] == {
        "numerator": 1,
        "denominator": 1,
        "rate": 1.0,
    }

def test_main_reads_jsonl_and_writes_metrics(tmp_path):
    dataset_path = tmp_path / "dataset.jsonl"
    responses_path = tmp_path / "responses.jsonl"
    output_path = tmp_path / "metrics.json"

    dataset_path.write_text(
        json.dumps(
            {
                "id": 16,
                "kind": "irrelevant",
                "irrelevant_type": "out_of_domain",
                "gold_chunks": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    responses_path.write_text(
        json.dumps(
            {
                "id": 16,
                "outcome": "refused",
                "refusal_reason": "OUT_OF_DOMAIN",
                "citations": [],
                "draft_items": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--dataset",
            str(dataset_path),
            "--responses",
            str(responses_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "correct_refusal": {
            "numerator": 1,
            "denominator": 1,
            "rate": 1.0,
        },
        "over_refusal": {
            "numerator": 0,
            "denominator": 0,
            "rate": 0.0,
        },
        "correct_citation": {
            "numerator": 0,
            "denominator": 0,
            "rate": 0.0,
        },
        "wrong_citation": {
            "numerator": 0,
            "denominator": 0,
            "rate": 0.0,
        },
        "hallucination_risk": {
            "numerator": 0,
            "denominator": 1,
            "rate": 0.0,
        },
    }

def test_main_rejects_response_ids_that_do_not_match_dataset(tmp_path):
    dataset_path = tmp_path / "dataset.jsonl"
    responses_path = tmp_path / "responses.jsonl"
    output_path = tmp_path / "metrics.json"

    dataset_path.write_text(
        json.dumps(
            {
                "id": 16,
                "kind": "irrelevant",
                "irrelevant_type": "out_of_domain",
                "gold_chunks": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    responses_path.write_text(
        json.dumps(
            {
                "id": 999,
                "outcome": "refused",
                "refusal_reason": "OUT_OF_DOMAIN",
                "citations": [],
                "draft_items": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="응답 ID"):
        main(
            [
                "--dataset",
                str(dataset_path),
                "--responses",
                str(responses_path),
                "--output",
                str(output_path),
            ]
        )
