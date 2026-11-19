"""뉴스 회사명 사후 필터 측정 스크립트(scripts/measure_news_company_filter.py)의 계산·판정 시험. 키·네트워크 없이 돈다."""
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "measure_news_company_filter.py"


def _load():
    spec = importlib.util.spec_from_file_location("measure_news_company_filter", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cases_are_eighteen_with_four_new_companies_and_the_hanwha_strike_case():
    mod = _load()

    assert len(mod.CASES) == 18
    assert [c[1] for c in mod.CASES[14:]] == ["동원산업", "한샘", "종근당", "한섬"]
    assert mod.CASES[mod.HANWHA_STRIKE_INDEX][:2] == ("한화시스템의 최근 노조 파업 이슈를 알려주세요.", "한화시스템")


def test_all_mention_and_ocean_count():
    mod = _load()
    items = [{"title": "한화시스템 수주", "description": ""}, {"title": "무관", "description": "한화시스템 언급"}]

    assert mod.all_mention(items, "한화시스템") is True
    assert mod.all_mention(items + [{"title": "한화오션", "description": ""}], "한화시스템") is False
    assert mod.all_mention([], "한화시스템") is True  # 0건이면 위배된 기사가 없다
    assert mod.count_title(items + [{"title": "한화오션 노조", "description": ""}], "한화오션") == 1


def _stats(count, all_mention=None, ocean=0):
    return {"count": count, "all_mention": all_mention if all_mention is not None else [True] * len(count), "ocean": ocean}


def test_judge_passes_when_all_four_criteria_hold():
    mod = _load()

    assert mod.judge_filter(_stats([3] * 15 + [0] * 3)) == {"a": True, "b": True, "c": True, "d": True, "adopt": True}


def test_judge_fails_each_criterion_separately():
    mod = _load()

    assert not mod.judge_filter(_stats([5] * 17 + [0], all_mention=[True] * 17 + [False]))["a"]
    assert not mod.judge_filter(_stats([1] * 18))["b"]  # 평균 1.0 < 2.5
    assert not mod.judge_filter(_stats([5] * 13 + [0] * 5))["c"]  # 0건 5개 > 4개
    assert not mod.judge_filter(_stats([5] * 18, ocean=1))["d"]
    assert not mod.judge_filter(_stats([1] * 18))["adopt"]
