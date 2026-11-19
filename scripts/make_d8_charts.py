"""D8 산출물의 도표를 평가 결과 파일에서 다시 그린다(유료 API 호출 없음).

  python -X utf8 scripts/make_d8_charts.py        # matplotlib·koreanize-matplotlib가 깔린 파이썬으로 실행

읽는 것: evals/results/graph50/의 요약(.summary.json)·문항별 결과(.jsonl)·노드 시간(.node_ms.jsonl).
쓰는 것: docs/images/d8/*.png (docs/deliverables/d8_observability_ci.md가 보여 준다).
숫자를 손으로 적지 않고 결과 파일에서 계산한다. 구성별 실행 목록만 아래 CONFIGS에 적는다.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import koreanize_matplotlib  # noqa: F401,E402  (한글 글꼴)
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results" / "graph50"
OUT = ROOT / "docs" / "images" / "d8"
SUFFIX = "_multi_d3-55_v9_v2"

NAVY, BLUE, ORANGE, GRAY, GREEN = "#2f3e6e", "#4a7fc1", "#e0703a", "#9aa0a6", "#3f9b6c"

# (표시 이름, 실행 목록). 실행 목록은 같은 코드로 돌린 회차들이다.
CONFIGS = [
    ("91번\n11/8 구성\n(코드 규칙 Supervisor)", ["w91_r2", "w91_r3", "w91_r4"]),
    ("104번\n매 방문 LLM 판단", ["w104_new_r1", "w104_new_r2", "w104_new_r3"]),
    ("106번\n첫 검색은\n원래 질문", ["w106_r1"]),
    ("108번\n정해진 길은 코드\n+ 빠른 공급자", ["w108_new_r1"]),
    ("109번\n작성기 출력\n줄이기", ["w109_new_r1"]),
    ("112번\n미리 실행\n+ 초안 2개", ["w112_new_r1"]),
    ("115번\n판단 복원\n7회", ["w115_new_r1", "w116_base_r1", "w116_base_r2", "w116_base_r3",
                          "w117_base_r1", "w117_base_r2", "w117_base_r3"]),
    ("121번(지금)\n병렬 검색·엄격 초안\n먼저·재검색 생략 3회", ["w120_new_r1", "w121_new_r1", "w121_new_r2"]),
]
ADOPTED = CONFIGS[-1][1]
PREVIOUS = CONFIGS[-2][1]  # 121번 채택 전 구성(115번, 7회). 회차별 그림에서 견줄 기준으로 쓴다


def load(run: str):
    summary = json.loads((RESULTS / f"{run}{SUFFIX}.summary.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (RESULTS / f"{run}{SUFFIX}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return summary, rows


def searched_seconds(rows: list[dict]) -> list[float]:
    # 검색 에이전트가 실제로 돈 문항만 센다. 110번부터는 입구에서 거절된 질문에도 미리 시작한 검색 기록이 남으므로
    # search_calls가 있으면 그것으로 보고, 없는 옛 결과(91번)만 retrieval_calls로 본다.
    return [r["elapsed_ms"] / 1000 for r in rows
            if (int(r["search_calls"]) > 0 if "search_calls" in r else len(r.get("retrieval_calls") or []) > 0)]


def pct(values: list[float], q: float) -> float:
    """가까운 순위 방식: 정렬한 뒤 round(q × (n − 1))번째 값. 29건이면 p95는 두 번째로 느린 문항이다."""

    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def config_stats(runs: list[str]) -> dict:
    seconds, costs, metrics = [], [], []
    for run in runs:
        summary, rows = load(run)
        seconds += searched_seconds(rows)
        costs.append(summary["llm_usage"]["cost_usd_known_total"])
        metrics.append({k: v["numerator"] for k, v in summary["metrics"]["response"].items() if isinstance(v, dict)})
    return {"p50": statistics.median(seconds), "p95": pct(seconds, 0.95), "cost": statistics.mean(costs),
            "n": len(seconds), "metrics": metrics}


def chart_latency_progress(stats: list[dict]) -> None:
    labels = [name for name, _ in CONFIGS]
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(12, 5.6))
    p50 = [s["p50"] for s in stats]
    p95 = [s["p95"] for s in stats]
    ax.bar([i - 0.2 for i in x], p50, width=0.4, color=BLUE, label="p50 (절반의 질문이 이 시간 안에 끝남)")
    ax.bar([i + 0.2 for i in x], p95, width=0.4, color=ORANGE, label="p95 (느린 5%를 뺀 나머지가 이 시간 안에 끝남)")
    for i, (a, b) in enumerate(zip(p50, p95)):
        ax.text(i - 0.2, a + 0.8, f"{a:.1f}", ha="center", fontsize=10)
        ax.text(i + 0.2, b + 0.8, f"{b:.1f}", ha="center", fontsize=10)
    ax.set_xticks(list(x), labels, fontsize=9)
    ax.set_ylabel("검색까지 간 질문의 응답 시간(초)")
    ax.set_title("응답 시간은 어떻게 변했나 (v2 50건, 검색까지 간 질문)", fontsize=13, pad=12)
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.01, 0.01, "출처: evals/results/graph50/ 의 w91·w104·w106·w108·w109·w112·w115~w117·w120~w121 결과 파일. 104번에서 구조를 바꾸며 느려졌고, 108번부터 줄였다.",
             fontsize=8, color=GRAY)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(OUT / "latency_progress.png", dpi=150)
    plt.close(fig)


def chart_cost(stats: list[dict]) -> None:
    labels = [name.split("\n")[0] for name, _ in CONFIGS]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    costs = [s["cost"] for s in stats]
    bars = ax.bar(labels, costs, color=[GRAY, ORANGE, ORANGE, ORANGE, BLUE, BLUE, BLUE, NAVY])
    for bar, cost in zip(bars, costs):
        ax.text(bar.get_x() + bar.get_width() / 2, cost + 0.01, f"\${cost:.2f}", ha="center", fontsize=10)
    ax.set_ylabel("질문 50건을 처리하는 비용(달러)")
    ax.set_title("비용은 어떻게 변했나 (v2 50건 한 번 실행)", fontsize=13, pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "cost_progress.png", dpi=150)
    plt.close(fig)


def chart_cost_breakdown() -> None:
    totals: dict[str, float] = {}
    calls: dict[str, int] = {}
    for run in ADOPTED:
        summary, _ = load(run)
        for model, usage in summary["llm_usage"]["by_model"].items():
            totals[model] = totals.get(model, 0) + (usage["cost_usd"] or 0) / len(ADOPTED)
            calls[model] = calls.get(model, 0) + usage["calls"] / len(ADOPTED)
    names = {
        "chat:openai/gpt-4o-mini": "gpt-4o-mini\n(초안 2개·주제 분류·Supervisor 판단)",
        "rerank:rerank-2.5": "Voyage rerank-2.5\n(검색 결과 재정렬)",
        "chat:openai/gpt-4.1-mini": "gpt-4.1-mini\n(검색 채점 등)",
        "embedding:openai/text-embedding-3-small": "text-embedding-3-small\n(질의 임베딩)",
    }
    items = sorted(totals.items(), key=lambda kv: -kv[1])
    fig, ax = plt.subplots(figsize=(9, 4.2))
    labels = [names.get(k, k) for k, _ in items]
    values = [v for _, v in items]
    bars = ax.barh(labels[::-1], values[::-1], color=[GRAY, GREEN, ORANGE, BLUE][: len(values)][::-1])
    total = sum(values)
    for bar, (key, value) in zip(bars, items[::-1]):
        ax.text(value + 0.002, bar.get_y() + bar.get_height() / 2,
                f"\${value:.3f} ({value / total:.0%}, {calls[key]:.0f}회)", va="center", fontsize=10)
    ax.set_xlim(0, max(values) * 1.45)
    ax.set_xlabel(f"50건당 비용(달러, 채택 구성 {len(ADOPTED)}회 평균)")
    ax.set_title(f"돈은 어디에 드나: 50건에 \${total:.3f}, 질문 하나에 약 \${total / 50:.4f}", fontsize=13, pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "cost_breakdown.png", dpi=150)
    plt.close(fig)


def chart_runs() -> None:
    """115번 구성 7회와 121번 구성 3회를 한 그림에 나란히 둔다(세로 점선이 경계)."""

    p50, p95, correct, wrong, refusal = [], [], [], [], []
    runs = PREVIOUS + ADOPTED
    for run in runs:
        summary, rows = load(run)
        seconds = searched_seconds(rows)
        p50.append(statistics.median(seconds)); p95.append(pct(seconds, 0.95))
        response = summary["metrics"]["response"]
        correct.append(response["correct_citation"]["numerator"]); wrong.append(response["wrong_citation"]["numerator"])
        refusal.append(response["correct_refusal"]["numerator"])
    x = list(range(1, len(runs) + 1))
    ticks = [f"115번\n{i}" for i in range(1, len(PREVIOUS) + 1)] + [f"121번\n{i}" for i in range(1, len(ADOPTED) + 1)]
    fig, (left, right) = plt.subplots(1, 2, figsize=(12, 4.4))
    left.plot(x, correct, "o-", color=NAVY, label="정답 근거 인용 (25건 중)")
    left.plot(x, refusal, "s-", color=GREEN, label="올바른 거절 (25건 중)")
    left.plot(x, wrong, "^-", color=ORANGE, label="오답 근거 (25건 중)")
    left.set_ylim(0, 26); left.set_xticks(x, ticks, fontsize=8); left.set_ylabel("문항 수")
    left.set_title("답의 품질: 기준과 같거나 1문항 차이", fontsize=12)
    left.legend(frameon=False, loc="center right")
    right.plot(x, p50, "o-", color=BLUE, label="p50")
    right.plot(x, p95, "o-", color=ORANGE, label="p95")
    for i, (a, b) in zip(x, zip(p50, p95)):
        right.text(i, a - 1.3, f"{a:.1f}", ha="center", fontsize=9); right.text(i, b + 0.5, f"{b:.1f}", ha="center", fontsize=9)
    right.set_ylim(0, 20); right.set_xticks(x, ticks, fontsize=8); right.set_ylabel("응답 시간(초)")
    right.set_title("응답 시간: p50·p95 모두 내려감(p95는 회차마다 움직임)", fontsize=12)
    right.legend(frameon=False, loc="lower right")
    for ax in (left, right):
        ax.axvline(len(PREVIOUS) + 0.5, color=GRAY, linestyle=":")
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(f"115번 구성 {len(PREVIOUS)}회와 지금 구성(121번) {len(ADOPTED)}회 (v2 50건, 같은 명령)", fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT / "adopted_runs.png", dpi=150)
    plt.close(fig)


def chart_timeline() -> None:
    """채택 구성에서 질문 하나가 처리되는 시간표(각 단계의 p50으로 그린 예시). 단계가 겹쳐 도는 것을 보여 준다."""

    retriever, rerank = [], []
    writer, supervisor, total = [], [], []
    for run in ADOPTED:
        _, rows = load(run)
        node_ms = {json.loads(line)["id"]: json.loads(line)["node_ms"]
                   for line in (RESULTS / f"{run}{SUFFIX}.node_ms.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
        for row in rows:
            if not int(row.get("search_calls") or 0):
                continue
            nodes = node_ms[row["id"]]
            leaves = lambda leaf: [v["ms"] / v["calls"] / 1000 for k, v in nodes.items() if k.split("/")[-1] == leaf]
            retriever += leaves("retriever"); writer += leaves("writer")
            rerank += [c["rerank_latency_ms"] / 1000 for c in row.get("retrieval_calls") or []]
            supervisor.append(nodes.get("supervisor", {}).get("ms", 0) / 1000)
            total.append(row["elapsed_ms"] / 1000)
    search = statistics.median(retriever)
    draft = statistics.median(writer)
    done = statistics.median(total)
    classify, grade, decide = 2.0, 1.8, max(0.5, statistics.median(supervisor) - 2.0)
    bars = [
        ("① 입구: 주제 분류 LLM", 0, classify, BLUE),
        ("② 검색 에이전트: 검색 + 리랭크", 0, search, GRAY),
        ("② 검색 에이전트: 근거 채점 LLM", search, grade, BLUE),
        ("③ 작성 에이전트: 초안(보통) LLM", search, draft, BLUE),
        ("③ 작성 에이전트: 초안(엄격) LLM", search, draft, BLUE),
        ("④ Supervisor 판단 LLM (재검색? 작성?)", max(classify, search + grade), decide, NAVY),
        ("⑤ 인용·숫자 검증(코드) 뒤 승인 대기", search + draft, 0.15, GREEN),
    ]
    fig, ax = plt.subplots(figsize=(11, 4.6))
    for i, (label, start, width, color) in enumerate(bars[::-1]):
        ax.barh(i, width, left=start, color=color, height=0.55)
        ax.text(start + width + 0.08, i, f"{start:.1f} ~ {start + width:.1f}초", va="center", fontsize=9)
    ax.set_yticks(range(len(bars)), [b[0] for b in bars[::-1]], fontsize=10)
    ax.axvline(done, color=ORANGE, linestyle="--")
    ax.text(done + 0.08, len(bars) - 0.55, f"답이 나오는 시점\np50 {done:.1f}초", color=ORANGE, fontsize=10, va="top")
    ax.set_xlim(0, max(done + 2.2, 9))
    ax.set_xlabel("질문을 받은 뒤 지난 시간(초)")
    ax.set_title("질문 하나가 처리되는 시간표: 기다리는 동안 다음 일을 미리 한다", fontsize=13, pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.01, 0.01, "단계 길이는 채택 구성 3회의 p50(검색·초안·전체)과 대표값(주제 분류 약 2초, 채점 약 1.8초: 109번 측정)으로 그린 예시다. 실제로는 질문마다 다르다.",
             fontsize=8, color=GRAY)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT / "question_timeline.png", dpi=150)
    plt.close(fig)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    stats = [config_stats(runs) for _, runs in CONFIGS]
    for (name, runs), s in zip(CONFIGS, stats):
        print(name.replace("\n", " "), f"| 실행 {len(runs)}회, 검색 문항 {s['n']}건, p50 {s['p50']:.1f}초, p95 {s['p95']:.1f}초, 비용 ${s['cost']:.3f}")
    chart_latency_progress(stats)
    chart_cost(stats)
    chart_cost_breakdown()
    chart_runs()
    chart_timeline()
    print("저장:", sorted(p.name for p in OUT.glob("*.png")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
