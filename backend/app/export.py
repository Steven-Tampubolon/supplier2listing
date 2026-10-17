"""Ekspor CSV baris yang disetujui. Konversi hanya untuk satuan yang dikenal; selebihnya sel kosong + peringatan."""
from __future__ import annotations

import csv
import io
import re
from decimal import ROUND_HALF_UP, Decimal

from app.validate import validate_records

COLUMNS = [
    "supplier_sku", "name", "brand", "category", "color", "size", "material", "gtin", "mpn", "cost_price",
    "currency", "stock", "weight_g", "net_content", "ingredients", "warranty_months", "sell_price",
]
TEXT_FIELDS = ["name", "brand", "category", "color", "size", "material", "gtin", "mpn", "ingredients"]
FILENAME = "supplier2listing_export.csv"

# satuan dikenal -> pengali ke satuan dasar
_TO_GRAM = {
    "g": 1, "gr": 1, "gram": 1, "grams": 1, "kg": 1000, "kilogram": 1000, "kilograms": 1000,
    "oz": 28.3495, "ounce": 28.3495, "ounces": 28.3495, "lb": 453.592, "lbs": 453.592, "pound": 453.592,
    "pounds": 453.592,
}
_TO_ML = {"ml": 1, "l": 1000, "liter": 1000, "litre": 1000, "fl oz": 29.5735, "floz": 29.5735}
_TO_MONTH = {
    "month": 1, "months": 1, "bulan": 1, "bln": 1, "mo": 1,
    "year": 12, "years": 12, "tahun": 12, "thn": 12, "yr": 12, "yrs": 12,
}
_CURRENCIES = {"IDR", "USD", "EUR"}
_MISSING_TEXT = {"null", "none", "n/a", "na", "nan"}
_INJECTION_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _round_half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _fmt_num(x: float) -> str:
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def safe_text(s: str) -> str:
    """Cegah CSV injection: sel teks yang diawali karakter rumus diberi awalan apostrof."""
    return "'" + s if s.startswith(_INJECTION_LEAD) else s


def _is_blank_entry(e: dict | None) -> bool:
    if not isinstance(e, dict):
        return True
    if e.get("status") == "missing":
        return True
    v = e.get("value")
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict)) and not v)


def _text(e: dict | None) -> str:
    if _is_blank_entry(e):
        return ""
    v = e["value"]
    if isinstance(v, list):
        v = ", ".join(str(x) for x in v)
    s = str(v).strip()
    return "" if s.casefold() in _MISSING_TEXT else s


def _split_qty(e: dict) -> tuple[float | None, str]:
    """(angka, satuan) dari value berupa angka, teks '0,5 kg', atau dict {'value','unit'}; satuan jatuh ke e['unit']."""
    v, unit = e.get("value"), str(e.get("unit") or "")
    if isinstance(v, dict):
        unit = str(v.get("unit") or unit)
        v = v.get("value")
    if isinstance(v, bool):
        return None, unit
    if isinstance(v, (int, float)):
        return float(v), unit
    m = re.match(r"\s*([-+]?\d+(?:[.,]\d+)?)\s*([A-Za-z. ]*)", str(v or ""))
    if not m:
        return None, unit
    return float(m.group(1).replace(",", ".")), (m.group(2).strip() or unit)


def _norm_unit(u: str) -> str:
    return re.sub(r"[.\s]+", " ", u.casefold()).strip()


def _convert(e: dict | None, table: dict[str, float], label: str, sku: str, warnings: list[str]) -> float | None:
    if _is_blank_entry(e):
        return None
    n, unit = _split_qty(e)
    u = _norm_unit(unit)
    if n is None or n < 0 or u not in table:
        shown = f"{e.get('value')!r} {unit}".strip()
        warnings.append(f"{sku}: {label} dikosongkan karena nilai/satuan tidak dikenal ({shown}).")
        return None
    return n * table[u]


