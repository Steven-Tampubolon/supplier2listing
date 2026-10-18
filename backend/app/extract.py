"""Ekstraksi satu potongan: mode aturan, replay, atau AI live dengan cadangan parser aturan.

Prinsip: keluaran AI tidak dipercaya. Ia disanitasi, buktinya dicek terhadap teks sumber, SKU karangan dibuang,
SKU yang hilang diisi dari parser aturan (ditandai needs_review), lalu melewati validator deterministik.
"""
from __future__ import annotations

import copy
import json
import re
import time
from datetime import datetime, timezone

from app import cache
from app.harness.common import ALL_STATUSES, norm_key
from app.harness.schema import extract_json
from app.llm import LLMClient
from app.prompts import T1_VERSION, t1_messages
from app.rules import extract_rules
from app.validate import validate_records

MAX_RECORDS = 60
MAX_FIELDS = 80
MAX_STR = 2000
VALUE_STATUSES = {"extracted", "normalized", "inferred"}
NOTE_FALLBACK = "Hasil parser aturan karena AI tidak tersedia; periksa nilainya."
NOTE_NO_EVIDENCE = "Bukti tidak ditemukan di berkas sumber."
NOTE_NO_QUOTE = "Nilai tanpa kutipan bukti dari sumber."


def _clip(v):
    if isinstance(v, str):
        return v[:MAX_STR]
    if v is None or isinstance(v, (int, float, bool)):
        return v
    if isinstance(v, (list, dict)) and len(json.dumps(v, ensure_ascii=False)) <= 2 * MAX_STR:
        return v
    return None


def sanitize_records(obj) -> tuple[list[dict] | None, list[str]]:
    """Kontrak keluaran AI -> rekaman bersih. Status tidak valid menjadi needs_review (bukan extracted)."""
    if not isinstance(obj, dict) or not isinstance(obj.get("records"), list):
        return None, ['top-level harus objek dengan daftar "records"']
    out: list[dict] = []
    seen: set[str] = set()
    problems: list[str] = []
    for i, r in enumerate(obj["records"][:MAX_RECORDS]):
        sku = r.get("supplier_sku") if isinstance(r, dict) else None
        if not isinstance(sku, str) or not sku.strip() or not isinstance(r.get("fields"), dict):
            problems.append(f"record {i}: supplier_sku/fields tidak valid")
            continue
        sku = sku.strip()[:200]
        if norm_key(sku) in seen:
            problems.append(f"record {i}: SKU ganda {sku}")
            continue
        seen.add(norm_key(sku))
        fields: dict[str, dict] = {}
        for name, e in list(r["fields"].items())[:MAX_FIELDS]:
            if not isinstance(name, str) or name == "description":  # deskripsi hanya dari sumber/aturan
                continue
            if not isinstance(e, dict) or "value" not in e:
                fields[name] = {"value": None if isinstance(e, dict) else _clip(e), "status": "needs_review",
                                "evidence": None, "confidence": None}
                continue
            value = _clip(e["value"])
            status = e.get("status")
            if status not in ALL_STATUSES:
                status = "needs_review" if value is not None else "missing"
            elif value is None and status in VALUE_STATUSES:
                status = "missing"
            conf = e.get("confidence")
            entry = {
                "value": value, "status": status,
                "evidence": e["evidence"][:MAX_STR] if isinstance(e.get("evidence"), str) else None,
                "confidence": min(1.0, max(0.0, float(conf))) if isinstance(conf, (int, float)) else None,
            }
            for k in ("unit", "system"):
                if isinstance(e.get(k), str):
                    entry[k] = e[k][:50]
            fields[name] = entry
        issues = [
            {"code": str(x["code"])[:60], "field": x["field"] if isinstance(x.get("field"), str) else None}
            for x in (r.get("issues") if isinstance(r.get("issues"), list) else [])
            if isinstance(x, dict) and "code" in x
        ]
        group = r.get("product_group")
        out.append({"supplier_sku": sku, "product_group": group if isinstance(group, str) else f"g-{sku}",
                    "fields": fields, "issues": issues})
    return out, problems


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def drop_unknown_skus(records: list[dict], text: str) -> tuple[list[dict], list[str]]:
    """Buang rekaman yang kode SKU-nya tidak muncul di teks potongan (karangan model)."""
    haystack = _norm(text)
    kept, dropped = [], []
    for r in records:
        (kept if _norm(r["supplier_sku"]) in haystack else dropped).append(r)
    return kept, [r["supplier_sku"] for r in dropped]


