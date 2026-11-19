"""평가용 답 판정자(docs/specs/answer-judge.md). 그래프 결과 파일을 사후에 읽어 판정 항목마다
grounded(근거 충실)·specific(구체성)을 O/X로 매긴다. 그래프·지표 5개 정의는 바꾸지 않는다.

  run   : 결과 파일 하나를 판정해 <결과>.judge.jsonl·<결과>.judge.summary.json을 쓴다
  sheet : 사람 판정 시트(읽기용 Markdown + 라벨 칸이 빈 CSV)를 만든다. 판정자를 부르지 않는다
  agree : 사람 라벨 CSV와 판정자를 대조해 라벨별 Cohen's kappa·일치율·혼동표를 낸다(P17 R9)

실행 예:
  python -X utf8 scripts/judge_answers.py run --results evals/results/graph50/w91_r2_multi_d3-55_v9_v2.jsonl \
      --chunks data/experiments/b100-v9-fixed_window/chunks.jsonl
"""
from __future__ import annotations

import argparse
import csv
import copy
import json
import random
import re
import sys
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

DEFAULT_DATASET = "evals/dataset_v2.jsonl"
DEFAULT_CHUNKS = "data/experiments/b100-v9-fixed_window/chunks.jsonl"
JUDGE_ATTEMPTS = 2  # 판정자 응답이 입력 항목과 맞지 않으면 한 번만 다시 묻는다(EC-03)

JudgeFn = Callable[..., list[dict]]


class JudgeError(RuntimeError):
    """조용히 넘어가면 안 되는 입력 문제(없는 청크 id, 같은 모델 등)."""


# ── 입력 읽기 ─────────────────────────────────────────

def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def relevant_ids(dataset_rows: Iterable[dict]) -> set[int]:
    return {int(row["id"]) for row in dataset_rows if row.get("kind") == "relevant"}


def cited_chunk_ids(draft: dict) -> list[str]:
    """판정 항목이 인용한 청크 id(순서 유지, 중복 제거)."""
    seen: list[str] = []
    for citation in draft.get("citations") or []:
        chunk_id = str(citation.get("chunk_id") or "")
        if chunk_id and chunk_id not in seen:
            seen.append(chunk_id)
    return seen


def judge_targets(rows: list[dict], relevant: set[int]) -> tuple[list[dict], int]:
    """판정 대상: 관련 문항의 최종 draft_items 중 인용이 있는 항목(AC-01). 반환: (문항별 대상, 건너뛴 항목 수)."""
    targets, skipped = [], 0
    for row in rows:
        if int(row["id"]) not in relevant:
            continue
        items = []
        for index, draft in enumerate(row.get("draft_items") or []):
            ids = cited_chunk_ids(draft)
            if not ids:
                skipped += 1
                continue
            items.append({
                "item_index": index,
                "item": draft.get("item", ""),
                "verdict": draft.get("verdict", ""),
                "reason": draft.get("reason", ""),
                "chunk_ids": ids,
            })
        if items:
            targets.append({"id": int(row["id"]), "question": row.get("question") or "", "items": items})
    return targets, skipped


def load_chunk_texts(chunks_path: str | Path, needed: set[str]) -> dict[str, str]:
    """필요한 청크 본문만 읽는다. 없는 id가 있으면 멈춘다(AC-03: 다른 청크 파일이면 조용히 넘어가지 않음)."""
    texts: dict[str, str] = {}
    with open(chunks_path, encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            chunk_id = str(row.get("chunk_id"))
            if chunk_id in needed:
                texts[chunk_id] = str(row.get("content") or "")
    missing = sorted(needed - set(texts))
    if missing:
        raise JudgeError(f"청크 파일에 없는 인용 id {len(missing)}개: {missing[:5]} (결과와 같은 청크 파일인지 확인)")
    return texts


def question_texts(dataset_rows: list[dict]) -> dict[int, str]:
    return {int(row["id"]): str(row.get("input") or row.get("question") or "") for row in dataset_rows}


def writer_model_of(results_path: Path) -> str | None:
    summary_path = results_path.with_suffix(".summary.json")
    if not summary_path.exists():
        return None
    models = json.loads(summary_path.read_text(encoding="utf-8")).get("models") or {}
    return models.get("writer")


# ── 판정 ─────────────────────────────────────────────

def judge_question(target: dict, texts: dict[str, str], judge_fn: JudgeFn, model: str) -> list[dict] | None:
    """문항 하나를 판정한다. 인용된 청크만 보낸다(AC-02). 두 번 다 형식이 맞지 않으면 None(AC-04)."""
    from backend.services.llm import ExternalApiResponseError

    chunk_ids: list[str] = []
    for item in target["items"]:
        for chunk_id in item["chunk_ids"]:
            if chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)
    chunks = [{"chunk_id": chunk_id, "content": texts[chunk_id]} for chunk_id in chunk_ids]
    items = [{key: item[key] for key in ("item", "verdict", "reason", "chunk_ids")} for item in target["items"]]
    for _ in range(JUDGE_ATTEMPTS):
        try:
            judged = judge_fn(question=target["question"], items=items, chunks=chunks, model=model)
        except ExternalApiResponseError:
            continue
        if len(judged) == len(items):
            return judged
    return None


