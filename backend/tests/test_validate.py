import copy

import pytest

from app.parse import parse_file
from app.rules import extract_rules
from app.settings import SAMPLES_DIR
from app.validate import gtin_valid, named_colors, validate_records

GOOD_GTIN = "2410725410660"  # check digit valid
BAD_GTIN = "2348588283593"  # check digit salah


def fe(value, status="extracted", **kw):
    return {"value": value, "status": status, "evidence": value, "confidence": None, **kw}


def fashion(**over):
    fields = {
        "name": fe("Celana Chino Lumora"), "brand": fe("Lumora"),
        "category": fe("Apparel > Bottoms > Pants", "normalized"),
        "cost_price": fe(221000, unit="IDR"), "stock": fe(70), "material": fe("Cotton 100%"), "color": fe("pink"),
        "size": fe("30"), "weight_item": fe(0.5, unit="kg"), "gtin": fe(GOOD_GTIN),
    }
    fields.update(over.pop("fields", {}))
    return {"supplier_sku": over.pop("sku", "A-1"), "product_group": "g", "fields": fields, "issues": [], **over}


def codes(issues, sku=None):
    return {(i["code"], i["field"]) for i in issues if sku is None or i["sku"] == sku}


def run(rec, category="fashion"):
    recs, issues = validate_records([rec], category)
    return recs[0], issues


def test_clean_record_has_no_issues():
    rec, issues = run(fashion())
    assert issues == [] and rec["issues"] == []


def test_gtin_check_digit_function():
    assert gtin_valid(GOOD_GTIN) and not gtin_valid(BAD_GTIN)
    assert not gtin_valid("123") and not gtin_valid("24107254106AB")


def test_gtin_missing_is_warning_and_value_stays_empty():
    rec, issues = run(fashion(fields={"gtin": fe(None, "missing")}))
    assert codes(issues) == {("GTIN_MISSING", "gtin")}
    assert issues[0]["severity"] == "warning"
    assert rec["fields"]["gtin"]["value"] is None


def test_gtin_invalid_is_blocker_and_never_corrected():
    rec, issues = run(fashion(fields={"gtin": fe(BAD_GTIN)}))
    assert codes(issues) == {("GTIN_INVALID", "gtin")}
    assert issues[0]["severity"] == "blocker"
    assert rec["fields"]["gtin"]["value"] == BAD_GTIN
    assert rec["fields"]["gtin"]["status"] == "error"


def test_gtin_not_reported_twice_for_categories_requiring_it():
    rec = {"supplier_sku": "E-1", "fields": {"name": fe("x"), "brand": fe("b")}, "issues": []}
    _, issues = validate_records([rec], "electronics")
    gtin_codes = [i["code"] for i in issues if i["field"] == "gtin"]
    assert gtin_codes == ["GTIN_MISSING"]


def test_required_attr_missing_per_category():
    _, issues = run(fashion(fields={"color": fe(None, "missing")}))
    assert codes(issues) == {("REQUIRED_ATTR_MISSING", "color")}
    _, issues = run(fashion(fields={"color": fe("")}))
    assert ("REQUIRED_ATTR_MISSING", "color") in codes(issues)
    # field wajib hilang total dari rekaman juga ditandai dan ditambahkan sebagai missing
    rec = fashion()
    del rec["fields"]["material"]
    out, issues = run(rec)
    assert ("REQUIRED_ATTR_MISSING", "material") in codes(issues)
    assert out["fields"]["material"]["status"] == "missing"


def test_no_category_only_checks_universal_fields():
    rec = fashion(fields={"color": fe(None, "missing")})
    _, issues = run(rec, category=None)
    assert issues == []


def test_conflict_status_is_not_reported_as_missing():
    _, issues = run(fashion(fields={"weight_item": fe(None, "conflict")}))
    assert ("REQUIRED_ATTR_MISSING", "weight_item") not in codes(issues)


def test_duplicate_sku_via_source_row_count_and_repeated_records():
    _, issues = run(fashion(source_row_count=2))
    assert codes(issues) == {("DUPLICATE_SKU", "supplier_sku")}
    recs, issues = validate_records([fashion(), fashion()], "fashion")
    assert [i["code"] for i in issues] == ["DUPLICATE_SKU", "DUPLICATE_SKU"]
    assert len(recs) == 2


@pytest.mark.parametrize(
    "text",
    [
        "Cures acne in 3 days.",
        "Terbukti klinis menyembuhkan eksim dan alergi kulit.",
        "Menghilangkan jerawat dalam 3 hari.",
        "100% safe with no side effects, treats skin infections.",
        "Clinically proven to treat eczema and skin allergies.",
    ],
)
def test_claims_are_blockers(text):
    _, issues = run(fashion(fields={"description": fe(text)}))
    assert codes(issues) == {("CLAIM_UNSUBSTANTIATED", "description")}
    assert issues[0]["severity"] == "blocker"


@pytest.mark.parametrize(
    "text",
    [
        "Celana Chino Lumora berbahan Cotton 100%. Nyaman dipakai untuk aktivitas sehari-hari.",
        "Gentle formula for daily use.",
        "Sabun Mandi Cair, formula lembut untuk pemakaian harian.",
    ],
)
def test_ordinary_descriptions_are_clean(text):
    _, issues = run(fashion(fields={"description": fe(text)}))
    assert issues == []


@pytest.mark.parametrize("raw", ["Others", "Lainnya", "lain-lain"])
def test_generic_category_is_ambiguous(raw):
    _, issues = run(fashion(fields={"category": fe(raw, "needs_review")}))
    assert codes(issues) == {("CATEGORY_AMBIGUOUS", "category")}


