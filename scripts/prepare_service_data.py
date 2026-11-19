"""서비스(Cloud Run) 이미지에 넣을 100사 검색 산출물을 `data/service/`에 모은다. GitHub에는 올리지 않는다.

키 소유자(배포 담당)가 저장소 루트에서 실행한다. 입력은 로컬에만 있는 v9 청크와 100사 벡터다.
  python -X utf8 scripts/prepare_service_data.py
결과(약 270MB, .gitignore에 있음, 배포 업로드에는 .gcloudignore로 포함):
  data/service/chunks.jsonl
  data/service/embeddings.ids.jsonl · embeddings.vectors.npy · embeddings.metadata.json
Cloud Run 환경변수: RETRIEVAL_COMBO=d3-55, RETRIEVAL_CHUNKS_PATH=data/service/chunks.jsonl,
RETRIEVAL_EMBEDDING_PREFIX=data/service/embeddings
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE_CHUNKS = ROOT / "data" / "experiments" / "b100-v9-fixed_window" / "chunks.jsonl"
SOURCE_PREFIX = ROOT / "data" / "embeddings" / "v9" / "oai-small-fixed1000-o100-all"
OUTPUT = ROOT / "data" / "service"


def main() -> int:
    from backend.retrieval_combo import RetrievalSettings, build_retriever, get_combo

    missing = [str(path) for path in (SOURCE_CHUNKS, Path(f"{SOURCE_PREFIX}.vectors.npy")) if not path.exists()]
    if missing:
        print("입력이 없습니다(키 소유자 로컬 산출물): " + ", ".join(missing), file=sys.stderr)
        print("100사 벡터는 scripts/merge_embeddings.py로 만든다(data/README.md)", file=sys.stderr)
        return 2
    OUTPUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE_CHUNKS, OUTPUT / "chunks.jsonl")
    for suffix in (".ids.jsonl", ".vectors.npy", ".metadata.json"):
        shutil.copyfile(f"{SOURCE_PREFIX}{suffix}", OUTPUT / f"embeddings{suffix}")

    class _NoQueries:  # 조립 검증만 한다(질의 임베딩·리랭크 호출 없음)
        dimension = 1536

        def encode_queries(self, texts):
            raise RuntimeError("검증 중에는 질의하지 않습니다")

    class _NoRerank:
        model_name = "check"

        def score(self, query, texts):
            raise RuntimeError("검증 중에는 질의하지 않습니다")

    retriever = build_retriever(
        RetrievalSettings(combo=get_combo("d3-55"), chunks_path=OUTPUT / "chunks.jsonl", embedding_prefix=OUTPUT / "embeddings"),
        embedder=_NoQueries(), reranker=_NoRerank(),
    )
    size = sum(path.stat().st_size for path in OUTPUT.iterdir()) / 1e6
    print(f"data/service 준비 완료: 검색 대상 {len(retriever.indexed_corp_codes)}사, {size:.0f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
