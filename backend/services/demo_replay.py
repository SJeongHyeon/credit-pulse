"""데모 모드 재생(SPEC §5.6, G2): 키 없이 기록해 둔 외부 호출 응답을 입력 해시로 돌려준다.

평가자에게는 API 키가 없다. 키가 있는 환경에서 데모 질문을 한 번 돌려 외부 호출(채팅·임베딩·리랭크)의
입력과 응답을 `data/demo/recordings.jsonl.gz`에 기록하고(`scripts/record_demo.py`), 키가 없으면 같은 입력에
같은 응답을 재생한다. 기록에 없는 입력은 `DemoMiss`를 낸다. `DemoMiss`는 '키 없음'과 같은 갈래로 다뤄지도록
`ExternalApiKeyMissing`을 상속한다(예: 주제 판정은 키가 없으면 건너뛴다).

외부 호출 자체는 여전히 llm.py에서만 한다(AGENTS.md 2번). 이 파일은 기록을 읽고 쓰기만 한다.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Callable

from backend.services.llm import ExternalApiKeyMissing

RECORDINGS_PATH = Path(__file__).resolve().parents[2] / "data" / "demo" / "recordings.jsonl.gz"


class DemoMiss(ExternalApiKeyMissing):
    """데모 모드에서 기록에 없는 입력이 왔다(키가 없어 실제 호출도 못 한다)."""


def request_key(kind: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_loaded: dict[str, Any] | None = None
_load_lock = threading.Lock()


def _recordings() -> dict[str, Any]:
    global _loaded
    with _load_lock:
        if _loaded is None:
            _loaded = {}
            if RECORDINGS_PATH.exists():
                with gzip.open(RECORDINGS_PATH, "rt", encoding="utf-8") as stream:
                    for line in stream:
                        if line.strip():
                            row = json.loads(line)
                            _loaded[row["key"]] = row["response"]
        return _loaded


def replay(kind: str, payload: dict[str, Any]) -> Any:
    key = request_key(kind, payload)
    recordings = _recordings()
    if key not in recordings:
        raise DemoMiss("데모 모드: 기록에 없는 요청입니다. API 키를 넣으면 모든 질문에 답합니다")
    return recordings[key]


class Recorder:
    """키가 있는 환경에서 외부 호출의 입력·응답을 모은다. 끝나면 save()로 결정적 gzip을 쓴다."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def record(self, kind: str, payload: dict[str, Any], response: Any) -> None:
        key = request_key(kind, payload)
        with self._lock:
            self._rows[key] = {"key": key, "kind": kind, "response": response}

    def __len__(self) -> int:
        return len(self._rows)

    def save(self, path: Path = RECORDINGS_PATH) -> int:
        lines = [json.dumps(self._rows[key], ensure_ascii=False, sort_keys=True) + "\n" for key in sorted(self._rows)]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            stream.write("".join(lines).encode("utf-8"))
        return len(lines)


_recorder: Recorder | None = None
_enabled = False


def enable() -> None:
    """데모 런타임(backend/api/runtime.py)이 조립될 때만 켠다. 꺼져 있으면 키 없는 채팅은 지금처럼 '키 없음'이다
    (시험·스크립트가 우연히 데모 질문과 같은 입력을 써도 기록이 끼어들지 않게)."""

    global _enabled
    _enabled = True


def enabled() -> bool:
    return _enabled


def start_recording() -> Recorder:
    global _recorder
    _recorder = Recorder()
    return _recorder


def active_recorder() -> Recorder | None:
    return _recorder


def chat_payload(messages: list[dict[str, str]], *, model: str, response_model: type, max_tokens: int | None) -> dict[str, Any]:
    return {"messages": list(messages), "model": model, "response_model": response_model.__name__, "max_tokens": max_tokens}


# ---- 임베더·리랭커에 주입하는 함수(backend/rag의 embed_fn·score_fn 자리) ----

def replay_embeddings(texts: list[str], *, model: str, dimensions: int, **_: Any) -> list[list[float]]:
    return replay("embeddings", {"texts": list(texts), "model": model, "dimensions": dimensions})


def replay_rerank(query: str, documents: list[str], *, model: str, **_: Any) -> list[float]:
    return replay("rerank", {"query": query, "documents": list(documents), "model": model})


def recording_embeddings(real: Callable[..., list[list[float]]], recorder: Recorder):
    def run(texts: list[str], *, model: str, dimensions: int, **kwargs: Any) -> list[list[float]]:
        vectors = real(texts, model=model, dimensions=dimensions, **kwargs)
        recorder.record("embeddings", {"texts": list(texts), "model": model, "dimensions": dimensions},
                        [[float(value) for value in vector] for vector in vectors])
        return vectors

    return run


def recording_rerank(real: Callable[..., list[float]], recorder: Recorder):
    def run(query: str, documents: list[str], *, model: str, **kwargs: Any) -> list[float]:
        scores = real(query, documents, model=model, **kwargs)
        recorder.record("rerank", {"query": query, "documents": list(documents), "model": model},
                        [float(score) for score in scores])
        return scores

    return run
