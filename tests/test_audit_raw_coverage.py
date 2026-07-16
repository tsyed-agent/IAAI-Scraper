import gzip
import json

from scripts.audit_raw_coverage import audit
from tests.conftest import make_row


def _snapshot(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_audit_accepts_complete_unique_snapshot(tmp_path):
    path = tmp_path / "snapshot.jsonl.gz"
    _snapshot(path, [make_row("100"), make_row("200")])
    result = audit([path], min_ontario_lots=2)
    assert result["ok"] is True
    assert result["ontario_lots"] == 2
    assert result["parse_failures"] == 0


def test_audit_rejects_duplicates_and_parse_failures(tmp_path):
    path = tmp_path / "snapshot.jsonl.gz"
    _snapshot(path, [make_row("100"), make_row("100"), {"Make": "missing stock"}])
    result = audit([path])
    assert result["ok"] is False
    assert result["duplicate_rows"] == 1
    assert result["parse_failures"] == 1
