"""평가셋을 동결한다: sha256, 건수, 관련/비관련 건수를 evals/FROZEN.json에 기록.

    python evals/freeze.py dataset_v1.jsonl   # 동결
    python evals/freeze.py --check            # 동결 이후 바뀐 파일이 있으면 실패
"""
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

EVALS = Path(__file__).resolve().parent
FROZEN = EVALS / "FROZEN.json"


def _stats(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "count": len(rows),
        "relevant": sum(r.get("kind") == "relevant" for r in rows),
        "irrelevant": sum(r.get("kind") == "irrelevant" for r in rows),
    }


def main(argv: list[str]) -> int:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8")) if FROZEN.exists() else {}
    if argv == ["--check"]:
        bad = [n for n, m in frozen.items() if _stats(EVALS / n)["sha256"] != m["sha256"]]
        print("동결 이후 바뀐 파일:", bad or "없음")
        return 1 if bad else 0
    if len(argv) != 1 or not (EVALS / argv[0]).exists():
        print(__doc__)
        return 2
    name = argv[0]
    if name in frozen:
        print(f"{name}은 이미 동결됐습니다. 동결한 평가셋은 고치지 않습니다.")
        return 1
    frozen[name] = {**_stats(EVALS / name), "frozen_at": date.today().isoformat()}
    FROZEN.write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(name, frozen[name])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
