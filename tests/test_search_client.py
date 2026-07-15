import asyncio

import pytest

from iaai_scraper.search_client import extract_total, _build_form, SearchClient, SearchResponseError


def test_extract_total_int():
    assert extract_total({"SummaryList": [{"Description": "TOTAL_COUNT", "Count": 5218}]}) == 5218


def test_extract_total_string_is_normalized():
    assert extract_total({"SummaryList": [{"Description": "TOTAL_COUNT", "Count": "5218"}]}) == 5218


def test_extract_total_missing_or_bad():
    assert extract_total({"SummaryList": []}) is None
    assert extract_total({}) is None
    assert extract_total({"SummaryList": [{"Description": "TOTAL_COUNT", "Count": "n/a"}]}) is None


def test_build_form_page1_is_new_search():
    assert _build_form(1, 100, "STOCK ASC")["IsNewSearch"] == "true"
    assert _build_form(2, 100, "STOCK ASC")["IsNewSearch"] == "false"
    assert _build_form(3, 100, "STOCK ASC")["RunlistPageIndex"] == "3"


def test_build_form_branch_ids():
    form = _build_form(1, 1000, "STOCK ASC", branch_ids="10,52,56")
    assert form["BranchIds"] == "10,52,56"
    assert form["PageSize"] == "1000"


class _Session:
    settings = type("Settings", (), {"page_size": 100, "sort": "STOCK ASC", "branch_ids": ""})()

    def __init__(self, payload):
        self.payload = payload

    async def post_json(self, *_args, **_kwargs):
        return self.payload


def test_fetch_page_rejects_malformed_response_contract():
    with pytest.raises(SearchResponseError, match="RunList"):
        asyncio.run(SearchClient(_Session({"SummaryList": []})).fetch_page(1))


def test_fetch_page_rejects_non_object_rows():
    payload = {"RunList": ["bad"], "SummaryList": []}
    with pytest.raises(SearchResponseError, match="non-object"):
        asyncio.run(SearchClient(_Session(payload)).fetch_page(1))


def test_fetch_page_rejects_non_numeric_total_count():
    payload = {"RunList": [], "SummaryList": [{"Description": "TOTAL_COUNT", "Count": "n/a"}]}
    with pytest.raises(SearchResponseError, match="TOTAL_COUNT"):
        asyncio.run(SearchClient(_Session(payload)).fetch_page(1))
