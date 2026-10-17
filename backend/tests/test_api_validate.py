from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _parsed(sample):
    return client.post("/api/parse", data={"sample": sample}).json()


def test_validate_roundtrip_on_parse_output():
    p = _parsed("andi_EA.xlsx")
    r = client.post("/api/validate", json={"records": p["rules_records"], "category": p["category"]})
    assert r.status_code == 200
    body = r.json()
    assert len(body["records"]) == 11
    assert body["summary"]["blockers"] >= 1
    assert body["summary"]["by_code"]["GTIN_INVALID"] >= 1
    assert all(i["severity"] in {"blocker", "warning"} for i in body["issues"])


def test_validate_rejects_unknown_category():
    assert client.post("/api/validate", json={"records": [], "category": "mobil"}).status_code == 422


def test_validate_rejects_too_many_records():
    recs = [{"supplier_sku": f"S{i}", "fields": {}} for i in range(401)]
    assert client.post("/api/validate", json={"records": recs, "category": None}).status_code == 422


def test_validate_empty_is_ok():
    r = client.post("/api/validate", json={"records": [], "category": None})
    assert r.status_code == 200 and r.json()["summary"]["blockers"] == 0
