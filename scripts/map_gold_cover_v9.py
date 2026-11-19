"""파서 v9(주석 제목 수정)용: v8 정답 매핑(엄격·확장)을 v9 청크로 옮긴다.

v8 매핑에는 수동 매핑·확장 정답 추가가 이미 반영돼 있으므로 v7이 아니라 v8 매핑에서 출발한다.
정답 id가 v9 청크에 그대로 있으면 유지하고, 없으면 map_gold_cover_v8.map_one과 같은 규칙
(옛 본문 50자 조각을 80% 덮을 때까지 같은 문서의 새 청크를 모음, 50% 미만이면 실패)으로 옮긴다.
엄격 정답을 하나도 옮기지 못한 문항이 생기면 파일을 쓰지 않고 실패로 멈춘다(정답을 지우지 않는다).
옮긴 기록은 evals/mappings/v9_remap.json에 남긴다.

v9는 group·fixed_window 두 청킹만 만들었다(fixed500은 만들지 않음).

실행: python -X utf8 scripts/map_gold_cover_v9.py           # 파일 생성
      python -X utf8 scripts/map_gold_cover_v9.py --check   # 커밋된 파일과 같은지만 검사
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from map_gold_cover_v8 import map_one, read_jsonl  # noqa: E402

CHUNKINGS = {
    "group": ("data/experiments/b100-v8-group/chunks.jsonl", "data/experiments/b100-v9-group/chunks.jsonl"),
    "fixed_window": (
        "data/experiments/b100-v8-fixed_window/chunks.jsonl",
        "data/experiments/b100-v9-fixed_window/chunks.jsonl",
    ),
}
MAPPING_DIR = Path("evals/mappings")


def remap_ids(gold_ids, old_chunks, new_ids, new_by_doc, cache, log):
    out: list[str] = []
    for gid in gold_ids:
        if gid in new_ids:
            out.append(gid)
            continue
        if gid not in cache:
            old = old_chunks[gid]
            cache[gid] = map_one(old, new_by_doc[old["rcept_no"]])
            log.append({"old_chunk_id": gid, "new_chunk_ids": cache[gid][0], "cover": round(cache[gid][1], 2)})
        out += cache[gid][0]
    return list(dict.fromkeys(out))


def build_outputs(chunkings: dict[str, tuple[list[dict], list[dict]]]) -> tuple[dict[str, str], dict]:
    files: dict[str, str] = {}
    record: dict = {
        "rule": "v8 정답 id가 v9에 있으면 유지, 없으면 v8 본문 50자 조각을 80% 덮을 때까지 v9 청크를 모음(50% 미만 실패)",
        "source": "evals/mappings/dataset_v1.v8_{chunking}*.jsonl",
        "chunkings": {},
    }
    for name, (old_rows, new_rows) in chunkings.items():
        old_chunks = {row["chunk_id"]: row for row in old_rows}
        new_ids = {row["chunk_id"] for row in new_rows}
        new_by_doc: dict[str, list[dict]] = defaultdict(list)
        for row in new_rows:
            new_by_doc[row["rcept_no"]].append(row)
        corpus_ids = {row["corpus_id"] for row in new_rows}
        if len(corpus_ids) != 1:
            raise SystemExit(f"{name}: corpus_id가 하나가 아닙니다 {sorted(corpus_ids)}")
        corpus = corpus_ids.pop()
        cache: dict = {}
        log: list[dict] = []
        failed: list[int] = []

        strict_lines = []
        for q in read_jsonl(MAPPING_DIR / f"dataset_v1.v8_{name}.jsonl"):
            if q["kind"] == "relevant":
                strict = remap_ids(q["gold_chunks"], old_chunks, new_ids, new_by_doc, cache, log)
                if not strict:
                    failed.append(q["id"])
                q = {**q, "gold_chunks": strict}
            strict_lines.append(json.dumps({**q, "corpus_version": corpus}, ensure_ascii=False))
        plus_lines = []
        for row in read_jsonl(MAPPING_DIR / f"dataset_v1.v8_{name}.gold_plus.jsonl"):
            strict = remap_ids(row["gold_strict"], old_chunks, new_ids, new_by_doc, cache, log)
            plus = remap_ids(row["gold_plus"], old_chunks, new_ids, new_by_doc, cache, log)
            plus_lines.append(json.dumps({"id": row["id"], "gold_strict": strict, "gold_plus": plus}, ensure_ascii=False))
        if failed:
            raise SystemExit(f"{name}: 엄격 정답을 옮기지 못한 문항 {failed}. 수동 확인이 필요합니다")
        files[f"dataset_v1.v9_{name}.jsonl"] = "\n".join(strict_lines) + "\n"
        files[f"dataset_v1.v9_{name}.gold_plus.jsonl"] = "\n".join(plus_lines) + "\n"
        record["chunkings"][name] = {"corpus_version": corpus, "remapped": log}
    files["v9_remap.json"] = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    return files, record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="파일을 쓰지 않고 커밋된 파일과 같은지만 검사")
    args = parser.parse_args(argv)

    chunkings = {name: (read_jsonl(old), read_jsonl(new)) for name, (old, new) in CHUNKINGS.items()}
    files, record = build_outputs(chunkings)
    mismatch = []
    for name, text in files.items():
        path = MAPPING_DIR / name
        if args.check:
            if not path.exists() or path.read_bytes() != text.encode("utf-8"):
                mismatch.append(name)
        else:
            path.write_bytes(text.encode("utf-8"))
    summary = {name: len(info["remapped"]) for name, info in record["chunkings"].items()}
    print(json.dumps({"remapped": summary, "check_mismatch": mismatch}, ensure_ascii=False))
    return 1 if mismatch else 0


if __name__ == "__main__":
    raise SystemExit(main())
