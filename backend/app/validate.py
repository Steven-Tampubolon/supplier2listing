"""Validator deterministik. Menghitung isu yang tidak perlu AI dan tidak pernah mengoreksi nilai sumber."""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache

from app import settings
from app.harness.baseline import COLOR_WORDS
from app.harness.common import CRITICAL, norm_key

SEVERITY = {
    "GTIN_INVALID": "blocker",
    "CLAIM_UNSUBSTANTIATED": "blocker",
    "GTIN_MISSING": "warning",
    "REQUIRED_ATTR_MISSING": "warning",
    "DUPLICATE_SKU": "warning",
    "CATEGORY_AMBIGUOUS": "warning",
    "TITLE_ATTR_CONTRADICTION": "warning",
}
# Kode yang dihitung ulang di sini. Isu lain dari masukan (mis. dari AI) dipertahankan.
DETERMINISTIC = frozenset(SEVERITY)

ACTIONS = {
    "GTIN_INVALID": "Periksa barcode di berkas supplier. Nilai tidak dikoreksi otomatis.",
    "GTIN_MISSING": "Minta GTIN ke supplier, atau isi manual bila produk memang punya barcode.",
    "REQUIRED_ATTR_MISSING": "Lengkapi dari supplier. Jangan ditebak.",
    "DUPLICATE_SKU": "Periksa baris ganda di berkas dan pastikan hanya satu yang benar.",
    "CLAIM_UNSUBSTANTIATED": "Hapus klaim atau lampirkan bukti sebelum produk tayang.",
    "CATEGORY_AMBIGUOUS": "Pilih kategori yang lebih spesifik.",
    "TITLE_ATTR_CONTRADICTION": "Samakan nama produk dengan atributnya, atau perbaiki atributnya.",
}
OTHER_SEVERITY = "warning"

FLAG_STATUSES = {"conflict", "needs_review", "error"}

CLAIM_PATTERNS = [
    r"menyembuhkan", r"mengobati", r"penyembuh", r"terbukti (secara )?klinis", r"teruji klinis",
    r"menghilangkan [\w\s]{0,30}(dalam|selama) \d+ (hari|minggu|jam)", r"dalam \d+ hari",
    r"(100% aman|aman 100%)", r"(tanpa|bebas) efek samping", r"anti ?kanker", r"ajaib",
    r"\bcures?\b", r"\bcured\b", r"\bheals\b", r"clinically proven",
    r"\btreats? [\w\s]{0,20}(eczema|infections?|acne|allerg\w*|disease|cancer|diabetes)",
    r"100% safe", r"no side effects?", r"\bmiracle\b", r"in \d+ days",
    r"(terbaik|best) (di|in) (dunia|the world)", r"\bsatu-satunya\b",
]
_CLAIM_RE = re.compile("|".join(CLAIM_PATTERNS), re.IGNORECASE)

GENERIC_CATEGORIES = {
    "lainnya", "lain-lain", "lain", "others", "other", "umum", "general", "misc", "miscellaneous",
    "uncategorized", "tanpa kategori",
}
GENERIC_NAME_RE = re.compile(
    r"^(special |new |hot |best )?(promo|arrival|item|product|produk|barang|terbaru|baru)"
    r"( item| produk| barang| terbaru| baru)*$",
    re.IGNORECASE,
)
CAPACITY_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s?(GB|TB|mAh)(?!\w)", re.IGNORECASE)
LENGTH_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s?m(?![\w])", re.IGNORECASE)


@lru_cache(maxsize=1)
def _rules() -> dict:
    return json.loads((settings.CONFIG_DIR / "channel_rules_v0.json").read_text(encoding="utf-8"))


def required_fields(category: str | None) -> list[str]:
    r = _rules()
    out = list(r["universal_required"]) + list(r["required_fields"].get(category or "", []))
    return list(dict.fromkeys(out))  # tanpa duplikat, urutan tetap


def _entry(rec: dict, field: str) -> dict | None:
    e = rec.get("fields", {}).get(field)
    return e if isinstance(e, dict) else None


def _value(rec: dict, field: str):
    e = _entry(rec, field)
    return e.get("value") if e else None


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict)) and not v)


