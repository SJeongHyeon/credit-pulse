"""표·수치가 든 청크를 DART 원문(document.zip)과 자동으로 대조한다(리뷰 코멘트 11/13 ②).

무엇을 보나(모두 원문 XML을 지금 파서로 다시 읽어 견준다. LLM 호출 없음)
  (행 순서를 자동으로 맞춰 보는 검사는 넣지 않았다: 창 겹침과 표 제목 삽입 때문에 믿을 만한 값이 나오지 않았다.)
  A. 숫자 보존: 청크의 숫자 토큰이 모두 같은 접수번호의 원문에 있는가. 청크의 맨 앞·맨 뒤에서 1,000자 창이
     숫자를 자른 경우(예: 원문 3,148,527 → 청크 첫머리 148,527)는 '가장자리 잘림'으로 따로 센다.
  C. 정답 행: 평가셋 v2의 정답 구간(gold_spans)이 원문의 한 행(또는 한 문단) 안에 통째로 있는가.
     청크는 행 경계를 공백으로만 잇지만, 정답의 '항목 이름 | 금액' 짝이 원문에서 같은 행이면 그 짝은 맞다.

대상: (1) --results의 답이 실제로 인용한 청크 전부 (2) 표 청크 무작위 표본(--sample, 고정 seed).

  python -X utf8 scripts/verify_table_chunks_source.py --chunks data/experiments/b100-v9-fixed_window/chunks.jsonl \
    --corpus data/corpus --results evals/results/graph50/w146_now_r1_multi_d3-55_v9_v2.jsonl \
    --output docs/evidence/d10/table_chunk_source_check.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.rag.dart_parser import normalize_text, parse_document  # noqa: E402

NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{4,}")
SEED = 20261113


def numbers(text: str) -> set[str]:
    return set(NUMBER_RE.findall(text))


EDGE = 14  # 청크 맨 앞·맨 뒤 이 글자 수 안에서 시작하거나 끝나는 숫자는 창이 잘랐을 수 있다


def check_chunk(content: str, source_numbers: set[str]) -> dict:
    """A(숫자)를 본다."""

    missing, edge_cut = [], []
    for match in NUMBER_RE.finditer(content):
        token = match.group()
        if token in source_numbers:
            continue
        at_edge = match.start() < EDGE or match.end() > len(content) - EDGE
        if at_edge and any(token in number for number in source_numbers):
            edge_cut.append(token)
        else:
            missing.append(token)
    return {"numbers": len(NUMBER_RE.findall(content)), "numbers_missing": missing, "numbers_edge_cut": edge_cut}


def check_span(span: dict, blocks_by_rcept: dict) -> dict:
    """C: 정답 구간의 글자가 그 위치(ord)의 원문 블록 하나 안에 통째로 있는가."""

    text = normalize_text(span["text"])
    for location in span.get("locations") or []:
        blocks = blocks_by_rcept.get(location["rcept_no"])
        if blocks is None:
            continue
        block = next((b for b in blocks if b.ordinal == location.get("ord")), None)
        if block is not None and text in block.text:
            return {"ok": True, "is_table": block.is_table}
    return {"ok": False, "is_table": None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True, help="접수번호별 document.zip이 든 폴더")
    parser.add_argument("--results", type=Path, required=True, help="인용 청크를 읽을 평가 결과 JSONL")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/dataset_v2.jsonl")
    parser.add_argument("--sample", type=int, default=300, help="무작위로 볼 표 청크 수")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    cited = {c["chunk_id"] for line in args.results.read_text(encoding="utf-8").splitlines() if line.strip()
             for item in json.loads(line).get("draft_items") or [] for c in item.get("citations") or []}
    chunks = [json.loads(line) for line in args.chunks.open(encoding="utf-8") if line.strip()]
    table_chunks = [c for c in chunks if c.get("is_table") or " | " in c["content"]]
    sample = random.Random(SEED).sample(sorted(table_chunks, key=lambda c: c["chunk_id"]), args.sample)
    targets = {c["chunk_id"]: ("cited", c) for c in chunks if c["chunk_id"] in cited}
    for chunk in sample:
        targets.setdefault(chunk["chunk_id"], ("sample", chunk))

    dataset = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    spans = [(row["id"], span) for row in dataset for span in row.get("gold_spans") or []]
    rcepts = {chunk["rcept_no"] for _, chunk in targets.values()} | {loc["rcept_no"] for _, s in spans for loc in s["locations"]}
    blocks_by_rcept, numbers_by_rcept = {}, {}
    for rcept in sorted(rcepts):
        path = args.corpus / rcept / "document.zip"
        if not path.exists():
            continue
        blocks = parse_document(path)
        blocks_by_rcept[rcept] = blocks
        numbers_by_rcept[rcept] = set().union(*(numbers(b.text) for b in blocks)) if blocks else set()

    records = []
    for chunk_id, (group, chunk) in sorted(targets.items()):
        blocks = blocks_by_rcept.get(chunk["rcept_no"])
        if blocks is None:
            records.append({"chunk_id": chunk_id, "group": group, "source_missing": True})
            continue
        records.append({"chunk_id": chunk_id, "group": group, "rcept_no": chunk["rcept_no"], "corp_name": chunk["corp_name"],
                        **check_chunk(chunk["content"], numbers_by_rcept[chunk["rcept_no"]])})
    span_records = [{"question_id": qid, "item": span["item"], **check_span(span, blocks_by_rcept)} for qid, span in spans]

    def group_summary(group: str) -> dict:
        rows = [r for r in records if r["group"] == group and not r.get("source_missing")]
        return {
            "chunks": len(rows),
            "numbers_checked": sum(r["numbers"] for r in rows),
            "numbers_missing_from_source": sum(len(r["numbers_missing"]) for r in rows),
            "numbers_cut_at_chunk_edge": sum(len(r["numbers_edge_cut"]) for r in rows),
            "chunks_with_missing_numbers": sum(bool(r["numbers_missing"]) for r in rows),
        }

    summary = {
        "what": "표·수치 청크의 원문 자동 대조(숫자 보존, 정답 구간이 원문 한 행 안에 있는지)",
        "chunks_file": args.chunks.name, "results": args.results.name, "sample_seed": SEED, "sample_size": args.sample,
        "documents_parsed": len(blocks_by_rcept),
        "cited": group_summary("cited"), "sample": group_summary("sample"),
        "gold_spans": {"total": len(span_records), "in_one_source_block": sum(r["ok"] for r in span_records),
                       "of_which_table_rows": sum(bool(r["is_table"]) for r in span_records if r["ok"]),
                       "not_found": [f'{r["question_id"]}:{r["item"]}' for r in span_records if not r["ok"]]},
        "problem_chunks": [r for r in records if r.get("numbers_missing")],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: summary[k] for k in ("documents_parsed", "cited", "sample", "gold_spans")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
