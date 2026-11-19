"""뉴스 관련 기사 선별 측정 스크립트(scripts/measure_news_relevance.py)의 계산 규칙 시험. 키·네트워크 없이 가짜 기사만 쓴다."""
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "measure_news_relevance.py"


def _load():
    spec = importlib.util.spec_from_file_location("measure_news_relevance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _item(title="t", description="d"):
    return {"title": title, "description": description}


def test_keep_mentioning_title_or_description_keeps_either_match_in_order():
    items = [_item("무관", "무관"), _item("오뚜기 소식", "d"), _item("다른", "오뚜기 언급"), _item("또 무관", "x")]

    kept = _load().keep_mentioning(items, "오뚜기", where="title_or_description", limit=5)

    assert [i["title"] for i in kept] == ["오뚜기 소식", "다른"]


def test_keep_mentioning_title_only_ignores_description_matches():
    items = [_item("다른", "오뚜기 언급"), _item("오뚜기 소식", "d")]

    kept = _load().keep_mentioning(items, "오뚜기", where="title", limit=5)

    assert [i["title"] for i in kept] == ["오뚜기 소식"]


def test_keep_mentioning_cuts_to_limit_and_keeps_input_order():
    items = [_item(f"오뚜기 {n}") for n in range(8)]

    kept = _load().keep_mentioning(items, "오뚜기", where="title", limit=5)

    assert [i["title"] for i in kept] == [f"오뚜기 {n}" for n in range(5)]


def test_keep_mentioning_rejects_unknown_where():
    import pytest

    with pytest.raises(ValueError):
        _load().keep_mentioning([_item()], "x", where="body", limit=5)


def test_first_term_is_the_first_token_of_the_query():
    mod = _load()

    assert mod.first_term("오뚜기 소송") == "오뚜기"
    assert mod.first_term("") == ""


def test_rates_and_adoption_judgement():
    mod = _load()
    items = [_item("삼성전자 투자", "반도체"), _item("무관", "무관")]

    assert mod.title_rate(items, "삼성전자") == 0.5
    assert mod.text_rate(items, "반도체") == 0.5
    assert mod.title_rate([], "x") == 0.0


def test_judge_adoption_requires_all_four_criteria():
    mod = _load()
    base = {"company": [0.2] * 10, "topic": [0.9] * 10, "count": [5] * 10}
    good = {"company": [0.4] * 10, "topic": [0.9] * 10, "count": [4] * 9 + [0]}

    verdict = mod.judge_adoption(base, good)

    assert verdict["a"] and verdict["b"] and verdict["c"] and verdict["d"] and verdict["adopt"]


def test_judge_adoption_fails_when_any_criterion_fails():
    mod = _load()
    base = {"company": [0.2] * 10, "topic": [0.9] * 10, "count": [5] * 10}

    too_small_gain = {"company": [0.25] * 10, "topic": [0.9] * 10, "count": [5] * 10}
    too_few_items = {"company": [0.5] * 10, "topic": [0.9] * 10, "count": [2] * 10}
    many_empty = {"company": [0.5] * 10, "topic": [0.9] * 10, "count": [5] * 7 + [0] * 3}
    topic_drop = {"company": [0.5] * 10, "topic": [0.8] * 10, "count": [5] * 10}

    assert not mod.judge_adoption(base, too_small_gain)["adopt"]
    assert not mod.judge_adoption(base, too_few_items)["b"]
    assert not mod.judge_adoption(base, many_empty)["c"]
    assert not mod.judge_adoption(base, topic_drop)["d"]
