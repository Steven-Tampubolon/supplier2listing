"""Perekam Replay: menjalankan potongan lewat AI (streaming, batas waktu panjang) dan menyimpan ke demo_cache/.

Berjalan dari komputer pengembang, bukan di Vercel, sehingga tidak terikat batas 300 detik fungsi serverless.
Dapat dilanjutkan: potongan yang sudah ada di cache dilewati kecuali force=True.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timezone

from app import cache
from app.extract import postprocess, sanitize_records
from app.harness.schema import extract_json
from app.llm import LLMClient
from app.prompts import T1_VERSION, t1_messages
from app.rules import extract_rules


async def record_chunk(
    llm: LLMClient, chunk: dict, category: str | None, *, attempts: int = 3, idle_timeout: float = 120.0,
    total_timeout: float = 900.0, force: bool = False, log: Callable[[str], None] = print,
) -> dict:
    """Hasil: {status: 'cached'|'recorded'|'failed', key, attempts, reason, metrics}."""
    key = cache.chunk_key(llm.cfg.model, T1_VERSION, chunk["text"])
    label = f"{chunk.get('chunk_id')} ({len(chunk.get('skus', []))} SKU)"
    if not force and cache.load(key) is not None:
        log(f"  {label}: sudah ada di cache, dilewati")
        return {"status": "cached", "key": key, "attempts": 0, "reason": None, "metrics": {}}
    reason = None
    for n in range(1, attempts + 1):
        log(f"  {label}: percobaan {n}/{attempts}...")
        res = await llm.stream_chat(t1_messages(chunk["text"]), idle_timeout=idle_timeout, total_timeout=total_timeout)
        if not res.ok:
            reason = res.reason
            log(f"  {label}: gagal ({reason}) setelah {res.latency_ms / 1000:.0f} dtk")
            continue
        obj, err = extract_json(res.content)
        recs, problems = sanitize_records(obj) if obj is not None else (None, [err or "JSON tidak valid"])
        if not recs:
            reason = "json_invalid"
            log(f"  {label}: keluaran tidak terbaca ({problems[:1]})")
            continue
        pp = postprocess(recs, chunk, category, extract_rules(chunk, category))
        metrics = {
            "records": len(pp["records"]), "expected": len(chunk.get("skus", [])), "rules_filled": pp["rules_filled"],
            "dropped_skus": pp["dropped_skus"], "evidence_downgraded": pp["evidence_downgraded"],
            "seconds": round(res.latency_ms / 1000), "usage": res.usage,
        }
        cache.save(key, {
            "records": recs, "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "model": llm.cfg.model,
            "prompt_version": T1_VERSION, "usage": res.usage, "metrics": metrics,
        })
        log(f"  {label}: DIREKAM dalam {metrics['seconds']} dtk | {json.dumps(metrics, ensure_ascii=False)}")
        return {"status": "recorded", "key": key, "attempts": n, "reason": None, "metrics": metrics}
    return {"status": "failed", "key": key, "attempts": attempts, "reason": reason, "metrics": {}}


async def record_all(
    llm: LLMClient, jobs: list[tuple[str, dict, str | None]], *, concurrency: int = 3, **kw
) -> list[tuple[str, dict]]:
    """jobs = [(nama_berkas, chunk, kategori)]. Paralel terbatas; urutan hasil mengikuti jobs."""
    sem = asyncio.Semaphore(concurrency)

    async def one(name: str, chunk: dict, cat: str | None):
        async with sem:
            return name, await record_chunk(llm, chunk, cat, **kw)

    return await asyncio.gather(*(one(*j) for j in jobs))
