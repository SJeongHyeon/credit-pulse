"""데모 모드 기록 만들기(SPEC §5.6, G2): 데모 질문을 실제 키로 한 번 돌려 외부 호출 응답을 기록한다.

키 소유자가 저장소 루트에서 실행한다(.env의 OPENAI_API_KEY·VOYAGE_API_KEY 사용, 질문 7개에 약 $0.05).
  python -X utf8 scripts/record_demo.py

1) 기록: `data/snapshot/`으로 만든 검색기 + 데모 모드와 같은 그래프 조립(backend/api/runtime.py)으로
   `data/demo/questions.jsonl`의 질문을 돌리며 채팅·임베딩·리랭크의 입력과 응답을 모은다.
2) 확인: 키를 지운 채 같은 질문을 기록 재생으로 다시 돌려 결과(outcome·거절 사유·판정·이유·인용 청크)가
   같은지 본다. 하나라도 다르면 기록 파일을 쓰지 않고 멈춘다.
결과: `data/demo/recordings.jsonl.gz`(커밋 대상, 키·계정 없음).
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

QUESTIONS = ROOT / "data" / "demo" / "questions.jsonl"
KEY_NAMES = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "VOYAGE_API_KEY")


class _NoPool:
    """기록 중에는 DB를 쓰지 않는다(승인 전에서 멈추고, 비용 상한은 쓰지 않음)."""

    def connection(self):
        raise RuntimeError("기록 스크립트는 DB를 쓰지 않습니다")

    def close(self):
        pass


def _targets() -> dict[str, dict]:
    with gzip.open(ROOT / "data" / "snapshot" / "manifest.jsonl.gz", "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    return {row["corp_name"]: row for row in rows}


def _summary(result: dict) -> dict:
    return {
        "outcome": result.get("outcome"),
        "refusal_reason": result.get("refusal_reason"),
        "draft": [
            {"verdict": item.get("verdict"), "reason": item.get("reason"),
             "citations": [citation.get("chunk_id") for citation in item.get("citations") or []]}
            for item in result.get("draft") or []
        ],
    }


def _run_all(build_runtime) -> list[dict]:
    from backend.api.runtime import build_review_runtime  # noqa: F401  (import 경로 확인)

    targets = _targets()
    built = build_runtime()
    results = []
    for row in (json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()):
        target = targets[row["corp_name"]]

        class Review:
            id = str(uuid.uuid4())

        user_id = str(uuid.uuid4())
        result = built.runner.ask(
            review=Review(), user_id=user_id, question=row["question"],
            thread_id=f"{user_id}:{Review.id}",
            review_target={"corp_code": target["corp_code"], "corp_name": target["corp_name"], "fiscal_year": int(row["fiscal_year"])},
        )
        results.append({"question": row["question"], **_summary(result)})
    return results


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    os.environ["DEMO_MODE"] = "1"  # 확인 단계의 재생이 꺼지지 않게(llm.DEMO_MODE는 import 때 읽힘)
    if not (os.getenv("OPENAI_API_KEY") and os.getenv("VOYAGE_API_KEY")):
        print("OPENAI_API_KEY·VOYAGE_API_KEY가 필요합니다(.env)", file=sys.stderr)
        return 2
    from backend.api import runtime
    from backend.services import demo_replay, llm

    env = {**os.environ, "DATABASE_URL": "unused"}
    recorder = demo_replay.start_recording()
    recorded = _run_all(lambda: runtime.build_review_runtime(env, pool=_NoPool(), checkpointer=InMemorySaver(), recorder=recorder))
    llm.drain_usage()
    temp_path = demo_replay.RECORDINGS_PATH.with_suffix(".tmp.gz")
    count = recorder.save(temp_path)
    print(f"기록 {count}건")

    # 확인: 키를 지우고 기록만으로 다시 돌린다
    saved_keys = {name: os.environ.pop(name) for name in KEY_NAMES if name in os.environ}
    demo_replay._recorder = None
    demo_replay.RECORDINGS_PATH, original_path = temp_path, demo_replay.RECORDINGS_PATH
    demo_replay._loaded = None
    try:
        demo_env = {key: value for key, value in env.items() if key not in KEY_NAMES}
        assert runtime.demo_mode(demo_env)
        replayed = _run_all(lambda: runtime.build_review_runtime(demo_env, pool=_NoPool(), checkpointer=InMemorySaver()))
    finally:
        os.environ.update(saved_keys)
        demo_replay.RECORDINGS_PATH = original_path
    for before, after in zip(recorded, replayed):
        same = before == after
        print(("같음 " if same else "다름 ") + before["question"][:40], "|", before["outcome"], before["refusal_reason"],
              [item["verdict"] for item in before["draft"]])
        if not same:
            print("  기록:", json.dumps(before, ensure_ascii=False)[:400])
            print("  재생:", json.dumps(after, ensure_ascii=False)[:400])
    if recorded != replayed:
        temp_path.unlink(missing_ok=True)
        print("재생 결과가 기록과 달라 기록 파일을 쓰지 않았습니다", file=sys.stderr)
        return 1
    temp_path.replace(original_path)
    print(f"저장: {original_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
