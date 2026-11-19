"""BM25 토큰 사전 계산표를 만든다(사전 등록 99번). 키·네트워크 불필요, 서비스 청크 기준 약 3분.

  python -X utf8 scripts/build_bm25_tokens.py
  python -X utf8 scripts/build_bm25_tokens.py --chunks data/service/chunks.jsonl --out data/service/bm25_tokens.jsonl.gz

결과 파일은 청크 파일 옆(`data/service/`, gitignore)에 두고 `RETRIEVAL_BM25_TOKENS_PATH`로 서버에 알린다.
청크 파일이나 kiwipiepy 버전이 바뀌면 다시 만들어야 한다(안 그러면 서버가 경고하고 느린 경로로 동작).
**운영과 같은 플랫폼(이미지)에서 만든다.** Windows의 kiwipiepy는 여러 낱말 사전을 적용하지 않아 Linux와
토큰이 다르다. Windows에서 만든 표는 Linux 서버가 기준 구절 비교로 거부한다(경고 후 느린 경로).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.rag.bm25 import create_tokenizer  # noqa: E402
from backend.rag.bm25_tokens import build_token_table, write_token_table  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chunks", type=Path, default=Path("data/service/chunks.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("data/service/bm25_tokens.jsonl.gz"))
    # 서비스 조합 d3-55는 kiwi. 다른 토크나이저는 시험·비교용.
    parser.add_argument("--tokenizer", default="kiwi", choices=("kiwi", "bigram", "space"))
    args = parser.parse_args(argv)

    with args.chunks.open(encoding="utf-8") as stream:
        chunks = [json.loads(line) for line in stream if line.strip()]
    chunks_sha256 = _sha256(args.chunks)
    tokenizer = create_tokenizer(args.tokenizer)
    started = time.perf_counter()
    table = build_token_table(chunks, tokenizer)
    elapsed = time.perf_counter() - started
    write_token_table(args.out, table, tokenizer_name=args.tokenizer, tokenizer=tokenizer, chunks_sha256=chunks_sha256)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "chunks": len(chunks),
                "entries": len(table),
                "chunks_sha256": chunks_sha256,
                "tokenize_s": round(elapsed, 1),
                "bytes": args.out.stat().st_size,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