def run_judge(
    *,
    results_path: Path,
    chunks_path: Path,
    dataset_rows: list[dict],
    correct_ids: set[int],
    model: str,
    judge_fn: JudgeFn,
    usage_fn: Callable[[], list[dict]] = lambda: [],
    tag: str = "",
) -> dict[str, Any]:
    """결과 파일 하나를 판정해 항목별 jsonl과 요약 json을 쓴다. 반환: 요약."""
    from backend.services import llm

    writer = writer_model_of(results_path)
    if writer is not None and writer == model:
        raise JudgeError(f"판정자 모델({model})이 결과의 작성 모델과 같습니다(SPEC §8)")  # AC-05: 부르기 전에 거절

    rows = read_jsonl(results_path)
    questions = question_texts(dataset_rows)
    for row in rows:
        row.setdefault("question", questions.get(int(row["id"]), ""))
    targets, skipped = judge_targets(rows, relevant_ids(dataset_rows))
    needed = {chunk_id for target in targets for item in target["items"] for chunk_id in item["chunk_ids"]}
    texts = load_chunk_texts(chunks_path, needed)  # 없는 id면 여기서 멈추고 아무 파일도 쓰지 않는다

    usage_fn()  # 이전 호출의 사용량을 비운다
    output_rows: list[dict] = []
    failed: list[int] = []
    per_question: dict[int, list[dict]] = {}
    for target in targets:
        judged = judge_question(target, texts, judge_fn, model)
        if judged is None:
            failed.append(target["id"])
            output_rows.append({"id": target["id"], "judge_failed": True})
            continue
        for item, verdict in zip(target["items"], judged):
            record = {
                "id": target["id"],
                "item_index": item["item_index"],
                "item": item["item"],
                "verdict": item["verdict"],
                "grounded": bool(verdict["grounded"]),
                "specific": bool(verdict["specific"]),
                "note": str(verdict.get("note") or ""),
            }
            output_rows.append(record)
            per_question.setdefault(target["id"], []).append(record)
    usage = usage_fn()
    cost = sum(float(record.get("cost_usd") or 0) for record in usage)
    cost_unknown = sum(1 for record in usage if record.get("cost_usd") is None)

    judged_items = [r for r in output_rows if not r.get("judge_failed")]
    useful = sorted(
        qid for qid in correct_ids
        if qid in per_question and all(r["grounded"] and r["specific"] for r in per_question[qid])
    )
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "results_file": str(results_path).replace("\\", "/"),
        "chunks_file": str(chunks_path).replace("\\", "/"),
        "judge_model": model,
        "prompt_version": llm.ANSWER_JUDGE_PROMPT_VERSION,
        "temperature": 0.0,
        "writer_model": writer,
        "judged_questions": len(targets) - len(failed),
        "judged_items": len(judged_items),
        "skipped_no_citation": skipped,
        "judge_failed_questions": failed,
        "grounded_rate": _rate(sum(r["grounded"] for r in judged_items), len(judged_items)),
        "specific_rate": _rate(sum(r["specific"] for r in judged_items), len(judged_items)),
        "correct_citation_questions": len(correct_ids),
        "useful_correct": len(useful),
        "useful_correct_ids": useful,
        "cost_usd": round(cost, 6),
        "cost_unknown_calls": cost_unknown,
    }
    suffix = f".judge{('-' + tag) if tag else ''}"
    out_rows = results_path.with_suffix(f"{suffix}.jsonl")
    out_summary = results_path.with_suffix(f"{suffix}.summary.json")
    with out_rows.open("w", encoding="utf-8", newline="\n") as stream:
        for record in output_rows:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return summary


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator,
            "rate": round(numerator / denominator, 4) if denominator else None}


