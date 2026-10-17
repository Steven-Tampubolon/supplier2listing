import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import settings
from app.budget import Budget
from app.main import app, get_budget, get_llm
from tests.helpers import ai_text, chunk_of, make_llm, ok_handler, sequence_handler

client = TestClient(app)


def use(llm, budget=None):
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_budget] = lambda: budget or Budget()


def body(mode, name="rina_F1.xlsx", idx=0):
    chunk, cat = chunk_of(name, idx)
    return {"chunk": chunk, "category": cat, "mode": mode}, chunk, cat


def test_live_success_and_budget_accounting():
    req, chunk, cat = body("live")
    budget = Budget(max_day=10)
    use(make_llm(ok_handler(ai_text(chunk, cat), {"x-ratelimit-remaining-user-daily-usd": "0.55"})), budget)
    r = client.post("/api/extract", json=req)
    assert r.status_code == 200
    out = r.json()
    assert out["source"] == "ai" and len(out["records"]) == 5 and out["latency_ms"] >= 0
    assert "_remaining_usd" not in out and budget.used_today == 1 and budget.remaining_usd == 0.55


def test_live_without_key_is_503():
    req, _, _ = body("live")
    r = client.post("/api/extract", json=req)
    assert r.status_code == 503 and "Replay" in r.json()["detail"]


def test_daily_limit_returns_friendly_429():
    req, chunk, cat = body("live")
    use(make_llm(ok_handler(ai_text(chunk, cat))), Budget(max_day=1))
    assert client.post("/api/extract", json=req).status_code == 200
    r = client.post("/api/extract", json=req)
    j = r.json()
    assert r.status_code == 429 and j["reason"] == "day" and j["suggestion"] == "replay" and "Replay" in j["detail"]


def test_per_ip_limit_is_per_client():
    req, chunk, cat = body("live")
    use(make_llm(ok_handler(ai_text(chunk, cat))), Budget(max_day=99, max_ip_hour=1))
    a = {"x-forwarded-for": "10.0.0.1"}
    assert client.post("/api/extract", json=req, headers=a).status_code == 200
    r = client.post("/api/extract", json=req, headers=a)
    assert r.status_code == 429 and r.json()["reason"] == "ip" and int(r.headers["retry-after"]) > 0
    assert client.post("/api/extract", json=req, headers={"x-forwarded-for": "10.0.0.2"}).status_code == 200


def test_replay_and_rules_do_not_consume_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_CACHE_DIR", tmp_path)
    budget = Budget(max_day=1)
    use(make_llm(lambda r: (_ for _ in ()).throw(AssertionError("jaringan"))), budget)
    for mode, source in (("rules", "rules"), ("replay", "rules")):  # replay tanpa cache jatuh ke aturan
        req, _, _ = body(mode)
        r = client.post("/api/extract", json=req)
        assert r.status_code == 200 and r.json()["source"] == source
    assert budget.used_today == 0


def test_upstream_failure_still_returns_200_with_rules_fallback():
    req, _, _ = body("live")
    use(make_llm(sequence_handler([httpx.Response(504)])))
    r = client.post("/api/extract", json=req)
    out = r.json()
    assert r.status_code == 200 and out["source"] == "rules" and out["fallback_reason"] == "upstream_error"


@pytest.mark.parametrize(
    "chunk",
    [{}, {"text": ""}, {"text": "x" * 40_001}, {"text": "ok", "skus": "bukan daftar"}, {"text": "ok", "skus": [1]}],
)
def test_invalid_chunks_are_422(chunk):
    use(make_llm(ok_handler("{}")))
    assert client.post("/api/extract", json={"chunk": chunk, "mode": "rules"}).status_code == 422


_BASE = {"text": "isi", "skus": ["A"], "header_row": 1, "columns": {"sku": 0}}


@pytest.mark.parametrize(
    ("grid_part", "expected"),
    [
        ({"grid": {}}, 200),  # tidak ada baris: hasil kosong, bukan galat
        ({"grid": {"header_row": 1, "rows": "x"}}, 422),
        ({"grid": {"header_row": 1, "rows": {"1": "bukan daftar", "2": 5}}}, 422),
        ({"grid": {"header_row": 1, "rows": {"abc": ["a"]}}}, 422),
        ({"grid": {"header_row": 1, "rows": {"1": [1, 2, 3], "2": [None, {}, []]}}}, 422),
        ({"columns": [1, 2], "grid": {"header_row": 1, "rows": {}}}, 200),
        ({"columns": {"sku": 99, "category": 98}, "grid": {"header_row": 1, "rows": {"1": ["a"], "2": ["b"]}}}, 200),
    ],
)
def test_malformed_chunks_never_cause_a_500(grid_part, expected):
    use(make_llm(ok_handler("{}")))
    r = client.post("/api/extract", json={"chunk": {**_BASE, **grid_part}, "mode": "rules"})
    assert r.status_code == expected


def test_unknown_mode_and_category_are_422():
    chunk, _ = chunk_of("rina_F1.xlsx")
    assert client.post("/api/extract", json={"chunk": chunk, "mode": "nuklir"}).status_code == 422
    assert client.post("/api/extract", json={"chunk": chunk, "category": "mobil"}).status_code == 422


def test_health_reports_real_budget(monkeypatch):
    assert client.get("/api/health").json()["daily_ai_budget_left"] == 0  # tanpa kunci
    monkeypatch.setenv("RADEON_API_KEY", "rc-test")
    monkeypatch.setattr(main, "_BUDGET", Budget(max_day=7))
    h = client.get("/api/health").json()
    assert h["ai_enabled"] is True and h["daily_ai_budget_left"] == 7


def test_samples_offer_live_only_with_a_key(monkeypatch):
    assert all("live" not in s["modes"] for s in client.get("/api/samples").json()["samples"])
    monkeypatch.setenv("RADEON_API_KEY", "rc-test")
    assert all("live" in s["modes"] for s in client.get("/api/samples").json()["samples"])
