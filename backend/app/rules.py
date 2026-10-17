"""Parser aturan (tanpa AI): membungkus baseline.predict untuk satu potongan dari app.parse."""
from __future__ import annotations

import json
from functools import lru_cache

from app import settings
from app.harness.baseline import predict


@lru_cache(maxsize=1)
def _taxonomy_index() -> dict[str, str]:
    """Segmen terakhir (casefold) -> jalur lengkap; hanya yang unik."""
    paths = json.loads((settings.CONFIG_DIR / "taxonomy.json").read_text(encoding="utf-8"))
    seen: dict[str, list[str]] = {}
    for p in paths:
        seen.setdefault(p.split(" > ")[-1].casefold(), []).append(p)
    return {k: v[0] for k, v in seen.items() if len(v) == 1}


def _raw_categories(chunk: dict) -> dict[str, str]:
    """sku -> nilai kategori mentah dari baris pertama SKU itu di potongan."""
    col = chunk["columns"]
    if "category" not in col or "sku" not in col:
        return {}
    out: dict[str, str] = {}
    rows = {int(k): v for k, v in chunk["grid"]["rows"].items()}
    for rn in sorted(rows):
        if rn <= chunk["header_row"]:
            continue
        c = rows[rn]
        sku = c[col["sku"]].strip() if col["sku"] < len(c) else ""
        if sku and sku not in out:
            out[sku] = c[col["category"]].strip() if col["category"] < len(c) else ""
    return out


def _apply_category(records: list[dict], chunk: dict) -> None:
    """Padankan kategori mentah ke taksonomi hanya bila persis satu kecocokan; jika tidak, minta review."""
    raw_by_sku = _raw_categories(chunk)
    index = _taxonomy_index()
    for rec in records:
        raw = raw_by_sku.get(rec["supplier_sku"], "")
        if not raw:
            continue  # tetap missing
        path = index.get(raw.casefold())
        if path:
            rec["fields"]["category"] = {
                "value": path, "status": "normalized", "evidence": raw, "confidence": None,
            }
        else:
            rec["fields"]["category"] = {
                "value": raw, "status": "needs_review", "evidence": raw, "confidence": None,
            }


def _raw_column(chunk: dict, key: str) -> dict[str, str]:
    """sku -> nilai sel kolom `key` dari baris pertama SKU itu."""
    col = chunk["columns"]
    if key not in col or "sku" not in col:
        return {}
    out: dict[str, str] = {}
    rows = {int(k): v for k, v in chunk["grid"]["rows"].items()}
    for rn in sorted(rows):
        if rn <= chunk["header_row"]:
            continue
        c = rows[rn]
        sku = c[col["sku"]].strip() if col["sku"] < len(c) else ""
        if sku and sku not in out:
            out[sku] = c[col[key]].strip() if col[key] < len(c) else ""
    return out


def _apply_description(records: list[dict], chunk: dict) -> None:
    """Deskripsi dibawa apa adanya agar validator dapat memeriksa klaim."""
    for rec in records:
        text = _raw_column(chunk, "description").get(rec["supplier_sku"], "")
        if text:
            rec["fields"]["description"] = {
                "value": text, "status": "extracted", "evidence": text, "confidence": None,
            }


def extract_rules(chunk: dict, category: str | None) -> list[dict]:
    if not chunk.get("columns"):  # header tidak dikenali: parser aturan tidak dapat membaca
        return []
    out = json.loads(predict({"grid": chunk["grid"]}, category_hint=category))
    records: list[dict] = out["records"]
    _apply_category(records, chunk)
    _apply_description(records, chunk)
    counts = chunk.get("duplicate_counts", {})
    for rec in records:
        if rec["supplier_sku"] in counts:
            rec["source_row_count"] = counts[rec["supplier_sku"]]
    return records
