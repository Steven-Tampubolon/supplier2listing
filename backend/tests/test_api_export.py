import csv
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _approved(sample):
    p = client.post("/api/parse", data={"sample": sample}).json()
    for r in p["rules_records"]:
        r["approved"] = True
    return p["rules_records"], p["category"]


def test_export_roundtrip_from_parse():
    recs, cat = _approved("rina_F1.xlsx")
    r = client.post("/api/export", json={"records": recs, "category": cat})
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == 6 and body["filename"].endswith(".csv")
    rows = list(csv.DictReader(io.StringIO(body["csv"], newline="")))
    assert rows[0]["supplier_sku"] == "LUM-CHI-BEI-28" and rows[0]["sell_price"] == ""


def test_export_blocker_needs_confirmation_then_succeeds():
    recs, cat = _approved("andi_EA.xlsx")
    r = client.post("/api/export", json={"records": recs, "category": cat})
    assert r.status_code == 409
    assert r.json()["blocked_skus"] == ["VOL-EB-02"] and "blocker" in r.json()["detail"]
    r2 = client.post("/api/export", json={"records": recs, "category": cat, "confirm_blockers": True})
    assert r2.status_code == 200 and r2.json()["rows"] == 11


def test_export_without_approved_rows_is_400():
    recs, cat = _approved("rina_F1.xlsx")
    for x in recs:
        x["approved"] = False
    r = client.post("/api/export", json={"records": recs, "category": cat})
    assert r.status_code == 400 and "Tidak ada baris" in r.json()["detail"]


def test_export_validates_request_shape():
    assert client.post("/api/export", json={"records": [], "category": "mobil"}).status_code == 422
