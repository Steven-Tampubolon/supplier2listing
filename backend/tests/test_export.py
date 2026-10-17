import csv
import io

import pytest

from app.export import COLUMNS, ExportError, build_export, safe_text
from app.parse import parse_file
from app.rules import extract_rules
from app.settings import SAMPLES_DIR

GOOD_GTIN = "2410725410660"
BAD_GTIN = "2348588283593"


def fe(value, status="extracted", **kw):
    return {"value": value, "status": status, "evidence": None, "confidence": None, **kw}


def rec(sku="A-1", approved=True, **fields):
    base = {
        "name": fe("Celana Chino"), "brand": fe("Lumora"), "category": fe("Apparel > Bottoms > Pants", "normalized"),
        "cost_price": fe(221000, unit="IDR"), "stock": fe(70), "material": fe("Cotton"), "color": fe("pink"),
        "size": fe("30"), "weight_item": fe(0.5, unit="kg"), "gtin": fe(GOOD_GTIN),
    }
    base.update(fields)
    return {"supplier_sku": sku, "fields": base, "issues": [], "approved": approved}


def export_rows(records, category="fashion", confirm=False):
    out = build_export(records, category, confirm)
    return out, list(csv.DictReader(io.StringIO(out["csv"], newline="")))


def test_columns_exact_and_sell_price_always_empty():
    out, rows = export_rows([rec(sell_price=fe(99999))])
    assert out["csv"].splitlines()[0].split(",") == COLUMNS
    assert COLUMNS[-1] == "sell_price"
    assert rows[0]["sell_price"] == ""


def test_only_approved_rows_are_exported():
    out, rows = export_rows([rec("A-1"), rec("A-2", approved=False), {**rec("A-3"), "approved": "true"}])
    assert [r["supplier_sku"] for r in rows] == ["A-1"]
    assert out["skipped_unapproved"] == 2


def test_no_approved_rows_is_an_error():
    with pytest.raises(ExportError, match="Tidak ada baris"):
        build_export([rec(approved=False)], "fashion")


def test_missing_values_are_empty_never_null_or_na():
    _, rows = export_rows([rec(gtin=fe(None, "missing"), color=fe("N/A"), material=fe("null"), size=fe(""))])
    r = rows[0]
    assert r["gtin"] == "" and r["color"] == "" and r["material"] == "" and r["size"] == ""
    assert "null" not in r.values() and "None" not in r.values()


def test_missing_status_wins_even_if_value_present():
    _, rows = export_rows([rec(gtin=fe(GOOD_GTIN, "missing"))])
    assert rows[0]["gtin"] == ""


def test_a_whole_field_absent_is_empty():
    r = rec()
    del r["fields"]["color"]
    _, rows = export_rows([r])
    assert rows[0]["color"] == ""


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (310, "g", "310"), (310, "gr", "310"), (0.12, "kg", "120"), (0.002, "kg", "2"),
        (10.58, "oz", "300"), (4.23, "oz", "120"), (1, "lb", "454"), ("0,5 kg", None, "500"),
        ("350 g", "kg", "350"), (310, "G", "310"),
    ],
)
def test_weight_converted_to_grams(value, unit, expected):
    _, rows = export_rows([rec(weight_item=fe(value, unit=unit))])
    assert rows[0]["weight_g"] == expected


def test_unknown_weight_unit_is_blank_with_warning():
    out, rows = export_rows([rec(weight_item=fe(5, unit="stone"))])
    assert rows[0]["weight_g"] == ""
    assert any("weight_g" in w and "A-1" in w for w in out["warnings"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"value": 50, "unit": "ml"}, "50 ml"), ({"value": 1.69, "unit": "fl oz"}, "50 ml"),
        ({"value": 0.5, "unit": "L"}, "500 ml"), ({"value": 4.5, "unit": "g"}, "4.5 g"),
        ({"value": 2, "unit": "oz"}, "57 g"), ("75 g", "75 g"),
    ],
)
def test_net_content_normalised_to_ml_or_g(value, expected):
    _, rows = export_rows([rec(net_content=fe(value))], category="beauty")
    assert rows[0]["net_content"] == expected


def test_net_content_unknown_unit_blank():
    out, rows = export_rows([rec(net_content=fe({"value": 3, "unit": "pcs"}))], category="beauty")
    assert rows[0]["net_content"] == "" and out["warnings"]


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [(12, "months", "12"), (60.0, "bulan", "60"), (2, "tahun", "24"), (1, "year", "12"), (6, "Bulan", "6")],
)
def test_warranty_in_months(value, unit, expected):
    _, rows = export_rows([rec(warranty=fe(value, unit=unit))], category="electronics")
    assert rows[0]["warranty_months"] == expected


def test_warranty_without_unit_is_not_guessed():
    out, rows = export_rows([rec(warranty=fe(12))], category="electronics")
    assert rows[0]["warranty_months"] == "" and out["warnings"]


