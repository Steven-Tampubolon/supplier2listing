import asyncio
import json

import httpx
import pytest

from app import cache, settings
from app.extract import run_extract, sanitize_records
from tests.helpers import ai_records, ai_text, chat_body, chunk_of, make_llm, ok_handler, sequence_handler


def go(chunk, category, mode, llm, **kw):
    return asyncio.run(run_extract(chunk, category, mode, llm, **kw))


def by_sku(out):
    return {r["supplier_sku"]: r for r in out["records"]}


def no_http(request):
    raise AssertionError("tidak boleh memanggil jaringan pada mode ini")


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_CACHE_DIR", tmp_path)
    return tmp_path


# ---------- sanitasi ----------
def test_sanitize_invalid_status_becomes_needs_review_not_extracted():
    obj = {"records": [{"supplier_sku": "A", "fields": {
        "color": {"value": "red", "status": "bogus"}, "size": {"value": None, "status": "bogus"},
        "brand": {"value": None, "status": "extracted"}, "name": "teks polos",
    }}]}
    recs, _ = sanitize_records(obj)
    f = recs[0]["fields"]
    assert f["color"]["status"] == "needs_review" and f["size"]["status"] == "missing"
    assert f["brand"]["status"] == "missing"  # null + extracted tidak konsisten
    assert f["name"]["status"] == "needs_review" and f["name"]["value"] == "teks polos"


def test_sanitize_drops_description_clamps_confidence_and_skips_bad_records():
    obj = {"records": [
        {"supplier_sku": "A", "fields": {"description": {"value": "x", "status": "extracted"},
                                         "brand": {"value": "b", "status": "extracted", "confidence": 7}}},
        {"supplier_sku": "a", "fields": {}},  # SKU ganda (tanpa memedulikan huruf besar)
        {"supplier_sku": "", "fields": {}}, {"fields": {}}, "bukan objek",
    ]}
    recs, problems = sanitize_records(obj)
    assert [r["supplier_sku"] for r in recs] == ["A"]
    assert "description" not in recs[0]["fields"] and recs[0]["fields"]["brand"]["confidence"] == 1.0
    assert len(problems) == 4


def test_sanitize_rejects_wrong_top_level():
    assert sanitize_records([1, 2])[0] is None and sanitize_records({"records": "x"})[0] is None


# ---------- mode rules ----------
def test_rules_mode_keeps_plain_statuses_and_never_calls_network():
    chunk, cat = chunk_of("rina_F1.xlsx")
    out = go(chunk, cat, "rules", make_llm(no_http))
    assert out["source"] == "rules" and out["fallback_reason"] is None
    assert by_sku(out)["OAK-SKI-BRO-XL"]["fields"]["gtin"]["status"] == "extracted"


# ---------- mode live ----------
def test_live_success_is_ai_with_usage_and_validation():
    chunk, cat = chunk_of("rina_F1.xlsx")
    llm = make_llm(ok_handler(ai_text(chunk, cat), {"x-ratelimit-remaining-user-daily-usd": "0.77"}))
    out = go(chunk, cat, "live", llm)
    assert out["source"] == "ai" and out["fallback_reason"] is None
    assert len(out["records"]) == len(chunk["skus"]) and out["usage"]["completion_tokens"] == 50
    assert out["_remaining_usd"] == 0.77 and out["attempts"][0]["status"] == 200
    assert {"code": "GTIN_MISSING", "field": "gtin", "severity": "warning"} in by_sku(out)["LUM-CHI-BEI-28"]["issues"]


def test_live_accepts_code_fenced_json():
    chunk, cat = chunk_of("rina_F1.xlsx")
    out = go(chunk, cat, "live", make_llm(ok_handler("```json\n" + ai_text(chunk, cat) + "\n```")))
    assert out["source"] == "ai"


def test_live_description_comes_from_source_so_claims_are_caught():
    chunk, cat = chunk_of("sinta_S3.xlsx")
    out = go(chunk, cat, "live", make_llm(ok_handler(ai_text(chunk, cat))))
    codes = {(i["code"], i["field"]) for i in by_sku(out)["BAY-FAC-02"]["issues"]}
    assert ("CLAIM_UNSUBSTANTIATED", "description") in codes
    assert by_sku(out)["BAY-FAC-02"]["source_row_count"] == 2
    assert ("DUPLICATE_SKU", "supplier_sku") in codes