def ground_evidence(records: list[dict], text: str) -> int:
    """Nilai yang buktinya tidak ada di sumber diturunkan ke needs_review. Mengembalikan jumlah yang diturunkan."""
    haystack = _norm(text)
    n = 0
    for r in records:
        for e in r["fields"].values():
            if e["status"] not in ("extracted", "normalized") or e["value"] is None:
                continue
            ev = e.get("evidence")
            if not ev or not ev.strip():
                e["status"], e["note"] = "needs_review", NOTE_NO_QUOTE
                n += 1
            elif _norm(ev) not in haystack:
                e["status"], e["note"] = "needs_review", NOTE_NO_EVIDENCE
                n += 1
    return n


def mark_needs_review(records: list[dict], note: str = NOTE_FALLBACK) -> list[dict]:
    """Field bernilai ditandai needs_review. Field kosong tetap missing agar validator tetap melihatnya."""
    out = copy.deepcopy(records)
    for r in out:
        for e in r["fields"].values():
            if e.get("status") in VALUE_STATUSES and e.get("value") is not None:
                e["status"], e["note"] = "needs_review", note
    return out


def _postprocess(ai_records: list[dict], chunk: dict, category: str | None, rules: list[dict]) -> dict:
    text = chunk["text"]
    records = copy.deepcopy(ai_records)
    records, dropped = drop_unknown_skus(records, text)
    downgraded = ground_evidence(records, text)

    by_key = {norm_key(r["supplier_sku"]): r for r in rules}
    got = {norm_key(r["supplier_sku"]) for r in records}
    filled = []
    for sku in chunk.get("skus", []):
        if ";" not in sku and norm_key(sku) not in got and norm_key(sku) in by_key:
            records.append(mark_needs_review([by_key[norm_key(sku)]])[0])
            filled.append(sku)
    counts = {norm_key(k): v for k, v in (chunk.get("duplicate_counts") or {}).items()}
    for r in records:
        rule = by_key.get(norm_key(r["supplier_sku"]))
        if rule and "description" in rule["fields"] and "description" not in r["fields"]:
            r["fields"]["description"] = copy.deepcopy(rule["fields"]["description"])
        if norm_key(r["supplier_sku"]) in counts:
            r["source_row_count"] = counts[norm_key(r["supplier_sku"])]
    return {"records": records, "dropped_skus": dropped, "rules_filled": filled, "evidence_downgraded": downgraded}


def _finish(records: list[dict], category: str | None) -> list[dict]:
    return validate_records(records, category)[0]


async def run_extract(chunk: dict, category: str | None, mode: str, llm: LLMClient, save_cache: bool = False) -> dict:
    t0 = time.monotonic()
    text = chunk["text"]
    rules = extract_rules(chunk, category)
    out: dict = {
        "chunk_id": chunk.get("chunk_id"), "source": "rules", "fallback_reason": None, "usage": {}, "attempts": [],
        "recorded_at": None, "notes": [], "rules_filled": [], "dropped_skus": [], "evidence_downgraded": 0,
        "_remaining_usd": None,
    }

    def fallback(reason: str) -> list[dict]:
        out["source"], out["fallback_reason"] = "rules", reason
        if not rules:
            out["notes"].append("Parser aturan tidak dapat membaca potongan ini (judul kolom tidak baku).")
        return mark_needs_review(rules)

    records: list[dict]
    if mode == "rules":
        records = copy.deepcopy(rules)
    elif mode == "replay":
        key = cache.chunk_key(llm.cfg.model, T1_VERSION, text)
        hit = cache.load(key)
        if hit is None:
            records = fallback("replay_miss")
        else:
            pp = _postprocess(hit["records"], chunk, category, rules)
            records, out["source"], out["recorded_at"] = pp["records"], "replay", hit.get("recorded_at")
            out.update({k: pp[k] for k in ("rules_filled", "dropped_skus", "evidence_downgraded")})
    else:  # live
        res = await llm.chat(t1_messages(text))
        out["attempts"], out["_remaining_usd"] = res.attempts, res.remaining_usd
        if not res.ok:
            records = fallback(res.reason or "unknown")
        else:
            obj, err = extract_json(res.content)
            recs, problems = sanitize_records(obj) if obj is not None else (None, [err or "JSON tidak valid"])
            if not recs:
                records = fallback("json_invalid")
            else:
                pp = _postprocess(recs, chunk, category, rules)
                records, out["source"], out["usage"] = pp["records"], "ai", res.usage
                out.update({k: pp[k] for k in ("rules_filled", "dropped_skus", "evidence_downgraded")})
                if problems:
                    out["notes"].append(f"{len(problems)} bagian keluaran AI dilewati karena tidak sesuai kontrak.")
                if save_cache:
                    cache.save(cache.chunk_key(llm.cfg.model, T1_VERSION, text), {
                        "records": recs, "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "model": llm.cfg.model, "prompt_version": T1_VERSION, "usage": res.usage,
                    })
    out["records"] = _finish(records, category)
    out["latency_ms"] = int((time.monotonic() - t0) * 1000)
    return out


postprocess = _postprocess  # dipakai perekam Replay untuk mengukur mutu keluaran