def test_price_and_currency():
    _, rows = export_rows([rec(cost_price=fe(221000, unit="IDR")), rec("B", cost_price=fe(11.25, unit="USD"))])
    assert (rows[0]["cost_price"], rows[0]["currency"]) == ("221000", "IDR")
    assert (rows[1]["cost_price"], rows[1]["currency"]) == ("11.25", "USD")


def test_unknown_currency_left_blank_with_warning():
    out, rows = export_rows([rec(cost_price=fe(5, unit="XYZ"))])
    assert rows[0]["currency"] == "" and rows[0]["cost_price"] == "5" and out["warnings"]


def test_negative_or_garbled_numbers_are_blank_with_warning():
    out, rows = export_rows([rec(stock=fe(-5), cost_price=fe("Rp 221.000", unit="IDR"))])
    assert rows[0]["stock"] == "" and rows[0]["cost_price"] == ""
    assert len(out["warnings"]) >= 2


@pytest.mark.parametrize("bad", ['=HYPERLINK("http://x","y")', "+1+1", "-2+3", "@SUM(A1)"])
def test_csv_injection_is_neutralised(bad):
    _, rows = export_rows([rec(name=fe(bad), brand=fe(bad), supplier_sku=None) | {"supplier_sku": bad}])
    assert rows[0]["name"] == "'" + bad
    assert rows[0]["brand"] == "'" + bad
    assert rows[0]["supplier_sku"] == "'" + bad


def test_leading_control_characters_are_trimmed_then_checked():
    _, rows = export_rows([rec(name=fe("\t=1+1"), brand=fe("\rcmd"))])
    assert rows[0]["name"] == "'=1+1"  # dipangkas dulu, lalu rumus dinetralkan
    assert rows[0]["brand"] == "cmd"  # tanpa karakter rumus, aman apa adanya
    assert safe_text("\tcmd") == "'\tcmd"  # perlindungan langsung bila teks tidak dipangkas


def test_safe_text_leaves_normal_text_alone():
    assert safe_text("Cotton 100%") == "Cotton 100%"
    assert safe_text("") == ""
    assert safe_text("A-1=2") == "A-1=2"


def test_special_characters_roundtrip_through_csv_and_utf8():
    tricky = 'Kemeja "Premium", Lengan\nPanjang é € ñ'
    out, rows = export_rows([rec(name=fe(tricky))])
    assert rows[0]["name"] == tricky
    assert out["csv"].encode("utf-8").decode("utf-8") == out["csv"]


def test_blockers_require_explicit_confirmation():
    bad = rec("BAD", gtin=fe(BAD_GTIN))
    with pytest.raises(ExportError) as e:
        build_export([rec("OK"), bad], "fashion")
    assert e.value.blocked == ["BAD"]
    out = build_export([rec("OK"), bad], "fashion", confirm_blockers=True)
    assert out["rows"] == 2 and out["exported_with_blockers"] == ["BAD"]


def test_blockers_use_fresh_validation_so_user_fix_unblocks():
    stale = rec("FIXED", gtin=fe(GOOD_GTIN, "user_provided"))
    stale["issues"] = [{"code": "GTIN_INVALID", "field": "gtin", "severity": "blocker"}]
    out = build_export([stale], "fashion")  # tanpa konfirmasi
    assert out["rows"] == 1 and out["exported_with_blockers"] == []


def test_unapproved_blocker_rows_do_not_block_export():
    out = build_export([rec("OK"), rec("BAD", approved=False, gtin=fe(BAD_GTIN))], "fashion")
    assert out["rows"] == 1


def _sample_records(name):
    r = parse_file(name, (SAMPLES_DIR / name).read_bytes())
    recs = [x for c in r["chunks"] for x in extract_rules(c, r["category"])]
    for x in recs:
        x["approved"] = True
    return recs, r["category"]


def test_sample_f1_export_content():
    recs, cat = _sample_records("rina_F1.xlsx")
    _, rows = export_rows(recs, cat)
    assert len(rows) == 6
    first = rows[0]
    assert first["supplier_sku"] == "LUM-CHI-BEI-28"
    assert first["gtin"] == ""  # barcode kosong di sumber tetap kosong
    assert (first["cost_price"], first["currency"], first["weight_g"]) == ("221000", "IDR", "490")
    assert first["sell_price"] == ""
    assert rows[3]["gtin"] == GOOD_GTIN


def test_sample_ea_requires_confirmation_for_invalid_gtin():
    recs, cat = _sample_records("andi_EA.xlsx")
    with pytest.raises(ExportError) as e:
        build_export(recs, cat)
    assert e.value.blocked == ["VOL-EB-02"]
    out, rows = export_rows(recs, cat, confirm=True)
    bad = next(r for r in rows if r["supplier_sku"] == "VOL-EB-02")
    assert bad["gtin"] == "2348588283593"  # nilai sumber, tidak dikoreksi
    assert out["exported_with_blockers"] == ["VOL-EB-02"]