def is_missing(rec: dict, field: str) -> bool:
    e = _entry(rec, field)
    if e is None:
        return True
    if e.get("status") in FLAG_STATUSES:
        return False
    return _blank(e.get("value")) or e.get("status") == "missing"


def gtin_valid(v) -> bool:
    d = re.sub(r"[\s\-]", "", str(v))
    if not (d.isdigit() and len(d) == 13):
        return False
    s = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d[:12]))
    return (10 - s % 10) % 10 == int(d[12])


def _canon_color(text: str) -> str:
    t = text.strip().casefold()
    return COLOR_WORDS.get(t, t)


def named_colors(name: str) -> set[str]:
    """Warna yang disebut di nama. Frasa terpanjang diproses dulu agar 'merah muda' tidak terbaca 'merah'."""
    text = name.casefold()
    found: set[str] = set()
    for word in sorted(COLOR_WORDS, key=len, reverse=True):
        pat = rf"(?<!\w){re.escape(word)}(?!\w)"
        if re.search(pat, text):
            found.add(COLOR_WORDS[word])
            text = re.sub(pat, " ", text)
    return found


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _to_family(value, unit: str) -> tuple[str, float] | None:
    u = (unit or "").casefold()
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if u == "gb":
        return "storage", v
    if u == "tb":
        return "storage", v * 1000
    if u == "mah":
        return "mah", v
    return None


def _close(a: float, b: float, tol: float = 0.03) -> bool:
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-9)


def title_contradictions(rec: dict) -> list[tuple[str, str]]:
    """[(field, alasan)] untuk warna, kapasitas, dan panjang yang disebut nama tetapi berbeda dari atribut."""
    name = _value(rec, "name")
    if not isinstance(name, str) or not name.strip():
        return []
    out: list[tuple[str, str]] = []
    color = _value(rec, "color")
    if isinstance(color, str) and color.strip():
        attr = _canon_color(color)
        others = named_colors(name) - {attr}
        if others:
            out.append(("color", f"Nama menyebut warna '{', '.join(sorted(others))}' tetapi atribut warna '{color}'."))
    cap = _entry(rec, "capacity")
    if cap and not _blank(cap.get("value")):
        attr_fam = _to_family(cap["value"], cap.get("unit", ""))
        if attr_fam:
            named = [_to_family(_num(m.group(1)), m.group(2)) for m in CAPACITY_RE.finditer(name)]
            same = [f[1] for f in named if f is not None and f[0] == attr_fam[0]]
            if same and not any(_close(v, attr_fam[1]) for v in same):
                shown = f"{cap['value']} {cap.get('unit', '')}"
                out.append(("capacity", f"Nama menyebut kapasitas berbeda dari atribut ({shown})."))
    ln = _entry(rec, "length")
    if ln and not _blank(ln.get("value")):
        try:
            attr_len = float(ln["value"])
        except (TypeError, ValueError):
            attr_len = None
        named_len = [_num(m.group(1)) for m in LENGTH_RE.finditer(name)]
        if attr_len is not None and named_len and not any(_close(v, attr_len) for v in named_len):
            out.append(("length", f"Nama menyebut panjang berbeda dari atribut ({ln['value']} m)."))
    return out


def raw_category(rec: dict) -> str:
    e = _entry(rec, "category")
    if not e:
        return ""
    raw = e.get("evidence") or e.get("value") or ""
    return str(raw).strip()


def _issue(sku: str, code: str, field: str, reason: str) -> dict:
    return {
        "sku": sku, "code": code, "field": field, "severity": SEVERITY[code],
        "reason": reason, "action": ACTIONS[code],
    }


def _ensure_field(rec: dict, field: str) -> None:
    if _entry(rec, field) is None:
        rec.setdefault("fields", {})[field] = {"value": None, "status": "missing", "evidence": None, "confidence": None}


