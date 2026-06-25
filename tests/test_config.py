import importlib

from iaai_scraper import config


def test_env_float_falls_back_on_bad_value(monkeypatch):
    monkeypatch.setenv("IAAI_DELAY_MIN", "not-a-number")
    importlib.reload(config)
    assert isinstance(config.DELAY_MIN_S, float)
    monkeypatch.delenv("IAAI_DELAY_MIN", raising=False)
    importlib.reload(config)


def test_env_int_falls_back_on_bad_value(monkeypatch):
    monkeypatch.setenv("IAAI_MAX_LIST_PAGES", "abc")
    importlib.reload(config)
    assert isinstance(config.MAX_LIST_PAGES, int)
    monkeypatch.delenv("IAAI_MAX_LIST_PAGES", raising=False)
    importlib.reload(config)