# ── 사람 판정 대조 ─────────────────────────────────────

def cohen_kappa(human: list[bool], judge: list[bool]) -> float | None:
    """두 판정자의 일치도에서 우연히 맞을 몫을 뺀 값. 둘 다 한 가지로만 답해 우연 일치가 1이면 None(정의 안 됨)."""
    if len(human) != len(judge) or not human:
        raise ValueError("라벨 쌍의 길이가 다르거나 비었습니다")
    n = len(human)
    observed = sum(h == j for h, j in zip(human, judge)) / n
    human_true, judge_true = sum(human) / n, sum(judge) / n
    expected = human_true * judge_true + (1 - human_true) * (1 - judge_true)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def agreement_report(human: list[bool], judge: list[bool], min_each_class: int = 5) -> dict[str, Any]:
    pairs = list(zip(human, judge))
    return {
        "n": len(pairs),
        "agreement": round(sum(h == j for h, j in pairs) / len(pairs), 4),
        "kappa": None if (k := cohen_kappa(human, judge)) is None else round(k, 4),
        "confusion": {  # 사람_판정자
            "O_O": sum(h and j for h, j in pairs), "O_X": sum(h and not j for h, j in pairs),
            "X_O": sum((not h) and j for h, j in pairs), "X_X": sum((not h) and (not j) for h, j in pairs),
        },
        "human_O": sum(human), "human_X": len(human) - sum(human),
        "enough_classes": min(sum(human), len(human) - sum(human)) >= min_each_class,
    }


LABEL_FIELDS = ("source", "id", "item_index", "human_grounded", "human_specific", "human_note")


