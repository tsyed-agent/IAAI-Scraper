import gzip
import json

from iaai_scraper.storage import RawWriter


def test_raw_writer_roundtrip_and_flush(tmp_path):
    writer = RawWriter(base_dir=tmp_path)
    writer.write({"StockNum": "1", "Make": "FORD"})
    writer.flush()
    with gzip.open(writer.path, "rt", encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    assert lines == [{"StockNum": "1", "Make": "FORD"}]

    writer.write({"StockNum": "2"})
    writer.close()
    with gzip.open(writer.path, "rt", encoding="utf-8") as fh:
        assert sum(1 for line in fh if line.strip()) == 2