def test_live_inferred_critical_value_is_downgraded():
    chunk, cat = chunk_of("rina_F1.xlsx")

    def mutate(recs):
        recs[3]["fields"]["brand"]["status"] = "inferred"

    out = go(chunk, cat, "live", make_llm(ok_handler(ai_text(chunk, cat, mutate))))
    assert by_sku(out)["OAK-SKI-BRO-XL"]["fields"]["brand"]["status"] == "needs_review"


def test_live_invalid_gtin_is_never_corrected():
    chunk, cat = chunk_of("andi_EA.xlsx", 0)  # memuat VOL-EB-02 dengan GTIN salah
    out = go(chunk, cat, "live", make_llm(ok_handler(ai_text(chunk, cat))))
    gtin = by_sku(out)["VOL-EB-02"]["fields"]["gtin"]
    assert gtin["value"] == "2348588283593" and gtin["status"] == "error"


def test_evidence_not_in_source_is_downgraded():
    chunk, cat = chunk_of("rina_F1.xlsx")

    def mutate(recs):
        recs[3]["fields"]["gtin"]["evidence"] = "9999999999999"  # tidak ada di sumber
        recs[4]["fields"]["brand"]["evidence"] = None  # nilai tanpa kutipan

    out = go(chunk, cat, "live", make_llm(ok_handler(ai_text(chunk, cat, mutate))))
    f3, f4 = by_sku(out)["OAK-SKI-BRO-XL"]["fields"], by_sku(out)["OAK-SKI-OLI-M"]["fields"]
    assert f3["gtin"]["status"] == "needs_review" and "Bukti" in f3["gtin"]["note"]
    assert f4["brand"]["status"] == "needs_review"
    assert out["evidence_downgraded"] >= 2


def test_evidence_matching_ignores_case_and_whitespace():
    chunk, cat = chunk_of("rina_F1.xlsx")

    def mutate(recs):
        ev = recs[0]["fields"]["name"]["evidence"]
        recs[0]["fields"]["name"]["evidence"] = "  " + ev.upper().replace(" ", "   ") + " "

    out = go(chunk, cat, "live", make_llm(ok_handler(ai_text(chunk, cat, mutate))))
    assert by_sku(out)["LUM-CHI-BEI-28"]["fields"]["name"]["status"] == "extracted"


def test_hallucinated_sku_is_dropped():
    chunk, cat = chunk_of("rina_F1.xlsx")

    def mutate(recs):
        fake = json.loads(json.dumps(recs[0]))
        fake["supplier_sku"] = "FAKE-999"
        recs.append(fake)

    out = go(chunk, cat, "live", make_llm(ok_handler(ai_text(chunk, cat, mutate))))
    assert "FAKE-999" not in by_sku(out) and out["dropped_skus"] == ["FAKE-999"]


def test_missing_sku_is_filled_from_rules_and_flagged():
    chunk, cat = chunk_of("rina_F1.xlsx")
    llm = make_llm(ok_handler(ai_text(chunk, cat, lambda recs: recs.pop(1))))
    out = go(chunk, cat, "live", llm)
    assert out["rules_filled"] == ["LUM-CHI-PIN-30"] and len(out["records"]) == len(chunk["skus"])
    assert by_sku(out)["LUM-CHI-PIN-30"]["fields"]["name"]["status"] == "needs_review"


def test_invalid_json_falls_back_to_rules_without_retrying():
    chunk, cat = chunk_of("rina_F1.xlsx")
    h = ok_handler("ini bukan json sama sekali")
    out = go(chunk, cat, "live", make_llm(h))
    assert out["source"] == "rules" and out["fallback_reason"] == "json_invalid" and h.calls["n"] == 1
    assert len(out["records"]) == 5