def read_labels(path: str | Path) -> list[dict]:
    """사람 라벨 CSV. grounded·specific은 O 또는 X만 받는다(빈 칸이 있으면 멈춤)."""
    with open(path, encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for number, row in enumerate(rows, start=2):
        for field in ("human_grounded", "human_specific"):
            if str(row.get(field, "")).strip().upper() not in {"O", "X"}:
                raise JudgeError(f"{path} {number}행 {field}가 O/X가 아닙니다: {row.get(field)!r}")
    return rows


def run_agree(
    *,
    labels_path: Path,
    chunks_path: Path,
    dataset_rows: list[dict],
    model: str,
    judge_fn: JudgeFn,
) -> dict[str, Any]:
    """사람 라벨이 있는 항목을 판정자로 매기고 라벨별 kappa를 낸다. 판정은 문항 단위(run과 같은 입력)로 한다."""
    labels = read_labels(labels_path)
    questions = question_texts(dataset_rows)
    relevant = relevant_ids(dataset_rows)
    by_source: dict[str, list[dict]] = {}
    for label in labels:
        by_source.setdefault(label["source"], []).append(label)

    human_g, human_s, judge_g, judge_s, details = [], [], [], [], []
    for source, source_labels in sorted(by_source.items()):
        rows = read_jsonl(ROOT / source if not Path(source).is_absolute() else source)
        for row in rows:
            row.setdefault("question", questions.get(int(row["id"]), ""))
        targets, _ = judge_targets(rows, relevant)
        targets_by_id = {t["id"]: t for t in targets}
        needed_ids = {int(label["id"]) for label in source_labels}
        missing = needed_ids - set(targets_by_id)
        if missing:
            raise JudgeError(f"{source}: 판정 대상이 아닌 문항의 라벨 {sorted(missing)}")  # EC-06
        needed_chunks = {c for qid in needed_ids for item in targets_by_id[qid]["items"] for c in item["chunk_ids"]}
        texts = load_chunk_texts(chunks_path, needed_chunks)
        for qid in sorted(needed_ids):
            target = targets_by_id[qid]
            judged = judge_question(target, texts, judge_fn, model)
            if judged is None:
                raise JudgeError(f"{source} {qid}번: 판정자 응답이 두 번 모두 형식에 맞지 않음")
            by_index = {item["item_index"]: verdict for item, verdict in zip(target["items"], judged)}
            for label in (lab for lab in source_labels if int(lab["id"]) == qid):
                index = int(label["item_index"])
                if index not in by_index:
                    raise JudgeError(f"{source} {qid}번 {index}항목은 판정 대상(인용 있는 항목)이 아님")
                h_g = label["human_grounded"].strip().upper() == "O"
                h_s = label["human_specific"].strip().upper() == "O"
                human_g.append(h_g)
                human_s.append(h_s)
                judge_g.append(bool(by_index[index]["grounded"]))
                judge_s.append(bool(by_index[index]["specific"]))
                details.append({"source": source, "id": qid, "item_index": index,
                                "human": [h_g, h_s], "judge": [judge_g[-1], judge_s[-1]],
                                "note": by_index[index].get("note", "")})
    real = [d for d in details if not d["source"].endswith(".contrast.jsonl")]  # 대조 항목을 뺀 실제 항목만
    real_only = {
        label: agreement_report([d["human"][k] for d in real], [d["judge"][k] for d in real])
        for k, label in enumerate(("grounded", "specific"))
    } if real else {}
    return {
        "judge_model": model,
        "labels_file": str(labels_path).replace("\\", "/"),
        "grounded": agreement_report(human_g, judge_g),
        "specific": agreement_report(human_s, judge_s),
        "real_only": real_only,
        "details": details,
    }


# ── 사람 판정 시트 ─────────────────────────────────────

def sheet_pool(results_paths: list[Path], dataset_rows: list[dict]) -> list[dict]:
    questions = question_texts(dataset_rows)
    relevant = relevant_ids(dataset_rows)
    pool = []
    for path in results_paths:
        rows = read_jsonl(path)
        for row in rows:
            row.setdefault("question", questions.get(int(row["id"]), ""))
        targets, _ = judge_targets(rows, relevant)
        source = _repo_relative(path)
        for target in targets:
            for item in target["items"]:
                pool.append({"source": source, "id": target["id"], "question": target["question"], **item})
    return pool


CONTRAST_KINDS = ("number_swap", "vague")  # number_swap: grounded X 후보, vague: specific X 후보
VAGUE_REASON = "해당 항목 관련 내역이 공시에 명시되어 있음"
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def swappable_numbers(reason: str) -> list[re.Match]:
    """바꿔 볼 숫자 후보. 한 자리 수와 연도(1900~2099)는 바꿔도 눈에 띄거나 뜻이 약해 뺀다."""
    candidates = []
    for match in _NUMBER.finditer(reason):
        integer_part = match.group().replace(",", "").split(".")[0]
        if len(integer_part) < 2:
            continue
        if len(integer_part) == 4 and "," not in match.group() and 1900 <= int(integer_part) <= 2099:
            continue
        candidates.append(match)
    return candidates


def swap_number(reason: str, rng: random.Random) -> str | None:
    """숫자 하나의 첫 자리를 다른 0이 아닌 숫자로 바꾼다((d+4)%9+1은 항상 d와 다름). 후보가 없으면 None."""
    candidates = swappable_numbers(reason)
    if not candidates:
        return None
    match = rng.choice(candidates)
    text = match.group()
    changed = str((int(text[0]) + 4) % 9 + 1) + text[1:]
    return reason[:match.start()] + changed + reason[match.end():]


SNIPPET_CHARS = 50  # 숫자 앞뒤로 보여 줄 글자 수


def number_evidence(reason: str, chunk_texts: list[str]) -> list[dict]:
    """reason의 숫자(두 자리 이상·소수)마다 인용 청크에 같은 숫자가 있는지 찾는다. 판정이 아니라 사람 판정을 돕는 단순 검색이다.

    먼저 그대로 찾고, 없으면 쉼표를 뺀 숫자로 쉼표를 뺀 본문에서 찾는다(표는 '4468', 문장은 '4,468'로 적기도 함)."""
    rows, seen = [], set()
    for match in _NUMBER.finditer(reason):
        number = match.group().rstrip(",.")
        digits = number.replace(",", "")
        if number in seen or (len(digits) < 2 and "." not in digits):
            continue
        seen.add(number)
        snippet = ""
        for text in chunk_texts:
            for needle, haystack in ((number, text), (digits, text.replace(",", ""))):
                position = _find_number(haystack, needle)
                if position >= 0:
                    start = max(0, position - SNIPPET_CHARS)
                    snippet = haystack[start:position + len(needle) + SNIPPET_CHARS]
                    break
            if snippet:
                break
        rows.append({"number": number, "found": bool(snippet), "snippet": snippet})
    return rows


def _find_number(text: str, needle: str) -> int:
    """다른 숫자의 일부가 아닌 위치에서 찾는다(예: '31'이 '2031'의 일부로 잡히지 않게)."""
    for match in re.finditer(re.escape(needle), text):
        start, end = match.start(), match.end()
        before = text[max(0, start - 2):start]  # 바로 앞 두 글자: '5' 또는 '5.'·'5,'면 다른 숫자의 일부
        after = text[end:end + 2]
        joined_before = before[-1:].isdigit() or (before[-1:] in ".," and before[:1].isdigit())
        joined_after = after[:1].isdigit() or (after[:1] in ".," and after[1:2].isdigit())
        if not joined_before and not joined_after:
            return start
    return -1


def _table_cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def make_contrasts(
    pool: list[dict], rows_by_source: dict[str, dict[int, dict]], per_kind: int, rng: random.Random,
) -> tuple[list[dict], list[dict]]:
    """실제 항목의 reason을 기계적으로 바꾼 대조 항목을 만든다(사람 판정에 X가 모자라는 문제, Spec '사람 판정 표본').

    문항 id가 겹치지 않게 고른다(대조 결과 파일 한 줄 = 한 문항). 반환: (시트 항목, 대조 결과 파일 줄)."""
    order = pool[:]
    rng.shuffle(order)
    used_ids: set[int] = set()
    entries, rows = [], []
    for kind in CONTRAST_KINDS:
        made = 0
        for p in order:
            if made == per_kind:
                break
            if p["id"] in used_ids:
                continue
            if kind == "number_swap":
                new_reason = swap_number(p["reason"], rng)
            else:
                new_reason = VAGUE_REASON if _NUMBER.search(p["reason"]) else None  # 원래 구체적이던 항목만
            if new_reason is None:
                continue
            base_row = rows_by_source[p["source"]][p["id"]]
            draft_items = copy.deepcopy(base_row["draft_items"])
            draft_items[p["item_index"]]["reason"] = new_reason
            rows.append({"id": p["id"], "draft_items": draft_items,
                         "contrast": {"kind": kind, "base_source": p["source"], "item_index": p["item_index"],
                                      "original_reason": p["reason"]}})
            entries.append({**p, "reason": new_reason, "contrast_kind": kind,
                            "base_key": (p["source"], p["id"], p["item_index"])})
            used_ids.add(p["id"])
            made += 1
        if made < per_kind:
            raise JudgeError(f"대조 항목 {kind}를 {per_kind}개 만들 후보가 없습니다({made}개)")
    return entries, rows


def make_sheet(
    *,
    results_paths: list[Path],
    chunks_path: Path,
    dataset_rows: list[dict],
    n: int,
    seed: int,
    out_prefix: Path,
    contrast_per_kind: int = 0,
) -> list[dict]:
    """n항목(실제 + 대조)을 뽑아 읽기용 Markdown과 라벨 칸이 빈 CSV를 쓴다. 판정자를 부르지 않는다(AC-08).

    대조 항목이 있으면 `<out>.contrast.jsonl`(결과 파일과 같은 형식)에 쓰고 CSV의 source가 그 파일을 가리킨다.
    Markdown에는 출처를 적지 않고 순서를 섞어, 사람이 대조 항목인지 모르고 판정하게 한다."""
    rng = random.Random(seed)
    pool = sheet_pool(results_paths, dataset_rows)
    rows_by_source = {_repo_relative(path): {int(r["id"]): r for r in read_jsonl(path)} for path in results_paths}
    contrast_entries, contrast_rows = make_contrasts(pool, rows_by_source, contrast_per_kind, rng) if contrast_per_kind else ([], [])
    real_count = n - len(contrast_entries)
    used = {entry["base_key"] for entry in contrast_entries}
    remaining = [p for p in pool if (p["source"], p["id"], p["item_index"]) not in used]  # 원본과 대조가 함께 나오지 않게
    if real_count < 0 or real_count > len(remaining):
        raise JudgeError(f"실제 후보 {len(remaining)}항목에서 {real_count}개를 뽑을 수 없습니다(n={n})")
    picked = rng.sample(remaining, real_count)
    if contrast_rows:
        contrast_path = out_prefix.with_suffix(".contrast.jsonl")
        with contrast_path.open("w", encoding="utf-8", newline="\n") as stream:
            for row in contrast_rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        contrast_source = _repo_relative(contrast_path)
        picked += [{**entry, "source": contrast_source} for entry in contrast_entries]
    rng.shuffle(picked)
    texts = load_chunk_texts(chunks_path, {c for p in picked for c in p["chunk_ids"]})

    lines = [
        "# 답 판정자 사람 판정 시트 (answer-judge-v1)",
        "",
        "라벨 정의는 `docs/specs/answer-judge.md`의 표와 같다. 판정은 같은 폴더의 CSV에 O/X로 적는다(번호 `no`가 같음).",
        "- **grounded**: reason의 사실·숫자·당사자가 모두 아래 인용 청크에 있으면 O. 없는 사실·다른 숫자·계산해야만 나오는 합계·비율이 있으면 X",
        "- **specific**: 질문이 물은 구체 사실(금액·건수·상대방·기간·조건)을 하나 이상 적었으면 O. '내역이 명시되어 있음'처럼 내용을 말하지 않으면 X",
        "- 일부 항목은 reason을 일부러 바꾼 대조 항목이다. 출처를 보지 말고 reason과 인용 청크만 보고 판정한다.",
        "",
        "**빠른 판정 순서** (항목마다 1분 안팎)",
        "1. 숫자 표에 ❌가 있으면 원문을 펼쳐 정말 없는지 한 번 보고, 없으면 grounded X.",
        "2. 모두 ✅면 '청크에서 나온 곳'이 reason과 **같은 뜻**인지 본다(같은 숫자가 다른 항목·다른 연도로 우연히 있을 수 있음). 같으면 grounded O.",
        "3. 숫자 표 대신 '숫자 없음'이 나오면 reason의 회사·사실이 원문에 있는지 직접 본다.",
        "4. specific: 질문이 물은 구체 사실이 reason에 하나라도 있으면 O, 내용 없이 '명시되어 있음'만 있으면 X.",
        "",
        "숫자 표는 판정이 아니라 단순 검색 결과다(쉼표를 뺀 표기도 찾음). 최종 O/X는 사람이 정한다.",
        "",
    ]
    for number, p in enumerate(picked, start=1):
        lines += [
            f"## {number}. {p['id']}번 문항 / 항목 {p['item_index']}",
            "",
            f"- **질문**: {p['question']}",
            f"- **item**: {p['item']} / **verdict**: {p['verdict']}",
            f"- **reason**: {p['reason']}",
            "",
        ]
        evidence = number_evidence(p["reason"], [texts[c] for c in p["chunk_ids"]])
        if evidence:
            lines += ["| reason 속 숫자 | 인용 청크에 | 청크에서 나온 곳 |", "|---|---|---|"]
            for row in evidence:
                mark = "✅ 있음" if row["found"] else "❌ 없음"
                lines.append(f"| {row['number']} | {mark} | {_table_cell(row['snippet']) or '-'} |")
            lines.append("")
        else:
            lines += ["(reason에 두 자리 이상 숫자 없음: 회사·당사자 같은 다른 사실이 청크에 있는지 직접 확인)", ""]
        for chunk_id in p["chunk_ids"]:
            lines += [f"<details><summary>인용 청크 원문 {chunk_id}</summary>", "", "```text", texts[chunk_id], "```", "", "</details>", ""]
    md_path = out_prefix.with_suffix(".md")
    md_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    csv_path = out_prefix.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["no", *LABEL_FIELDS])
        writer.writeheader()
        for number, p in enumerate(picked, start=1):
            writer.writerow({"no": number, "source": p["source"], "id": p["id"], "item_index": p["item_index"],
                             "human_grounded": "", "human_specific": "", "human_note": ""})
    return picked


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


