"""같은 청크 파일·같은 모델로 회사별로 나눠 만든 임베딩 산출물을 하나로 합친다(서비스용 100사 벡터).

평가에 쓴 16사 벡터(`oai-small-fixed1000-o100`)는 그대로 두고, 나머지 84사를 따로 임베딩한 뒤 합친다.
16사를 다시 임베딩하지 않는 이유: API 벡터가 소수점 아래에서 조금만 달라도 순위가 바뀌어 측정 결과와 어긋날 수 있다.

  python -X utf8 scripts/merge_embeddings.py --output-prefix data/embeddings/v9/oai-small-fixed1000-o100-all `
    data/embeddings/v9/oai-small-fixed1000-o100 data/embeddings/v9/oai-small-fixed1000-o100-rest84

같은 본문(content_sha256)이 여러 입력에 있으면 처음 입력의 벡터를 쓴다. 결과는 GitHub에 올리지 않는다(.gitignore의 data/embeddings/).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def merge(prefixes: list[str], output_prefix: str) -> dict:
    metas = [json.loads(Path(f"{prefix}.metadata.json").read_text(encoding="utf-8")) for prefix in prefixes]
    for field in ("model_name", "dimension", "chunks_sha256", "input_header", "dtype"):
        values = {json.dumps(meta.get(field)) for meta in metas}
        if len(values) != 1:
            raise SystemExit(f"입력 산출물의 {field}가 서로 다릅니다: {sorted(values)}")
    if not all(meta.get("complete") for meta in metas):
        raise SystemExit("완료되지 않은 산출물이 있습니다")

    ids: list[dict] = []
    blocks: list[np.ndarray] = []
    seen: set[str] = set()
    for prefix in prefixes:
        vectors = np.load(f"{prefix}.vectors.npy", mmap_mode="r")
        rows = [json.loads(line) for line in Path(f"{prefix}.ids.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        keep = [row for row in rows if row["content_sha256"] not in seen]
        seen.update(row["content_sha256"] for row in keep)
        blocks.append(np.asarray(vectors[[int(row["index"]) for row in keep]], dtype=np.float32))
        for row in keep:
            ids.append({**{k: v for k, v in row.items() if k != "index"}, "index": len(ids)})

    out = Path(output_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    ids_path, vectors_path = Path(f"{out}.ids.jsonl"), Path(f"{out}.vectors.npy")
    ids_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ids), encoding="utf-8", newline="\n")
    np.save(vectors_path, np.concatenate(blocks), allow_pickle=False)
    metadata = {
        "complete": True,
        "model_name": metas[0]["model_name"],
        "dimension": int(metas[0]["dimension"]),
        "dtype": "float32",
        "chunks_sha256": metas[0]["chunks_sha256"],
        "input_header": metas[0].get("input_header"),
        "rows": len(ids),
        "corp_codes": sorted({code for meta in metas for code in meta.get("corp_codes") or []}),
        "merged_from": [{"prefix": prefix, "rows": int(meta["rows"]), "vectors_sha256": meta["vectors_sha256"]} for prefix, meta in zip(prefixes, metas)],
        "ids_sha256": _sha256(ids_path),
        "vectors_sha256": _sha256(vectors_path),
    }
    Path(f"{out}.metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("prefixes", nargs="+")
    args = parser.parse_args(argv)
    metadata = merge(args.prefixes, args.output_prefix)
    print(json.dumps({k: metadata[k] for k in ("rows", "dimension", "model_name")}, ensure_ascii=False), len(metadata["corp_codes"]), "사")
    return 0


if __name__ == "__main__":
    sys.exit(main())
