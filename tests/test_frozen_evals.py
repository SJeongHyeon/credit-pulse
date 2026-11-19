"""동결한 평가셋이 바뀌지 않았는지 지킨다.

정답을 바꿔 점수를 맞추면 측정이 아니라 맞추기가 된다. evals/FROZEN.json에 기록된 파일은
해시가 같아야 하고, 다르면 이 테스트가 PR을 막는다.
"""
import hashlib
import json
from pathlib import Path

EVALS = Path(__file__).resolve().parent.parent / "evals"


def test_frozen_files_unchanged():
    frozen = EVALS / "FROZEN.json"
    if not frozen.exists():
        return  # 아직 동결 전 (v1은 11/3에 동결)
    for name, meta in json.loads(frozen.read_text(encoding="utf-8")).items():
        actual = hashlib.sha256((EVALS / name).read_bytes()).hexdigest()
        assert actual == meta["sha256"], f"{name}: 동결 후 바뀌었습니다"
