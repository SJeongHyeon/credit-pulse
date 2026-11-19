"""뉴스 검색어 회사명 보존 측정 스크립트(scripts/measure_news_query_company.py)의 계산 규칙 시험. 키·네트워크 없이 돈다."""
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "measure_news_query_company.py"


def _load():
    spec = importlib.util.spec_from_file_location("measure_news_query_company", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _item(title="t", description="d"):
    return {"title": title, "description": description}


def test_rates():
    mod = _load()
    items = [_item("한화시스템 수주", "방산"), _item("무관", "방산 소식")]

    assert mod.title_rate(items, "한화시스템") == 0.5
    assert mod.text_rate(items, "방산") == 1.0
    assert mod.title_rate([], "x") == 0.0 and mod.text_rate([], "x") == 0.0


def _stats(company, topic, count, queries=None):
    return {"company": company, "topic": topic, "count": count, "query": queries or ["q"] * len(company)}


def test_judge_passes_when_all_four_criteria_hold():
    mod = _load()
    base = _stats([0.2] * 10, [0.9] * 10, [5] * 10, ["a"] * 10)
    cand = _stats([0.5] * 10, [0.9] * 10, [4] * 10, ["a"] * 9 + ["a"])

    verdict = mod.judge_adoption(base, cand, control_index=9)

    assert verdict == {"a": True, "b": True, "c": True, "d": True, "adopt": True}


def test_judge_fails_each_criterion_separately():
    mod = _load()
    base = _stats([0.2] * 10, [0.9] * 10, [5] * 10, ["a"] * 10)

    small_gain = _stats([0.3] * 10, [0.9] * 10, [5] * 10, ["a"] * 10)
    topic_drop = _stats([0.6] * 10, [0.8] * 10, [5] * 10, ["a"] * 10)
    too_empty = _stats([0.6] * 10, [0.9] * 10, [5] * 7 + [0] * 3, ["a"] * 10)
    control_changed = _stats([0.6] * 10, [0.9] * 10, [5] * 10, ["a"] * 9 + ["CHANGED"])

    assert not mod.judge_adoption(base, small_gain, control_index=9)["a"]
    assert not mod.judge_adoption(base, topic_drop, control_index=9)["b"]
    assert not mod.judge_adoption(base, too_empty, control_index=9)["c"]
    assert not mod.judge_adoption(base, control_changed, control_index=9)["d"]
    assert not mod.judge_adoption(base, small_gain, control_index=9)["adopt"]


# ── 2차(Q) 채택 판정: 사전 등록 기준 (a)~(d) ──


def _q(company_text, topic, count, control_company_text=None):
    n = len(count)
    return {"company_text": company_text, "topic": topic, "count": count, "query": ["q"] * n,
            "control_company_text": control_company_text if control_company_text is not None else company_text[9]}


def test_cases_have_fourteen_with_four_new_unprobed_companies():
    mod = _load()

    assert len(mod.CASES) == 14
    assert [c[1] for c in mod.CASES[10:]] == ["코웨이", "크래프톤", "신한지주", "하나금융지주"]
    assert mod.CASES[mod.CONTROL_INDEX][1] == "오뚜기"


def test_judge_quoted_passes_when_all_criteria_hold():
    mod = _load()
    base = _q([0.2] * 14, [0.9] * 14, [5] * 14)
    cand = _q([0.6] * 14, [0.85] * 14, [4] * 14)

    assert mod.judge_quoted(base, cand, control_index=9) == {"a": True, "b": True, "c": True, "d": True, "adopt": True}


def test_judge_quoted_fails_each_criterion_separately():
    mod = _load()
    base = _q([0.2] * 14, [0.9] * 14, [5] * 14)

    small = _q([0.3] * 14, [0.9] * 14, [5] * 14)
    topic_drop = _q([0.6] * 14, [0.7] * 14, [5] * 14)
    too_empty = _q([0.6] * 14, [0.9] * 14, [5] * 11 + [0] * 3)
    control_worse = _q([0.6] * 9 + [0.1] + [0.6] * 4, [0.9] * 14, [5] * 14)

    assert not mod.judge_quoted(base, small, control_index=9)["a"]
    assert not mod.judge_quoted(base, topic_drop, control_index=9)["b"]
    assert not mod.judge_quoted(base, too_empty, control_index=9)["c"]
    assert not mod.judge_quoted(base, control_worse, control_index=9)["d"]
    assert not mod.judge_quoted(base, small, control_index=9)["adopt"]


def test_main_uses_the_quoted_judge_not_the_first_round_judge():
    """첫 실행에서 main이 옛 판정(judge_adoption, 'P' 키)을 불러 KeyError로 끝난 결함의 재발 방지."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'judge_quoted(stats["B0"], stats["Q"]' in source
    assert 'stats["P"]' not in source
