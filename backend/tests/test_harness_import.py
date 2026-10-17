from app.harness.baseline import gtin_ok, map_header
from app.harness.schema import parse_and_validate


def test_gtin_check_digit():
    assert gtin_ok("2000000000008") is True
    assert gtin_ok("2000000000009") is False
    assert gtin_ok("123") is False


def test_header_mapping():
    assert map_header("Nama Produk") == "name"
    assert map_header("Kolom Aneh Banget") is None


def test_schema_rejects_garbage():
    recs, _errs, ok = parse_and_validate("bukan json")
    assert recs == {} and ok is False