def _cell_number(e: dict | None, label: str, sku: str, warnings: list[str], integer: bool = False) -> str:
    if _is_blank_entry(e):
        return ""
    n, _ = _split_qty(e)
    raw = e.get("value")
    if n is None or n < 0 or (isinstance(raw, str) and not re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*", raw)):
        warnings.append(f"{sku}: {label} dikosongkan karena bukan angka yang valid ({raw!r}).")
        return ""
    return str(_round_half_up(n)) if integer else _fmt_num(n)


def row_for(rec: dict, warnings: list[str]) -> dict[str, str]:
    sku = str(rec.get("supplier_sku", "")).strip()
    f = rec.get("fields") or {}
    row = {"supplier_sku": safe_text(sku)}
    for name in TEXT_FIELDS:
        row[name] = safe_text(_text(f.get(name)))

    cost = f.get("cost_price")
    row["cost_price"] = _cell_number(cost, "cost_price", sku, warnings)
    cur = str((cost or {}).get("unit") or "").upper() if isinstance(cost, dict) else ""
    row["currency"] = cur if row["cost_price"] and cur in _CURRENCIES else ""
    if row["cost_price"] and not row["currency"]:
        warnings.append(f"{sku}: currency dikosongkan karena mata uang tidak dikenal ({cur or 'kosong'}).")
    row["stock"] = _cell_number(f.get("stock"), "stock", sku, warnings, integer=True)

    grams = _convert(f.get("weight_item"), _TO_GRAM, "weight_g", sku, warnings)
    row["weight_g"] = "" if grams is None else str(_round_half_up(grams))

    row["net_content"] = _net_content(f.get("net_content"), sku, warnings)

    months = _convert(f.get("warranty"), _TO_MONTH, "warranty_months", sku, warnings)
    row["warranty_months"] = "" if months is None else _fmt_num(round(months, 2))
    row["sell_price"] = ""  # selalu kosong: harga jual keputusan seller
    return row


def _net_content(e: dict | None, sku: str, warnings: list[str]) -> str:
    if _is_blank_entry(e):
        return ""
    n, unit = _split_qty(e)
    u = _norm_unit(unit)
    if n is None or n < 0:
        warnings.append(f"{sku}: net_content dikosongkan karena bukan angka yang valid.")
        return ""
    if u in _TO_ML:
        ml = n * _TO_ML[u]
        return f"{_round_half_up(ml) if u != 'ml' else _fmt_num(n)} ml"
    if u in _TO_GRAM:
        g = n * _TO_GRAM[u]
        return f"{_round_half_up(g) if u not in ('g', 'gr', 'gram', 'grams') else _fmt_num(n)} g"
    warnings.append(f"{sku}: net_content dikosongkan karena satuan tidak dikenal ({unit or 'kosong'}).")
    return ""


def blocker_skus(records: list[dict], category: str | None) -> list[str]:
    """SKU berisi isu blocker menurut validasi segar (agar suntingan pengguna ikut dihitung)."""
    _, issues = validate_records(records, category)
    seen: dict[str, None] = {}
    for i in issues:
        if i["severity"] == "blocker":
            seen[i["sku"]] = None
    return list(seen)


def build_export(records: list[dict], category: str | None, confirm_blockers: bool = False) -> dict:
    """Hasilkan CSV dari rekaman bertanda approved. Memunculkan ExportError bila ditolak."""
    approved = [r for r in records if r.get("approved") is True]
    if not approved:
        raise ExportError("Tidak ada baris yang disetujui untuk diekspor.", [])
    blocked = blocker_skus(approved, category)
    if blocked and not confirm_blockers:
        raise ExportError(
            f"{len(blocked)} baris berisi isu blocker. Konfirmasi eksplisit diperlukan untuk mengekspornya.", blocked
        )
    warnings: list[str] = []
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\r\n")
    w.writeheader()
    for rec in approved:
        w.writerow(row_for(rec, warnings))
    return {
        "filename": FILENAME,
        "csv": buf.getvalue(),
        "rows": len(approved),
        "skipped_unapproved": len(records) - len(approved),
        "exported_with_blockers": blocked if confirm_blockers else [],
        "warnings": warnings,
    }


class ExportError(ValueError):
    def __init__(self, message: str, blocked: list[str]):
        super().__init__(message)
        self.blocked = blocked
