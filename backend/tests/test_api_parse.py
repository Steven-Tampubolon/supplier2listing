import io

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app
from app.settings import SAMPLES_DIR

client = TestClient(app)


def test_samples_list():
    body = client.get("/api/samples").json()
    names = {s["name"] for s in body["samples"]}
    assert names == {"rina_F1.xlsx", "andi_EA.xlsx", "sinta_S3.xlsx", "rina_F3.csv"}
    assert all("rules" in s["modes"] for s in body["samples"])
    assert all((SAMPLES_DIR / n).exists() for n in names)


def test_parse_sample():
    r = client.post("/api/parse", data={"sample": "andi_EA.xlsx"})
    assert r.status_code == 200
    body = r.json()
    assert body["n_skus"] == 11
    assert len(body["rules_records"]) == 11
    assert {"chunks", "rules_records", "warnings"} <= body.keys()


def test_parse_upload_matches_sample():
    data = (SAMPLES_DIR / "rina_F3.csv").read_bytes()
    r = client.post("/api/parse", files={"file": ("rina_F3.csv", data, "text/csv")})
    assert r.status_code == 200
    assert r.json()["n_skus"] == 6


def test_parse_unknown_sample_is_404_and_blocks_path_traversal():
    assert client.post("/api/parse", data={"sample": "nope.xlsx"}).status_code == 404
    assert client.post("/api/parse", data={"sample": "../main.py"}).status_code == 404


def test_parse_without_input_is_400():
    r = client.post("/api/parse")
    assert r.status_code == 400
    assert "Pilih berkas" in r.json()["detail"]


def test_parse_wrong_format_is_friendly_400():
    r = client.post("/api/parse", files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 400
    assert "tidak didukung" in r.json()["detail"]


def test_parse_oversize_is_413():
    big = b"a" * (2 * 1024 * 1024 + 10)
    r = client.post("/api/parse", files={"file": ("big.csv", big, "text/csv")})
    assert r.status_code == 413
    assert "2 MB" in r.json()["detail"]


def test_parse_bad_category_is_400():
    wb = Workbook()
    ws = wb.active
    ws.append(["Kode SKU", "Nama Produk", "Merek"])
    ws.append(["A-1", "Kaos", "B"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(
        "/api/parse",
        files={"file": ("a.xlsx", buf.getvalue(), "application/octet-stream")},
        data={"category": "mobil"},
    )
    assert r.status_code == 400
    assert "Kategori tidak dikenal" in r.json()["detail"]
