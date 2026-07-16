import gzip
import json

from iaai_scraper.storage import RawWriter


def test_raw_writer_roundtrip_and_flush(tmp_path):
    writer = RawWriter(base_dir=tmp_path)
    writer.write({"StockNum": "1", "Make": "FORD"})
    writer.flush()
    with open(writer._staging, "rt", encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    assert lines == [{"StockNum": "1", "Make": "FORD"}]

    writer.write({"StockNum": "2"})
    writer.close()
    with gzip.open(writer.path, "rt", encoding="utf-8") as fh:
        assert sum(1 for line in fh if line.strip()) == 2


def test_raw_writer_persists_parse_dlq(tmp_path):
    writer = RawWriter(base_dir=tmp_path)
    writer.persist_before_parse({"StockNum": "bad"})
    writer.write_dlq({"StockNum": "bad"}, ValueError("missing make"))
    writer.close()
    with gzip.open(writer.dlq_path, "rt", encoding="utf-8") as fh:
        envelope = json.loads(next(fh))
    assert envelope["row"] == {"StockNum": "bad"}
    assert "missing make" in envelope["error"]
