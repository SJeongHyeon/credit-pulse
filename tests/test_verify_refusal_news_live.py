"""실호출 검증 스크립트(scripts/verify_refusal_news_live.py)의 합격 판정 규칙 시험. 키·네트워크 없이 가짜 결과만 쓴다."""
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "verify_refusal_news_live.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_refusal_news_live", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _item(n):
    return {"title": f"t{n}", "link": f"https://n/{n}", "source_domain": f"o{n}.kr", "pub_date": "d"}


def _result(**kw):
    base = {"outcome": "refused", "citations": [], "draft_items": [], "refusal_kind": "work_related",
            "news_search": "ok", "news_calls": 1, "news_items": [_item(i) for i in range(5)]}
    base.update(kw)
    return base


def test_corpus_miss_passes_with_one_call_and_five_items():
    assert _load().judge("corp_out", _result()) == []


def test_corpus_miss_fails_when_news_is_missing():
    failures = _load().judge("corp_out", _result(news_search="error", news_items=[]))
    assert failures and any("news_search" in f for f in failures)


def test_corpus_miss_fails_when_called_more_than_once():
    assert _load().judge("corp_out", _result(news_calls=2))


def test_corpus_miss_fails_when_it_has_citations():
    assert _load().judge("corp_out", _result(citations=[{"chunk_id": "c1"}]))


def test_unrelated_passes_with_zero_news_calls():
    result = _result(refusal_kind="unrelated", news_search="", news_calls=0, news_items=[])
    assert _load().judge("weather", result) == []


def test_unrelated_fails_if_news_was_called():
    assert _load().judge("weather", _result(refusal_kind="unrelated"))


def test_work_related_ood_passes_with_news():
    assert _load().judge("work_ood", _result()) == []


def test_work_related_ood_fails_if_classified_unrelated():
    result = _result(refusal_kind="unrelated", news_calls=0, news_items=[])
    assert _load().judge("work_ood", result)


def test_unknown_case_name_is_an_error():
    import pytest

    with pytest.raises(KeyError):
        _load().judge("nope", _result())
