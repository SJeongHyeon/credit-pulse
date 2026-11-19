"""사전 등록 99번의 '회사별 첫 검색 시간'을 다시 재고 원시 측정값을 파일로 남긴다(키·유료 호출 없음).

왜 필요한가: 99번은 "첫 검색 시간의 거의 전부가 청크 형태소 분석이고, 토큰을 미리 계산하면 사라진다"를 근거로
채택했는데, 그때의 원시 측정 파일을 저장소에 남기지 않아 평가자가 다시 확인할 수 없었다(11/11·11/12 리뷰 코멘트).

재는 것: 회사 하나(회사+FY2025 필터)의 BM25 첫 검색과 두 번째 검색 시간.
  - 표 없음: 첫 검색 때 그 회사 청크를 Kiwi로 토큰화한다(99번 이전 경로).
  - 표 있음: 미리 계산한 토큰표에서 꺼낸다(99번).
'첫 검색'이 되도록 측정마다 새 파이썬 프로세스를 띄운다. 표 없음·있음을 번갈아 잰다(같은 시간대 비교).

두 가지 자료로 잴 수 있다.
  --source snapshot  저장소에 들어 있는 데모 스냅샷(data/snapshot/chunks.jsonl.gz, 16개사 4,942청크).
                     **새 clone에서 그대로 돈다.** 토큰표는 실행하는 컴퓨터에서 그 자리에서 만든다(약 30초).
  --source service   운영 청크(data/service/chunks.jsonl, 100개사 31,967청크)와 미리 만든 토큰표.
                     두 파일은 용량 때문에 저장소에 없어, 파일을 가진 팀 컴퓨터에서만 돈다.
  --source auto      (기본) 운영 청크가 있으면 service, 없으면 snapshot.

사용: python -X utf8 scripts/measure_bm25_first_search.py [--source snapshot] [--repeat 3]
필요한 것: pip install -r backend/requirements.txt (kiwipiepy 포함). API 키·DB·네트워크는 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SNAPSHOT_CHUNKS = ROOT / "data/snapshot/chunks.jsonl.gz"
SERVICE_CHUNKS = ROOT / "data/service/chunks.jsonl"
# 토큰표는 만든 환경(OS·Kiwi 버전)에서만 쓸 수 있다. 서비스 이미지용(linux)과 로컬 평가용(win)이 따로 있다.
SERVICE_TOKENS = (ROOT / "data/experiments/b100-v9-fixed_window/bm25_tokens.win.jsonl.gz" if sys.platform == "win32"
                  else ROOT / "data/service/bm25_tokens.jsonl.gz")
# 99번 0단계와 같은 3개사: 운영 청크에서 청크가 가장 많은 회사, 중간, 적은 회사
SERVICE_COMPANIES = ["00181712", "00153393", "00120571"]
QUESTION = "2025년 말 연결 기준 차입금과 사채 잔액은 얼마인가요?"
FISCAL_YEAR = 2025


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_chunks(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def child(corp_code: str, mode: str, chunks_path: Path, tokens_path: Path) -> dict:
    """새 프로세스 안에서 한 번 잰다. 청크 읽기·Kiwi 생성·표 읽기는 '검색 시간'에서 뺀다(서버 기동 때 하는 일)."""

    from backend.rag.bm25 import create_tokenizer
    from backend.rag.bm25_tokens import load_token_table
    from backend.rag.offline_eval import InMemoryBM25Backend
    from backend.rag.retrieve import SearchFilters

    chunks = _read_chunks(chunks_path)
    tokenizer = create_tokenizer("kiwi")
    tokenizer("기동 때 Kiwi를 한 번 깨운다")  # 모델 읽기 시간을 첫 검색에서 뺀다
    table, table_seconds = None, 0.0
    if mode == "with":
        started = time.perf_counter()
        table = load_token_table(tokens_path, tokenizer_name="kiwi", tokenizer=tokenizer, chunks_sha256=_sha256(chunks_path))
        table_seconds = time.perf_counter() - started
        if table is None:
            raise SystemExit("토큰표가 지금 환경과 맞지 않습니다(scripts/build_bm25_tokens.py로 다시 만드세요)")
    backend = InMemoryBM25Backend(chunks=chunks, tokenizer=tokenizer, retrieval_method="bm25:kiwi", prebuilt_tokens=table)
    filters = SearchFilters(corp_code=corp_code, fiscal_year=FISCAL_YEAR)
    started = time.perf_counter()
    first = backend.search(QUESTION, filters=filters, top_k=50)
    first_seconds = time.perf_counter() - started
    started = time.perf_counter()
    second = backend.search(QUESTION, filters=filters, top_k=50)
    second_seconds = time.perf_counter() - started
    candidates, _ = backend._index_for(filters)
    return {
        "corp_code": corp_code, "mode": mode, "company_chunks": len(candidates),
        "first_search_seconds": round(first_seconds, 4), "second_search_seconds": round(second_seconds, 5),
        "token_table_load_seconds": round(table_seconds, 3),
        "top5_chunk_ids": [item.chunk_id for item in first[:5]],
        "same_result_second_time": [(a.chunk_id, a.score) for a in first] == [(b.chunk_id, b.score) for b in second],
    }


def _prepare_snapshot(workdir: Path) -> tuple[Path, Path, list[str], float]:
    """스냅샷을 풀고 이 컴퓨터에서 토큰표를 만든다. 반환: (청크 파일, 토큰표, 재는 회사 3곳, 토큰표 만든 시간)."""

    from backend.rag.bm25 import create_tokenizer
    from backend.rag.bm25_tokens import build_token_table, write_token_table

    chunks_path = workdir / "chunks.jsonl"
    with gzip.open(SNAPSHOT_CHUNKS, "rb") as source, chunks_path.open("wb") as target:
        shutil.copyfileobj(source, target)
    chunks = _read_chunks(chunks_path)
    counts = Counter(str(c["corp_code"]) for c in chunks if int(c["fiscal_year"]) == FISCAL_YEAR).most_common()
    companies = [counts[0][0], counts[len(counts) // 2][0], counts[-1][0]]  # 가장 많은 회사, 중간, 가장 적은 회사
    tokenizer = create_tokenizer("kiwi")
    started = time.perf_counter()
    table = build_token_table(chunks, tokenizer)
    build_seconds = time.perf_counter() - started
    tokens_path = workdir / "bm25_tokens.jsonl.gz"
    write_token_table(tokens_path, table, tokenizer_name="kiwi", tokenizer=tokenizer, chunks_sha256=_sha256(chunks_path))
    return chunks_path, tokens_path, companies, build_seconds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--child", nargs=4, metavar=("CORP_CODE", "MODE", "CHUNKS", "TOKENS"))
    parser.add_argument("--source", choices=("auto", "snapshot", "service"), default="auto")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", help="기본: evals/results/w99_bm25_first_search.<source>.json")
    args = parser.parse_args()
    if args.child:
        corp_code, mode, chunks, tokens = args.child
        print(json.dumps(child(corp_code, mode, Path(chunks), Path(tokens)), ensure_ascii=False))
        return 0

    source = args.source
    if source == "auto":
        source = "service" if SERVICE_CHUNKS.is_file() and SERVICE_TOKENS.is_file() else "snapshot"
    workdir = Path(tempfile.mkdtemp(prefix="w99_bm25_"))
    try:
        if source == "service":
            for path in (SERVICE_CHUNKS, SERVICE_TOKENS):
                if not path.is_file():
                    raise SystemExit(f"파일이 없습니다: {path} (저장소에 없는 파일입니다. --source snapshot으로 재세요)")
            chunks_path, tokens_path, companies, build_seconds = SERVICE_CHUNKS, SERVICE_TOKENS, SERVICE_COMPANIES, None
            chunks_label, tokens_label = (str(p.relative_to(ROOT)).replace("\\", "/") for p in (chunks_path, tokens_path))
        else:
            print("스냅샷을 풀고 이 컴퓨터에서 토큰표를 만듭니다(약 30초)...", flush=True)
            chunks_path, tokens_path, companies, build_seconds = _prepare_snapshot(workdir)
            chunks_label, tokens_label = "data/snapshot/chunks.jsonl.gz(푼 것)", "(이 실행에서 만든 임시 토큰표)"

        runs = []
        for corp_code in companies:
            for round_no in range(1, args.repeat + 1):
                for mode in ("without", "with"):  # 번갈아 잰다
                    done = subprocess.run(
                        [sys.executable, "-X", "utf8", __file__, "--child", corp_code, mode, str(chunks_path), str(tokens_path)],
                        capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
                    if done.returncode != 0:
                        raise SystemExit(f"측정 실패({corp_code}, {mode}):\n{done.stderr[-2000:]}")
                    run = json.loads(done.stdout.strip().splitlines()[-1])
                    run["round"] = round_no
                    runs.append(run)
                    print(f"{corp_code} {round_no}회 {mode:7s} 첫 검색 {run['first_search_seconds']:8.3f}초  "
                          f"두 번째 {run['second_search_seconds'] * 1000:6.1f}ms", flush=True)

        summary = []
        for corp_code in companies:
            row = {"corp_code": corp_code, "company_chunks": next(r["company_chunks"] for r in runs if r["corp_code"] == corp_code)}
            for mode in ("without", "with"):
                picked = [r for r in runs if r["corp_code"] == corp_code and r["mode"] == mode]
                row[f"first_search_median_seconds_{mode}_table"] = round(statistics.median(r["first_search_seconds"] for r in picked), 4)
                row[f"second_search_median_ms_{mode}_table"] = round(statistics.median(r["second_search_seconds"] for r in picked) * 1000, 2)
            row["speedup"] = round(row["first_search_median_seconds_without_table"] / max(row["first_search_median_seconds_with_table"], 1e-6), 1)
            row["same_top5_with_and_without_table"] = len({tuple(r["top5_chunk_ids"]) for r in runs if r["corp_code"] == corp_code}) == 1
            summary.append(row)

        record = {
            "what": "사전 등록 99번: 회사별 BM25 첫 검색 시간, 토큰표 없음(그 자리에서 Kiwi 토큰화) 대 있음(미리 계산)",
            "source": source,
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "machine": {"platform": platform.platform(), "python": platform.python_version(), "processor": platform.processor()},
            "chunks_file": {"path": chunks_label, "sha256": _sha256(chunks_path)},
            "token_table": {"path": tokens_label, "sha256": _sha256(tokens_path),
                            "build_seconds_on_this_machine": None if build_seconds is None else round(build_seconds, 1)},
            "question": QUESTION, "fiscal_year": FISCAL_YEAR, "repeat": args.repeat,
            "note": "측정마다 새 프로세스. 청크 읽기·Kiwi 생성·토큰표 읽기는 첫 검색 시간에 넣지 않았다(서버 기동 때 하는 일). "
                    "절대 시간은 컴퓨터와 그때의 부하에 따라 달라진다. 볼 것은 같은 컴퓨터에서 번갈아 잰 표 없음 대 있음의 차이다.",
            "summary": summary, "runs": runs,
        }
        output = ROOT / (args.output or f"evals/results/w99_bm25_first_search.{source}.json")
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"→ {output.relative_to(ROOT)}")
        for row in summary:
            print(f"  {row['corp_code']} 청크 {row['company_chunks']:5d}개: 첫 검색 {row['first_search_median_seconds_without_table']:.3f}초 → "
                  f"{row['first_search_median_seconds_with_table']:.3f}초 ({row['speedup']}배), 결과 같음 {row['same_top5_with_and_without_table']}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