# ── CLI ───────────────────────────────────────────────

def _correct_ids(dataset_rows: list[dict], chunks_path: Path, rows: list[dict]) -> set[int]:
    """정답 근거 O 문항(러너와 같은 정의: span 정답 청크와 인용이 겹침)."""
    from evals.response_metrics import per_question_flags
    from run_graph_eval import load_span_chunks, span_gold_rows

    gold = {int(r["id"]): r for r in span_gold_rows(dataset_rows, load_span_chunks(dataset_rows, chunks_path))}
    return {int(row["id"]) for row in rows
            if gold.get(int(row["id"]), {}).get("kind") == "relevant" and per_question_flags(gold[int(row["id"])], row)["correct_citation"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--results", required=True)
    run_p.add_argument("--tag", default="", help="같은 결과를 두 번 판정할 때 출력 이름 구분(예: rep2)")
    sheet_p = sub.add_parser("sheet")
    sheet_p.add_argument("--results", nargs="+", required=True)
    sheet_p.add_argument("--n", type=int, default=40)
    sheet_p.add_argument("--seed", type=int, default=20261108)
    sheet_p.add_argument("--contrast-per-kind", type=int, default=0,
                         help="대조 항목 종류(number_swap·vague)마다 몇 개 섞을지. --n은 대조 포함 전체 수")
    sheet_p.add_argument("--out", required=True, help="확장자 없는 출력 경로(.md와 .csv를 씀)")
    agree_p = sub.add_parser("agree")
    agree_p.add_argument("--labels", required=True)
    agree_p.add_argument("--out", required=True)
    for p in (run_p, sheet_p, agree_p):
        p.add_argument("--chunks", default=DEFAULT_CHUNKS)
        p.add_argument("--dataset", default=DEFAULT_DATASET)
    for p in (run_p, agree_p):
        p.add_argument("--model", default=None, help="판정자 모델(기본 llm.DEFAULT_JUDGE_MODEL)")
    args = parser.parse_args(argv)

    dataset_rows = read_jsonl(args.dataset)
    if args.command == "sheet":
        picked = make_sheet(results_paths=[Path(p) for p in args.results], chunks_path=Path(args.chunks),
                            dataset_rows=dataset_rows, n=args.n, seed=args.seed, out_prefix=Path(args.out),
                            contrast_per_kind=args.contrast_per_kind)
        print(f"시트 {len(picked)}항목: {Path(args.out).with_suffix('.md')}, {Path(args.out).with_suffix('.csv')}")
        return 0

    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True), override=False)
    from backend.services import llm, tracing

    model = args.model or llm.DEFAULT_JUDGE_MODEL
    try:
        if args.command == "run":
            results_path = Path(args.results)
            summary = run_judge(
                results_path=results_path, chunks_path=Path(args.chunks), dataset_rows=dataset_rows,
                correct_ids=_correct_ids(dataset_rows, Path(args.chunks), read_jsonl(results_path)),
                model=model, judge_fn=llm.judge_answers, usage_fn=llm.drain_usage, tag=args.tag,
            )
            print(json.dumps({k: v for k, v in summary.items() if k != "useful_correct_ids"}, ensure_ascii=False, indent=2))
        else:
            report = run_agree(labels_path=Path(args.labels), chunks_path=Path(args.chunks),
                               dataset_rows=dataset_rows, model=model, judge_fn=llm.judge_answers)
            report["cost_usd"] = round(sum(float(r.get("cost_usd") or 0) for r in llm.drain_usage()), 6)
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(json.dumps({k: report[k] for k in ("judge_model", "grounded", "specific", "cost_usd")}, ensure_ascii=False, indent=2))
    finally:
        tracing.flush()  # 짧게 끝나는 프로세스(SDD 13.1, P19)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
