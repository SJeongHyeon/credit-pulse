"""사전 등록 8·14번용: v7 정답(엄격·확장)을 v8 청크로 옮긴다.

옛 정답 본문을 50자 조각으로 나누고, 같은 문서의 새 청크 중 조각을 가장 많이 덮는
청크부터 골라 80% 이상 덮을 때까지 모은다. 50% 미만이면 옮기지 못한 정답이다.
엄격 정답을 하나도 옮기지 못한 관련 문항은 어느 한 청킹에서라도 그렇다면 모든 청킹에서
제외해 비교 집합을 같게 둔다. 제외는 evals/mappings/v8_exclusions.json에 기록한다.
옮긴 뒤 발견한 확장 정답 누락은 evals/mappings/v8_gold_plus_additions.jsonl에 판정과 함께 적고,
판정이 O인 줄만 해당 청킹의 확장 정답에 더한다(엄격 정답은 바꾸지 않는다).
자동으로 옮기지 못한 엄격 정답은 evals/mappings/v8_strict_manual_map.jsonl에 근거 규칙과 함께
청크를 직접 적을 수 있다. 적힌 청킹·문항은 그 청크를 엄격·확장 정답으로 쓰고 제외하지 않는다.

실행: python -X utf8 scripts/map_gold_cover_v8.py           # 파일 생성
      python -X utf8 scripts/map_gold_cover_v8.py --check   # 커밋된 파일과 같은지만 검사
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.rag.chunking import chunk_body  # noqa: E402
from backend.rag.dart_parser import normalize_text  # noqa: E402

PIECE_CHARS = 50
TARGET_COVER = 0.8
MIN_COVER = 0.5
CHUNKINGS = {
    "group": "data/experiments/b100-v8-group/chunks.jsonl",
    "fixed_window": "data/experiments/b100-v8-fixed_window/chunks.jsonl",
    "fixed_window_500": "data/experiments/b100-v8-fixed500/chunks.jsonl",
}
ADDITIONS_PATH = "evals/mappings/v8_gold_plus_additions.jsonl"
MANUAL_MAP_PATH = "evals/mappings/v8_strict_manual_map.jsonl"


def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def pieces(text: str) -> list[str]:
    text = normalize_text(text)
    out = [text[i : i + PIECE_CHARS] for i in range(0, max(len(text) - PIECE_CHARS + 1, 1), PIECE_CHARS)]
    return out or [text]


def map_one(old: dict, new_chunks: list[dict]) -> tuple[list[str], float]:
    """옛 정답 하나를 새 청크 id 목록과 덮은 비율로 돌려준다(덮은 비율 MIN_COVER 미만이면 빈 목록)."""

    parts = pieces(chunk_body(old["content"], old.get("table_title")))
    candidates = []
    for row in new_chunks:
        content = normalize_text(row["content"])
        hit = {i for i, part in enumerate(parts) if part in content}
        if hit:
            candidates.append((row["chunk_id"], hit))
    covered: set[int] = set()
    chosen: list[str] = []
    while len(covered) / len(parts) < TARGET_COVER:
        best = max(candidates, key=lambda c: len(c[1] - covered), default=None)
        if best is None or not (best[1] - covered):
            break
        chosen.append(best[0])
        covered |= best[1]
    ratio = len(covered) / len(parts)
    return (chosen if ratio >= MIN_COVER else []), ratio


def build_outputs(
    dataset: list[dict],
    gold_plus: dict[int, dict],
    old_chunks: dict[str, dict],
    new_by_chunking: dict[str, list[dict]],
    additions: list[dict] | None = None,
    manual_map: list[dict] | None = None,
) -> tuple[dict[str, list[str]], dict]:
    """청킹별 (엄격 파일 줄, 확장 파일 줄)과 제외 기록을 만든다. 파일은 쓰지 않는다."""

    manual: dict[tuple[str, int], list[str]] = {}
    for item in manual_map or []:
        known = {row["chunk_id"] for row in new_by_chunking.get(item["chunking"], [])}
        missing = [cid for cid in item["chunk_ids"] if cid not in known]
        if missing or not item["chunk_ids"]:
            raise SystemExit(f"수동 매핑 id={item['id']}의 청크가 {item['chunking']}에 없습니다 {missing}")
        manual[(item["chunking"], item["id"])] = list(item["chunk_ids"])

    added: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for item in additions or []:
        if item["verdict"] != "O":
            continue
        known = {row["chunk_id"] for row in new_by_chunking.get(item["chunking"], [])}
        if item["chunk_id"] not in known:
            raise SystemExit(f"추가 확장 정답 {item['chunk_id']}가 {item['chunking']} 청크에 없습니다")
        added[item["chunking"]][item["id"]].append(item["chunk_id"])

    mapped: dict[str, dict[int, tuple[list[str], list[str]]]] = {}
    corpus: dict[str, str] = {}
    partial: dict[int, list[dict]] = defaultdict(list)
    for name, rows in new_by_chunking.items():
        by_doc: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_doc[row["rcept_no"]].append(row)
        ids = {row["corpus_id"] for row in rows}
        if len(ids) != 1:
            raise SystemExit(f"{name}: corpus_id가 하나가 아닙니다 {sorted(ids)}")
        corpus[name] = ids.pop()
        cache: dict[str, tuple[list[str], float]] = {}

        def convert(gold_ids: list[str], qid: int) -> list[str]:
            out: list[str] = []
            for gid in gold_ids:
                if gid not in cache:
                    cache[gid] = map_one(old_chunks[gid], by_doc[old_chunks[gid]["rcept_no"]])
                new_ids, ratio = cache[gid]
                if not new_ids:
                    partial[qid].append({"chunking": name, "old_chunk_id": gid, "cover": round(ratio, 2)})
                out += new_ids
            return list(dict.fromkeys(out))

        mapped[name] = {}
        for q in dataset:
            if q["kind"] != "relevant":
                continue
            if (name, q["id"]) in manual:
                strict = manual[(name, q["id"])]
                plus = list(dict.fromkeys(strict + convert(gold_plus[q["id"]]["gold_plus"], q["id"])))
                mapped[name][q["id"]] = (strict, list(dict.fromkeys(plus + added[name][q["id"]])))
                continue
            strict = convert(q["gold_chunks"], q["id"])
            plus = convert(gold_plus[q["id"]]["gold_plus"], q["id"])
            plus = list(dict.fromkeys(plus + added[name][q["id"]]))
            mapped[name][q["id"]] = (strict, plus)

    excluded = sorted({qid for name in mapped for qid, (strict, _) in mapped[name].items() if not strict})
    outputs: dict[str, list[str]] = {}
    for name in new_by_chunking:
        strict_lines, plus_lines = [], []
        for q in dataset:
            if q["kind"] == "relevant":
                if q["id"] in excluded:
                    continue
                strict, plus = mapped[name][q["id"]]
                strict_lines.append(json.dumps({**q, "gold_chunks": strict, "corpus_version": corpus[name]}, ensure_ascii=False))
                plus_lines.append(json.dumps({"id": q["id"], "gold_strict": strict, "gold_plus": plus}, ensure_ascii=False))
            else:
                strict_lines.append(json.dumps({**q, "corpus_version": corpus[name]}, ensure_ascii=False))
        outputs[f"dataset_v1.v8_{name}.jsonl"] = strict_lines
        outputs[f"dataset_v1.v8_{name}.gold_plus.jsonl"] = plus_lines

    seen = set()
    partial_rows = []
    for qid in sorted(partial):
        if qid in excluded:
            continue
        for item in partial[qid]:
            key = (qid, item["old_chunk_id"])
            if key not in seen:
                seen.add(key)
                partial_rows.append({"id": qid, **item})
    record = {
        "rule": f"옛 정답 본문 {PIECE_CHARS}자 조각을 {int(TARGET_COVER*100)}% 덮을 때까지 새 청크를 모으고 "
        f"{int(MIN_COVER*100)}% 미만이면 옮기지 못함. 엄격 정답을 하나도 못 옮긴 문항은 모든 청킹에서 제외",
        "applies_to": sorted(outputs),
        "excluded": [
            {"id": qid, "reason": "엄격 정답을 v8 청크로 옮기지 못함(v7 본문이 #52 파서 버그로 뭉개짐)",
             "cover": sorted({item["cover"] for item in partial[qid]})}
            for qid in excluded
        ],
        "partially_mapped": partial_rows,
        "manual_mapped": sorted({qid for _, qid in manual}),
    }
    return outputs, record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="파일을 쓰지 않고 커밋된 파일과 같은지만 검사")
    parser.add_argument("--out-dir", default="evals/mappings")
    args = parser.parse_args(argv)

    dataset = read_jsonl("evals/dataset_v1.jsonl")
    gold_plus = {row["id"]: row for row in read_jsonl("evals/mappings/dataset_v1.gold_plus.jsonl")}
    old_chunks = {row["chunk_id"]: row for row in read_jsonl("data/chunks.100-b-v7.jsonl")}
    new_by_chunking = {name: read_jsonl(path) for name, path in CHUNKINGS.items()}
    additions = read_jsonl(ADDITIONS_PATH) if Path(ADDITIONS_PATH).exists() else []
    manual_map = read_jsonl(MANUAL_MAP_PATH) if Path(MANUAL_MAP_PATH).exists() else []
    outputs, record = build_outputs(dataset, gold_plus, old_chunks, new_by_chunking, additions, manual_map)

    files = {name: "\n".join(lines) + "\n" for name, lines in outputs.items()}
    files["v8_exclusions.json"] = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    out_dir = Path(args.out_dir)
    mismatch = []
    for name, text in files.items():
        path = out_dir / name
        if args.check:
            if not path.exists() or path.read_bytes() != text.encode("utf-8"):
                mismatch.append(name)
        else:
            path.write_bytes(text.encode("utf-8"))
    print(json.dumps({"excluded": [e["id"] for e in record["excluded"]], "check_mismatch": mismatch}, ensure_ascii=False))
    return 1 if mismatch else 0


if __name__ == "__main__":
    raise SystemExit(main())
