"""kiwipiepy가 없을 때 뉴스 검색어 만들기가 경고를 남기는지 시험. 키·네트워크·kiwipiepy 없이 돈다."""
import logging

import pytest

from backend.services import news_search


@pytest.fixture()
def without_kiwi(monkeypatch):
    def missing():
        raise ImportError("No module named 'kiwipiepy'")

    monkeypatch.setattr(news_search, "_get_kiwi", missing)
    monkeypatch.setattr(news_search, "_FALLBACK_WARNED", False, raising=False)


def test_missing_kiwi_logs_one_warning_and_still_returns_tokens(without_kiwi, caplog):
    with caplog.at_level(logging.WARNING, logger=news_search.__name__):
        tokens = news_search.extract_nouns("오뚜기의 FY2025 공급망")

    assert tokens == ["오뚜기의", "FY2025", "공급망"]  # 기존 대체 동작은 그대로
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "kiwipiepy" in warnings[0].getMessage() and "requirements" in warnings[0].getMessage()


def test_warning_is_logged_only_once_per_process(without_kiwi, caplog):
    with caplog.at_level(logging.WARNING, logger=news_search.__name__):
        news_search.extract_nouns("오뚜기 공급망")
        news_search.extract_nouns("삼성전자 채무보증")

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_no_warning_when_kiwi_is_available(monkeypatch, caplog):
    class FakeToken:
        def __init__(self, form):
            self.form, self.tag = form, "NNG"

    class FakeKiwi:
        def tokenize(self, text):
            return [FakeToken("공급망")]

    monkeypatch.setattr(news_search, "_get_kiwi", lambda: FakeKiwi())
    with caplog.at_level(logging.WARNING, logger=news_search.__name__):
        assert news_search.extract_nouns("공급망") == ["공급망"]

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
