"""동결 평가셋과 원시 검색 결과에서 Hit@k·Recall@k·MRR@k·nDCG@k를 계산한다.

- Hit@k: 상위 k개 안에 정답 청크가 하나라도 있는 관련 문항의 비율
- Recall@k: 문항마다 정답 청크 중 상위 k개에 들어온 비율을 구해 평균
- MRR@k: 첫 정답 순위의 역수 평균(상위 k개 밖이면 0)
- nDCG@k: 정답 위치에 1/log2(순위+1)을 주고, 정답이 모두 맨 앞에 있을 때 점수로 나눈 값의 평균

이 모듈의 예전 결과 파일(evals/results)에 있는 ``recall_at_k``는 Hit@k로 계산된 값이다.

v2부터 문항에 ``gold_spans``(원문 span 정본)가 있으면 정답 청크를 ``--chunks`` 청크 파일에서
다시 계산한다. span 본문을 통째로 포함한 청크가 그 span의 정답이고, Recall@k는 ``item`` 단위로
센다(같은 item의 정답 청크가 하나라도 상위 k개에 있으면 찾은 것). Hit·MRR·nDCG는 모든 item의
정답 청크를 합친 집합으로 계산한다. ``gold_spans``가 없는 문항(v1)은 예전 방식 그대로다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class RetrievalEvaluationError(ValueError):
    """평가 입력이 불완전하거나 계약과 다를 때 발생한다."""


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RetrievalEvaluationError(
                f"JSONL {line_number}행 형식 오류: {path}"
            ) from exc
        if not isinstance(value, dict):
            raise RetrievalEvaluationError(f"JSONL {line_number}행은 객체여야 합니다")
        rows.append(value)
    return rows


def _rows_by_id(rows: Iterable[dict[str, Any]], label: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            row_id = int(row["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RetrievalEvaluationError(f"{label}의 id가 올바르지 않습니다") from exc
        if row_id in output:
            raise RetrievalEvaluationError(f"{label}에 중복 id={row_id}가 있습니다")
        output[row_id] = row
    return output


def _span_rcept_nos(row: dict[str, Any]) -> set[str]:
    return {
        location["rcept_no"]
        for span in row.get("gold_spans") or []
        for location in span.get("locations") or []
    }


def load_chunk_contents(
    chunks_path: str | Path, rcept_nos: set[str]
) -> dict[str, list[tuple[str, str]]]:
    """필요한 문서(rcept_no)의 청크만 (chunk_id, content)로 읽는다. 청크 파일이 커서 줄 단위로 거른다."""

    by_rcept: dict[str, list[tuple[str, str]]] = {rcept: [] for rcept in rcept_nos}
    for row in _read_jsonl(chunks_path):
        if row.get("rcept_no") in by_rcept:
            by_rcept[row["rcept_no"]].append((row["chunk_id"], row["content"]))
    return by_rcept


def span_item_chunks(
    row: dict[str, Any], chunks_by_rcept: dict[str, list[tuple[str, str]]]
) -> dict[str, set[str]]:
    """문항의 item마다, 그 item span 중 하나라도 통째로 포함한 청크 id 집합.

    span이 나온 문서(locations의 rcept_no) 안에서만 찾는다. 다른 회사 청크에 같은 문자열이
    우연히 있어도 정답으로 세지 않기 위해서다. 어떤 청크에도 없는 item은 빈 집합이다(청크 경계 잘림).
    """

    from backend.rag.dart_parser import normalize_text

    items: dict[str, set[str]] = {}
    for span in row["gold_spans"]:
        text = normalize_text(span["text"])
        holders = items.setdefault(span["item"], set())
        for rcept in {location["rcept_no"] for location in span["locations"]}:
            if rcept not in chunks_by_rcept:
                raise RetrievalEvaluationError(f"id={row['id']} 청크 파일에 문서 {rcept}가 없습니다")
            holders.update(chunk_id for chunk_id, content in chunks_by_rcept[rcept] if text in content)
    return items


def compute_retrieval_metrics(
    dataset_rows: Iterable[dict[str, Any]],
    result_rows: Iterable[dict[str, Any]],
    *,
    k: int = 10,
    chunks_by_rcept: dict[str, list[tuple[str, str]]] | None = None,
) -> dict[str, Any]:
    if not 1 <= k <= 100:
        raise RetrievalEvaluationError("k는 1~100이어야 합니다")

    dataset = _rows_by_id(dataset_rows, "dataset")
    results = _rows_by_id(result_rows, "results")
    unknown = sorted(results.keys() - dataset.keys())
    missing = sorted(dataset.keys() - results.keys())
    if unknown:
        raise RetrievalEvaluationError(f"알 수 없는 결과 id: {unknown}")
    if missing:
        raise RetrievalEvaluationError(f"결과가 없는 평가 id: {missing}")

    relevant = 0
    irrelevant = 0
    hits = 0
    recall_sum = 0.0
    ndcg_sum = 0.0
    reciprocal_rank_sum = 0.0
    per_question: list[dict[str, Any]] = []

    for row_id in sorted(dataset):
        item = dataset[row_id]
        kind = item.get("kind")
        gold = item.get("gold_chunks")
        retrieved = results[row_id].get("retrieved_chunks")
        if not isinstance(gold, list) or not all(isinstance(x, str) for x in gold):
            raise RetrievalEvaluationError(f"id={row_id} gold_chunks 형식 오류")
        if not isinstance(retrieved, list) or not all(
            isinstance(x, str) for x in retrieved
        ):
            raise RetrievalEvaluationError(f"id={row_id} retrieved_chunks 형식 오류")
        if len(set(retrieved)) != len(retrieved):
            raise RetrievalEvaluationError(f"id={row_id} 검색 결과에 중복 청크가 있습니다")

        if kind == "relevant" and item.get("gold_spans"):
            # v2 span 정본 문항: 정답 청크를 청크 파일에서 다시 만들고 Recall은 item 단위로 센다
            if chunks_by_rcept is None:
                raise RetrievalEvaluationError(
                    f"id={row_id}에 gold_spans가 있어 청크 파일(--chunks)이 필요합니다"
                )
            items = span_item_chunks(item, chunks_by_rcept)
            gold_set = set().union(*items.values())
            if not gold_set:
                raise RetrievalEvaluationError(f"관련 id={row_id}의 span을 포함한 청크가 없습니다")
            relevant += 1
            top_k = retrieved[:k]
            found = [rank for rank, chunk_id in enumerate(top_k, start=1) if chunk_id in gold_set]
            first_rank = found[0] if found else None
            top_set = set(top_k)
            items_found = sum(1 for holders in items.values() if holders & top_set)
            recall = items_found / len(items)
            dcg = sum(1.0 / math.log2(rank + 1) for rank in found)
            ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(gold_set), k) + 1))
            ndcg = dcg / ideal
            hits += int(first_rank is not None)
            recall_sum += recall
            ndcg_sum += ndcg
            reciprocal_rank_sum += 0.0 if first_rank is None else 1.0 / first_rank
            per_question.append(
                {
                    "id": row_id,
                    "kind": kind,
                    "hit": first_rank is not None,
                    "first_gold_rank": first_rank,
                    "gold_count": len(gold_set),
                    "gold_items": len(items),
                    "items_found": items_found,
                    "items_unreachable": sorted(name for name, holders in items.items() if not holders),
                    "recall": recall,
                    "ndcg": ndcg,
                }
            )
        elif kind == "relevant":
            if not gold:
                raise RetrievalEvaluationError(f"관련 id={row_id}의 gold가 비었습니다")
            relevant += 1
            gold_set = set(gold)
            first_rank = next(
                (
                    rank
                    for rank, chunk_id in enumerate(retrieved[:k], start=1)
                    if chunk_id in gold_set
                ),
                None,
            )
            hit = first_rank is not None
            top_k = retrieved[:k]
            found = [rank for rank, chunk_id in enumerate(top_k, start=1) if chunk_id in gold_set]
            recall = len(found) / len(gold_set)
            dcg = sum(1.0 / math.log2(rank + 1) for rank in found)
            ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(gold_set), k) + 1))
            ndcg = dcg / ideal
            hits += int(hit)
            recall_sum += recall
            ndcg_sum += ndcg
            reciprocal_rank_sum += 0.0 if first_rank is None else 1.0 / first_rank
            per_question.append(
                {
                    "id": row_id,
                    "kind": kind,
                    "hit": hit,
                    "first_gold_rank": first_rank,
                    "gold_count": len(gold_set),
                    "recall": recall,
                    "ndcg": ndcg,
                }
            )
        elif kind == "irrelevant":
            if gold:
                raise RetrievalEvaluationError(f"비관련 id={row_id}의 gold는 비어야 합니다")
            irrelevant += 1
            per_question.append(
                {
                    "id": row_id,
                    "kind": kind,
                    "hit": None,
                    "first_gold_rank": None,
                    "gold_count": 0,
                    "recall": None,
                    "ndcg": None,
                }
            )
        else:
            raise RetrievalEvaluationError(f"id={row_id} kind 형식 오류")

    if relevant == 0:
        raise RetrievalEvaluationError("관련 문항이 없어 지표를 계산할 수 없습니다")
    return {
        "k": k,
        "questions_total": len(dataset),
        "relevant_questions": relevant,
        "irrelevant_questions": irrelevant,
        f"hit_at_{k}": hits / relevant,
        f"recall_at_{k}": recall_sum / relevant,
        f"mrr_at_{k}": reciprocal_rank_sum / relevant,
        f"ndcg_at_{k}": ndcg_sum / relevant,
        "per_question": per_question,
    }


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evaluate_files(
    dataset_path: str | Path,
    results_path: str | Path,
    output_path: str | Path,
    *,
    k: int = 10,
    chunks_path: str | Path | None = None,
) -> dict[str, Any]:
    dataset_rows = _read_jsonl(dataset_path)
    chunks_by_rcept = None
    if chunks_path is not None:
        needed = set().union(*(_span_rcept_nos(row) for row in dataset_rows))
        chunks_by_rcept = load_chunk_contents(chunks_path, needed)
    metrics = compute_retrieval_metrics(
        dataset_rows,
        _read_jsonl(results_path),
        k=k,
        chunks_by_rcept=chunks_by_rcept,
    )
    payload = {
        "dataset_sha256": _sha256(dataset_path),
        "results_sha256": _sha256(results_path),
        **metrics,
    }
    if chunks_path is not None:  # v1처럼 청크 파일 없이 재면 출력이 예전과 바이트 단위로 같다
        payload["chunks_sha256"] = _sha256(chunks_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="검색 Recall@k·MRR 평가")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--chunks", help="gold_spans가 있는 평가셋(v2)에서 정답 청크를 만들 청크 파일")
    args = parser.parse_args(argv)
    payload = evaluate_files(
        args.dataset, args.results, args.output, k=args.k, chunks_path=args.chunks
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
