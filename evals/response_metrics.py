import argparse
import json
from pathlib import Path


EXPECTED_REFUSAL_REASONS = {
    "out_of_domain": "OUT_OF_DOMAIN",
    "unknown_entity": "CORP_NOT_IN_CORPUS",
    "missing_attribute": "ITEM_NOT_DISCLOSED",
    "wrong_period": "PERIOD_NOT_IN_CORPUS",
}

def per_question_flags(dataset_row: dict, response: dict) -> dict[str, int]:
    """문항 하나의 응답 지표(0/1). calculate_response_metrics의 문항별 판정과 같다(G6: Langfuse 점수로 트레이스에 연결)."""

    if dataset_row["kind"] == "irrelevant":
        expected_reason = EXPECTED_REFUSAL_REASONS[dataset_row["irrelevant_type"]]
        return {
            "correct_refusal": int(response["outcome"] == "refused" and response["refusal_reason"] == expected_reason),
            "hallucination_risk": int(
                response["outcome"] != "refused" or bool(response["citations"]) or bool(response["draft_items"])
            ),
        }
    cited = {citation["chunk_id"] for citation in response["citations"]}
    gold = set(dataset_row["gold_chunks"])
    return {
        "correct_citation": int(bool(cited & gold)),
        "wrong_citation": int(bool(cited) and not cited & gold),
        "over_refusal": int(response["outcome"] == "refused"),
    }


def calculate_response_metrics(
    dataset_rows: list[dict],
    response_rows: list[dict],
) -> dict:
    responses_by_id = {row["id"]: row for row in response_rows}

    irrelevant_rows = [
        row for row in dataset_rows if row["kind"] == "irrelevant"
    ]
    correct_refusals = 0
    for row in irrelevant_rows:
        response = responses_by_id[row["id"]]
        expected_reason = EXPECTED_REFUSAL_REASONS[row["irrelevant_type"]]
        if (
            response["outcome"] == "refused"
            and response["refusal_reason"] == expected_reason
        ):
            correct_refusals += 1

    hallucination_risks = sum(
        response["outcome"] != "refused"
        or bool(response["citations"])
        or bool(response["draft_items"])
        for row in irrelevant_rows
        for response in [responses_by_id[row["id"]]]
    )

    relevant_rows = [
        row for row in dataset_rows if row["kind"] == "relevant"
    ]
    correct_citations = 0
    for row in relevant_rows:
        response = responses_by_id[row["id"]]
        cited_chunk_ids = {
            citation["chunk_id"]
            for citation in response["citations"]
        }
        if cited_chunk_ids.intersection(row["gold_chunks"]):
            correct_citations += 1

    wrong_citations = 0
    for row in relevant_rows:
        response = responses_by_id[row["id"]]
        cited_chunk_ids = {
            citation["chunk_id"]
            for citation in response["citations"]
        }
        if cited_chunk_ids and not cited_chunk_ids.intersection(row["gold_chunks"]):
            wrong_citations += 1

    over_refusals = sum(
        responses_by_id[row["id"]]["outcome"] == "refused"
        for row in relevant_rows
    )

    denominator = len(irrelevant_rows)
    relevant_denominator = len(relevant_rows)

    return {
        "correct_refusal": {
            "numerator": correct_refusals,
            "denominator": denominator,
            "rate": correct_refusals / denominator if denominator else 0.0,
        },
        "over_refusal": {
            "numerator": over_refusals,
            "denominator": relevant_denominator,
            "rate": (
                over_refusals / relevant_denominator
                if relevant_denominator
                else 0.0
            ),
        },
        "correct_citation": {
            "numerator": correct_citations,
            "denominator": relevant_denominator,
            "rate": (
                correct_citations / relevant_denominator
                if relevant_denominator
                else 0.0
            ),
        },
        "wrong_citation": {
            "numerator": wrong_citations,
            "denominator": relevant_denominator,
            "rate": (
                wrong_citations / relevant_denominator
                if relevant_denominator
                else 0.0
            ),
        },
        "hallucination_risk": {
            "numerator": hallucination_risks,
            "denominator": denominator,
            "rate": (
                hallucination_risks / denominator
                if denominator
                else 0.0
            ),
        },
    }


def _read_jsonl(path: str | Path) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _validate_response_ids(
    dataset_rows: list[dict],
    response_rows: list[dict],
) -> None:
    dataset_ids = [row["id"] for row in dataset_rows]
    response_ids = [row["id"] for row in response_rows]

    duplicated_ids = {
        response_id
        for response_id in response_ids
        if response_ids.count(response_id) > 1
    }
    missing_ids = set(dataset_ids) - set(response_ids)
    unexpected_ids = set(response_ids) - set(dataset_ids)

    if duplicated_ids or missing_ids or unexpected_ids:
        raise ValueError(
            "응답 ID가 평가셋과 일치하지 않습니다: "
            f"중복={sorted(duplicated_ids)}, "
            f"누락={sorted(missing_ids)}, "
            f"추가={sorted(unexpected_ids)}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="응답 기록으로 비관련 질문 대응 지표를 계산합니다."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--responses", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    dataset_rows = _read_jsonl(args.dataset)
    response_rows = _read_jsonl(args.responses)
    _validate_response_ids(dataset_rows, response_rows)
    metrics = calculate_response_metrics(dataset_rows, response_rows)
    Path(args.output).write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