def test_generic_product_name_with_a_category_is_ambiguous():
    rec = fashion(fields={"name": fe("Special Promo Item"), "category": fe("Keyboards", "needs_review")})
    _, issues = run(rec)
    assert ("CATEGORY_AMBIGUOUS", "category") in codes(issues)


def test_specific_category_is_not_ambiguous():
    cat = fe("Electronics > Computer Accessories > Keyboards", "normalized")
    cat["evidence"] = "Keyboards"
    _, issues = run(fashion(fields={"category": cat}))
    assert issues == []


def test_named_colors_handles_multiword_and_bilingual():
    assert named_colors("Rok Merah Muda") == {"pink"}
    assert named_colors("Celana Chino Lumora Wear Blue") == {"blue"}
    assert named_colors("Kemeja covered Redwood") == set()  # bukan kata warna utuh


def test_title_color_contradiction():
    _, issues = run(fashion(fields={"name": fe("Celana Chino Lumora Wear Blue")}))
    assert codes(issues) == {("TITLE_ATTR_CONTRADICTION", "color")}
    _, issues = run(fashion(fields={"name": fe("Kaos Kaki Marun Abu-abu 5 pcs"), "color": fe("grey")}))
    assert codes(issues) == {("TITLE_ATTR_CONTRADICTION", "color")}


def test_title_color_consistent_is_clean():
    _, issues = run(fashion(fields={"name": fe("Rok Merah Muda Plisket"), "color": fe("Pink")}))
    assert issues == []
    _, issues = run(fashion(fields={"name": fe("Celana Chino Lumora")}))
    assert issues == []


def test_title_capacity_and_length_contradiction():
    rec = fashion(fields={"name": fe("MicroSD Nexio 4 TB"), "capacity": fe(512, unit="GB")})
    assert ("TITLE_ATTR_CONTRADICTION", "capacity") in codes(run(rec)[1])
    ok = fashion(fields={"name": fe("MicroSD Nexio 1 TB"), "capacity": fe(1000, unit="GB")})
    assert ("TITLE_ATTR_CONTRADICTION", "capacity") not in codes(run(ok)[1])
    rec = fashion(fields={"name": fe("Kabel HDMI 5 m"), "length": fe(2, unit="m")})
    assert ("TITLE_ATTR_CONTRADICTION", "length") in codes(run(rec)[1])
    ok = fashion(fields={"name": fe("Kabel HDMI 2 m 10 ml"), "length": fe(2, unit="m")})
    assert ("TITLE_ATTR_CONTRADICTION", "length") not in codes(run(ok)[1])


def test_inferred_critical_values_become_needs_review():
    rec, _ = run(fashion(fields={"brand": fe("Lumora", "inferred"), "color": fe("pink", "inferred")}))
    assert rec["fields"]["brand"]["status"] == "needs_review"
    assert rec["fields"]["color"]["status"] == "inferred"  # bukan field kritis


def test_other_issues_kept_and_stale_deterministic_ones_dropped():
    rec = fashion(issues=[
        {"code": "WEIGHT_SCOPE_AMBIGUOUS", "field": "weight_item"},
        {"code": "GTIN_INVALID", "field": "gtin"},  # AI salah klaim; GTIN sebenarnya valid
    ])
    out, issues = run(rec)
    assert codes(issues) == {("WEIGHT_SCOPE_AMBIGUOUS", "weight_item")}
    assert out["issues"] == [{"code": "WEIGHT_SCOPE_AMBIGUOUS", "field": "weight_item", "severity": "warning"}]


def test_idempotent_and_input_not_mutated():
    rec = fashion(fields={"gtin": fe(BAD_GTIN), "description": fe("Cures acne in 3 days.")})
    before = copy.deepcopy(rec)
    once, i1 = validate_records([rec], "fashion")
    assert rec == before
    twice, i2 = validate_records(once, "fashion")
    assert once == twice and i1 == i2


def test_every_issue_has_sku_reason_and_action():
    rec = fashion(fields={"gtin": fe(BAD_GTIN)})
    _, issues = run(rec)
    assert {"sku", "code", "field", "severity", "reason", "action"} <= issues[0].keys()


def _sample(name):
    r = parse_file(name, (SAMPLES_DIR / name).read_bytes())
    recs = [x for c in r["chunks"] for x in extract_rules(c, r["category"])]
    return validate_records(recs, r["category"])


def test_sample_f1_flags_missing_gtin_and_title_contradiction():
    _, issues = _sample("rina_F1.xlsx")
    assert ("GTIN_MISSING", "gtin") in codes(issues, "LUM-CHI-BEI-28")
    assert ("TITLE_ATTR_CONTRADICTION", "color") in codes(issues, "LUM-CHI-PIN-34")


def test_sample_ea_flags_invalid_gtin_duplicates_and_ambiguous_category():
    _, issues = _sample("andi_EA.xlsx")
    assert ("GTIN_INVALID", "gtin") in codes(issues, "VOL-EB-02")
    assert ("DUPLICATE_SKU", "supplier_sku") in codes(issues, "NEX-KB-01")
    assert ("CATEGORY_AMBIGUOUS", "category") in codes(issues, "VOL-EB-03")


def test_sample_s3_flags_claim_as_blocker():
    _, issues = _sample("sinta_S3.xlsx")
    claim = [i for i in issues if i["sku"] == "BAY-FAC-02" and i["code"] == "CLAIM_UNSUBSTANTIATED"]
    assert claim and claim[0]["severity"] == "blocker"
