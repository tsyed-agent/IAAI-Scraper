from iaai_scraper.search_client import extract_total, _build_form


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
