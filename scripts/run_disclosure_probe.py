"""평가셋 밖 탐침 질문을 전체 그래프(단일)로 돌려 거절 여부만 기록한다(사전 등록 72번 과적합 확인).

동결 평가셋이 아니라 응답 지표에 넣지 않는다. 사용:
  python scripts/run_disclosure_probe.py evals/probes/disclosure_probe_v2.jsonl evals/results/probe_disclosure_v2_w72.jsonl
  python scripts/run_disclosure_probe.py <탐침> <결과> --service   # 84번: 서비스 구성(멀티·Supervisor 판단·100사 검색 산출물)

--service: 멀티 그래프(build_supervisor_graph, 104번 Supervisor 판단)와 data/service/의 100사 검색 산출물을 쓴다
(scripts/prepare_service_data.py로 만든다). 평가셋 밖 회사를 물으려면 이 구성이 필요하다.
expected가 "refused"면 거절 사유와 상관없이 거절이면 맞음으로 센다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(probe_path: str, output_path: str, *flags: str) -> int:
    import os

    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True), override=False)
    service = "--service" in flags
    # 86번: --generation-model <모델> 로 작성·주제 분류 모델을 바꾼다(없으면 llm 기본 모델)
    model = flags[flags.index("--generation-model") + 1] if "--generation-model" in flags else None
    if service:
        os.environ.update({"RETRIEVAL_COMBO": "d3-55", "RETRIEVAL_CHUNKS_PATH": "data/service/chunks.jsonl",
                           "RETRIEVAL_EMBEDDING_PREFIX": "data/service/embeddings"})
    from backend.graph.build import build_graph, build_supervisor_graph
    from backend.retrieval_combo import build_retriever, settings_from_env
    from backend.services import llm

    rows = [json.loads(line) for line in Path(probe_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    builder = build_supervisor_graph if service else build_graph
    graph = builder(retriever=build_retriever(settings_from_env()), domain_check_enabled=True,
                    **({"generation_model": model} if model else {}))
    llm.drain_usage()
    results, correct = [], 0
    for row in rows:
        final = graph.invoke({"user_id": "probe", "review_id": f"probe-{row['id']}", "question": row["input"],
                              "node_events": [], "retrieval_rounds": 0})
        got = final.get("refusal_reason") if final.get("outcome") == "refused" else "answered"
        ok = got == row["expected"] or (row["expected"] == "refused" and final.get("outcome") == "refused")
        correct += ok
        results.append({**row, "got": got, "correct": ok, "domain_check": final.get("domain_check"),
                        "verdicts": [d.get("verdict") for d in final.get("draft_items", [])],
                        "node_events": final.get("node_events", [])})
        print(row["id"], row["expected"], "->", got, "O" if ok else "X")
    cost = sum(u.get("cost_usd") or 0 for u in llm.drain_usage())
    with open(output_path, "w", encoding="utf-8", newline="\n") as stream:
        for record in results:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"정답 {correct}/{len(rows)}, 비용 ${cost:.4f} → {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
