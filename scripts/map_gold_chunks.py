"""동결 평가셋의 정답을 새 청크로 옮겨 매핑 파일과 파생 평가 파일을 만든다.

실행 예: python scripts/map_gold_chunks.py --dataset evals/dataset_v1.jsonl
  --old-chunks data/chunks.100-b-v7.jsonl --new-chunks data/chunks.100-b-v7-merge.jsonl
  --output-map evals/mappings/short_merge_v1.gold_map.json
  --output-dataset evals/mappings/dataset_v1.short_merge.jsonl
"""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

# `python scripts/map_gold_chunks.py`로 실행해도 backend를 찾도록 저장소 루트를 경로에 넣는다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.rag.gold_mapping import GoldMappingError, derive_dataset, map_gold_chunks  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="동결 정답을 새 청크로 매핑")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--old-chunks", required=True, type=Path)
    parser.add_argument("--new-chunks", required=True, type=Path)
    parser.add_argument("--output-map", required=True, type=Path)
    parser.add_argument("--output-dataset", required=True, type=Path)
    args = parser.parse_args(argv)

    dataset = read_jsonl(args.dataset)
    new_chunks = read_jsonl(args.new_chunks)
    corpus_ids = {str(row["corpus_id"]) for row in new_chunks}
    if len(corpus_ids) != 1:
        print(f"새 청크의 corpus_id가 하나가 아닙니다: {sorted(corpus_ids)}", file=sys.stderr)
        return 1
    try:
        mapping = map_gold_chunks(dataset, read_jsonl(args.old_chunks), new_chunks)
    except GoldMappingError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    corpus_id = next(iter(corpus_ids))
    derived = derive_dataset(dataset, mapping, corpus_id=corpus_id)
    args.output_dataset.parent.mkdir(parents=True, exist_ok=True)
    with args.output_dataset.open("w", encoding="utf-8", newline="\n") as stream:
        for row in derived:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    payload = {
        "frozen_dataset_sha256": sha256(args.dataset),
        "old_chunks_sha256": sha256(args.old_chunks),
        "new_chunks_sha256": sha256(args.new_chunks),
        "derived_dataset_sha256": sha256(args.output_dataset),
        "new_corpus_id": corpus_id,
        "counts": dict(Counter(item["match"] for item in mapping.values())),
        "mapping": mapping,
    }
    args.output_map.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({key: payload[key] for key in ("new_corpus_id", "counts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