def test_wrong_shape_json_falls_back():
    chunk, cat = chunk_of("rina_F1.xlsx")
    out = go(chunk, cat, "live", make_llm(ok_handler('{"records": []}')))
    assert out["fallback_reason"] == "json_invalid" and out["source"] == "rules"


def test_fallback_marks_valued_fields_needs_review_but_keeps_missing_missing():
    chunk, cat = chunk_of("rina_F1.xlsx")
    out = go(chunk, cat, "live", make_llm(ok_handler("bukan json")))
    f = by_sku(out)["LUM-CHI-BEI-28"]["fields"]
    assert f["name"]["status"] == "needs_review" and "AI tidak tersedia" in f["name"]["note"]
    assert f["gtin"]["status"] == "missing"  # tetap terlihat oleh validator
    assert any(i["code"] == "GTIN_MISSING" for i in by_sku(out)["LUM-CHI-BEI-28"]["issues"])


@pytest.mark.parametrize(
    ("responses", "reason"),
    [
        ([httpx.Response(504, text="gt")], "upstream_error"),
        ([httpx.Response(429, headers={"Retry-After": "1"})], "rate_limited"),
        ([httpx.ReadTimeout("x")], "timeout"),
        ([httpx.Response(401, json={})], "auth"),
        ([httpx.Response(200, json=chat_body("", finish="length"))], "empty_response"),
    ],
)
def test_http_failures_fall_back_to_rules(responses, reason):
    chunk, cat = chunk_of("rina_F1.xlsx")
    out = go(chunk, cat, "live", make_llm(sequence_handler(responses)))
    assert out["source"] == "rules" and out["fallback_reason"] == reason and len(out["records"]) == 5
    assert all(r["fields"]["name"]["status"] == "needs_review" for r in out["records"])


def test_fallback_on_unrecognised_header_returns_no_records_with_explanation():
    csv = b"Art.No,Desc,Mrk\nA-1,Kaos,Brand\nA-2,Celana,Brand\n"
    from app.parse import parse_file

    chunk = parse_file("x.csv", csv)["chunks"][0]
    out = go(chunk, None, "live", make_llm(sequence_handler([httpx.Response(504)])))
    assert out["records"] == [] and any("judul kolom" in n for n in out["notes"])


# ---------- mode replay dan perekaman ----------
def test_replay_hit_uses_recorded_output_without_network(cache_dir):
    chunk, cat = chunk_of("rina_F1.xlsx")
    llm = make_llm(no_http)
    key = cache.chunk_key(llm.cfg.model, "t1_v0", chunk["text"])
    cache.save(key, {"records": ai_records(chunk, cat), "recorded_at": "2026-10-18"})
    out = go(chunk, cat, "replay", llm)
    assert out["source"] == "replay" and out["recorded_at"] == "2026-10-18" and len(out["records"]) == 5


def test_replay_miss_falls_back_to_flagged_rules(cache_dir):
    chunk, cat = chunk_of("rina_F1.xlsx")
    out = go(chunk, cat, "replay", make_llm(no_http))
    assert out["source"] == "rules" and out["fallback_reason"] == "replay_miss"
    assert out["records"][0]["fields"]["name"]["status"] == "needs_review"


def test_replay_changes_when_chunk_text_changes(cache_dir):
    chunk, cat = chunk_of("rina_F1.xlsx")
    llm = make_llm(no_http)
    cache.save(cache.chunk_key(llm.cfg.model, "t1_v0", chunk["text"]), {"records": ai_records(chunk, cat)})
    edited = {**chunk, "text": chunk["text"] + "\n"}
    assert go(edited, cat, "replay", llm)["fallback_reason"] == "replay_miss"


def test_recorded_live_run_can_be_replayed(cache_dir):
    chunk, cat = chunk_of("rina_F1.xlsx")
    live = go(chunk, cat, "live", make_llm(ok_handler(ai_text(chunk, cat))), save_cache=True)
    replay = go(chunk, cat, "replay", make_llm(no_http))
    assert live["source"] == "ai" and replay["source"] == "replay"
    assert {r["supplier_sku"] for r in live["records"]} == {r["supplier_sku"] for r in replay["records"]}
    assert replay["recorded_at"]
