import io

import pytest
from openpyxl import Workbook

from app.parse import MAX_DATA_ROWS, ParseError, parse_file
from app.rules import extract_rules
from app.settings import SAMPLES_DIR


def _load(name: str, category: str | None = None):
    result = parse_file(name, (SAMPLES_DIR / name).read_bytes(), category)
    records = {r["supplier_sku"]: r for c in result["chunks"] for r in extract_rules(c, result["category"])}
    return result, records


def _xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HEADER = ["Kode SKU", "Nama Produk", "Merek", "Barcode (EAN)", "Harga Modal", "Stok"]


@pytest.mark.parametrize(
    ("name", "n_skus", "n_rows", "n_chunks", "category"),
    [
        ("rina_F1.xlsx", 6, 6, 2, "fashion"),
        ("andi_EA.xlsx", 11, 13, 3, "electronics"),
        ("sinta_S3.xlsx", 6, 7, 2, "beauty"),
        ("rina_F3.csv", 6, 6, 2, "fashion"),
    ],
)
def test_sample_counts_match_gold(name, n_skus, n_rows, n_chunks, category):
    result, records = _load(name)
    assert result["n_skus"] == n_skus
    assert result["n_rows"] == n_rows
    assert len(result["chunks"]) == n_chunks
    assert len(records) == n_skus
    assert result["category"] == category
    assert result["category_source"] == "inferred"
    assert all(len(c["skus"]) <= 5 for c in result["chunks"])


def test_user_category_overrides_inference():
    result, _ = _load("rina_F1.xlsx", category="beauty")
    assert result["category"] == "beauty"
    assert result["category_source"] == "user"


def test_duplicates_grouped_in_one_chunk_and_flagged():
    result, records = _load("andi_EA.xlsx")
    assert any("muncul lebih dari sekali" in w for w in result["warnings"])
    for sku in ("KYR-PW-02", "NEX-KB-01"):
        owners = [c for c in result["chunks"] if sku in c["skus"]]
        assert len(owners) == 1
        assert owners[0]["duplicate_counts"][sku] == 2
        assert records[sku]["source_row_count"] == 2


def test_extra_sheet_is_context_for_ai_only():
    result, _ = _load("andi_EA.xlsx")
    assert result["sheet"] == "Spec"
    assert all("SHEET: Pricelist" in c["text"] for c in result["chunks"])
    assert any("Pricelist" in w for w in result["warnings"])


def test_title_rows_and_footer_kept_as_context():
    result, _ = _load("sinta_S3.xlsx")
    assert result["chunks"][0]["header_row"] == 4
    assert "Periode: Oktober 2026" in result["chunks"][0]["text"]
    assert "Harga belum" in result["chunks"][-1]["text"]


def test_rules_never_fix_gtin_and_leave_missing_empty():
    _, f1 = _load("rina_F1.xlsx")
    assert f1["LUM-CHI-BEI-28"]["fields"]["gtin"]["value"] is None
    assert f1["LUM-CHI-BEI-28"]["fields"]["gtin"]["status"] == "missing"
    assert f1["OAK-SKI-BRO-XL"]["fields"]["gtin"]["value"] == "2410725410660"
    _, ea = _load("andi_EA.xlsx")
    gtin = ea["VOL-EB-02"]["fields"]["gtin"]
    assert gtin["value"] == "2348588283593"  # nilai tetap seperti di sumber
    assert gtin["status"] == "error"


def test_rules_key_fields():
    _, f1 = _load("rina_F1.xlsx")
    assert f1["LUM-CHI-BEI-28"]["fields"]["cost_price"]["value"] == 221000
    assert f1["LUM-CHI-BEI-28"]["fields"]["cost_price"]["unit"] == "IDR"
    _, f3 = _load("rina_F3.csv")
    w = f3["URB-SOC-GRE-5pcs"]["fields"]["weight_item"]
    assert (w["value"], w["unit"]) == (0.12, "kg")


def test_category_normalized_only_on_unique_taxonomy_match():
    _, ea = _load("andi_EA.xlsx")
    assert ea["NEX-KB-03"]["fields"]["category"]["value"].endswith("> Keyboards")
    assert ea["NEX-KB-03"]["fields"]["category"]["status"] == "normalized"
    other = ea["VOL-EB-03"]["fields"]["category"]  # "Others": tidak ditebak
    assert other["status"] == "needs_review"
    assert other["value"] == "Others"


def test_rejects_unsupported_format():
    with pytest.raises(ParseError, match="tidak didukung"):
        parse_file("data.pdf", b"%PDF")


def test_rejects_oversize():
    with pytest.raises(ParseError, match="2 MB"):
        parse_file("big.csv", b"a" * (2 * 1024 * 1024 + 1))


def test_unknown_header_falls_back_to_ai_only_chunks():
    data = b"Art.No,Desc,Mrk\nA-1,Kaos,Brand\nA-2,Celana,Brand\n"
    result = parse_file("x.csv", data)
    assert result["header_recognized"] is False
    assert result["n_rows"] == 2 and result["n_skus"] is None
    assert len(result["chunks"]) == 1
    assert "Art.No" in result["chunks"][0]["text"]
    assert any("tidak baku" in w for w in result["warnings"])
    assert extract_rules(result["chunks"][0], None) == []


def test_rejects_file_without_a_table():
    with pytest.raises(ParseError, match="minimal 3 kolom"):
        parse_file("x.csv", b"hanya,dua\n1,2\n")


def test_rejects_corrupt_xlsx():
    with pytest.raises(ParseError, match="tidak dapat dibaca"):
        parse_file("x.xlsx", b"bukan zip")


def test_rejects_too_many_rows():
    rows = [HEADER] + [[f"SKU-{i}", f"Produk {i}", "Merek", "", "1000", 1] for i in range(MAX_DATA_ROWS + 1)]
    with pytest.raises(ParseError, match="batas demo"):
        parse_file("many.xlsx", _xlsx(rows))


def test_accepts_exactly_max_rows():
    rows = [HEADER] + [[f"SKU-{i}", f"Produk {i}", "Merek", "", "1000", 1] for i in range(MAX_DATA_ROWS)]
    result = parse_file("ok.xlsx", _xlsx(rows))
    assert result["n_skus"] == MAX_DATA_ROWS
    assert len(result["chunks"]) == MAX_DATA_ROWS // 5


def test_semicolon_delimited_csv():
    data = "Kode SKU;Nama Produk;Merek;Stok\nA-1;Kaos;Brand;5\n".encode()
    result = parse_file("semi.csv", data)
    assert result["n_skus"] == 1


def test_rows_without_sku_are_reported_not_silently_dropped():
    rows = [HEADER, ["A-1", "Kaos", "B", "", "1000", 1], ["", "Celana", "B", "", "2000", 2]]
    result = parse_file("nosku.xlsx", _xlsx(rows))
    assert result["n_skus"] == 1
    assert any("tanpa kode SKU" in w for w in result["warnings"])