def validate_records(records: list[dict], category: str | None) -> tuple[list[dict], list[dict]]:
    """Kembalikan (rekaman dengan isu terhitung ulang, daftar isu rata). Tidak mengubah masukan."""
    recs = copy.deepcopy(records)
    counts: dict[str, int] = {}
    for r in recs:
        k = norm_key(str(r.get("supplier_sku", "")))
        counts[k] = counts.get(k, 0) + 1
    all_issues: list[dict] = []
    req = required_fields(category)

    for rec in recs:
        sku = str(rec.get("supplier_sku", ""))
        rec.setdefault("fields", {})
        found: list[dict] = []

        # nilai kritis hasil inferensi tidak boleh lolos tanpa tinjauan
        for f in CRITICAL:
            e = _entry(rec, f)
            if e and e.get("status") == "inferred":
                e["status"] = "needs_review"

        # GTIN
        _ensure_field(rec, "gtin")
        if is_missing(rec, "gtin"):
            found.append(_issue(sku, "GTIN_MISSING", "gtin", "GTIN kosong."))
        elif not is_missing(rec, "gtin") and _value(rec, "gtin") is not None and not gtin_valid(_value(rec, "gtin")):
            rec["fields"]["gtin"]["status"] = "error"
            found.append(_issue(
                sku, "GTIN_INVALID", "gtin",
                f"GTIN '{_value(rec, 'gtin')}' bukan 13 digit atau check digit GS1 salah.",
            ))

        # field wajib kategori (GTIN punya kode sendiri)
        for f in req:
            if f == "gtin":
                continue
            if is_missing(rec, f):
                _ensure_field(rec, f)
                found.append(_issue(sku, "REQUIRED_ATTR_MISSING", f, f"Field wajib '{f}' kosong."))

        # duplikat
        n_rows = int(rec.get("source_row_count") or 1)
        if n_rows > 1 or counts[norm_key(sku)] > 1:
            found.append(_issue(sku, "DUPLICATE_SKU", "supplier_sku", "SKU muncul lebih dari sekali di berkas."))

        # klaim tanpa dasar
        desc = _value(rec, "description")
        if isinstance(desc, str) and (m := _CLAIM_RE.search(desc)):
            reason = f"Deskripsi memuat klaim: '{m.group(0)}'."
            found.append(_issue(sku, "CLAIM_UNSUBSTANTIATED", "description", reason))

        # kategori ambigu
        raw = raw_category(rec)
        name = _value(rec, "name")
        if raw.casefold() in GENERIC_CATEGORIES:
            found.append(_issue(sku, "CATEGORY_AMBIGUOUS", "category", f"Kategori '{raw}' terlalu umum."))
        elif isinstance(name, str) and GENERIC_NAME_RE.match(name.strip()) and raw:
            found.append(_issue(sku, "CATEGORY_AMBIGUOUS", "category", f"Nama produk '{name}' terlalu generik."))

        # kontradiksi judul vs atribut
        for field, reason in title_contradictions(rec):
            found.append(_issue(sku, "TITLE_ATTR_CONTRADICTION", field, reason))

        # gabungkan: isu non-deterministik dari masukan dipertahankan, sisanya dihitung ulang
        kept = []
        for i in rec.get("issues") or []:
            if not isinstance(i, dict) or i.get("code") in DETERMINISTIC:
                continue
            kept.append({
                "code": i.get("code", "UNKNOWN"), "field": i.get("field"),
                "severity": i.get("severity") or OTHER_SEVERITY,
            })
            all_issues.append({
                "sku": sku, "code": i.get("code", "UNKNOWN"), "field": i.get("field"),
                "severity": i.get("severity") or OTHER_SEVERITY,
                "reason": i.get("reason") or "Dilaporkan saat ekstraksi.",
                "action": i.get("action") or "Tinjau nilainya di berkas sumber.",
            })
        seen: set[tuple[str, str]] = set()
        det = []
        for i in found:
            key = (i["code"], i["field"])
            if key in seen:
                continue
            seen.add(key)
            det.append(i)
            all_issues.append(i)
        rec["issues"] = [{"code": i["code"], "field": i["field"], "severity": i["severity"]} for i in det] + kept
    return recs, all_issues


def summarize(issues: list[dict]) -> dict:
    by_code: dict[str, int] = {}
    for i in issues:
        by_code[i["code"]] = by_code.get(i["code"], 0) + 1
    return {
        "blockers": sum(1 for i in issues if i["severity"] == "blocker"),
        "warnings": sum(1 for i in issues if i["severity"] != "blocker"),
        "by_code": by_code,
    }
