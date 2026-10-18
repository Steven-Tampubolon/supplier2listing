"""Diagnostik latensi AI (BUKAN bagian aplikasi). Memakai kuota kecil (sekitar US$0,003) dan bisa makan ~5 menit.

Jalankan dari folder backend dengan venv aktif:   python ../scripts/latency_probe.py
Pilihan:  --model NAMA  --file rina_F1.xlsx  --chunk 0  --max-seconds 290  --skip-stream
Mengukur: (1) sisa kuota, (2) panggilan kecil non-streaming, (3) potongan T1 lewat streaming:
waktu ke token pertama, jeda terpanjang antar data, kecepatan token, dan apakah hasilnya terbaca.
Kunci dibaca dari backend/.env dan tidak pernah dicetak.
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app.extract import sanitize_records  # noqa: E402
from app.harness.schema import extract_json  # noqa: E402
from app.llm import LLMConfig  # noqa: E402
from app.parse import parse_file  # noqa: E402
from app.prompts import t1_messages  # noqa: E402
from app.settings import SAMPLES_DIR  # noqa: E402


def parse_sse_line(line):
    """Daftar (jenis, teks) dari SATU baris SSE: 'content', 'reasoning', 'usage', 'finish', atau 'done'."""
    if not line.startswith("data:"):
        return []
    data = line[5:].strip()
    if data == "[DONE]":
        return [("done", "")]
    try:
        obj = json.loads(data)
    except ValueError:
        return []
    out = []
    if isinstance(obj.get("usage"), dict):
        out.append(("usage", json.dumps(obj["usage"])))
    for ch in obj.get("choices") or []:
        delta = ch.get("delta") or {}
        if delta.get("content"):
            out.append(("content", delta["content"]))
        for k in ("reasoning", "reasoning_content"):
            if delta.get(k):
                out.append(("reasoning", delta[k]))
        if ch.get("finish_reason"):
            out.append(("finish", ch["finish_reason"]))
    return out


async def stream_probe(client, url, headers, payload, max_seconds):
    t0, first, last, max_gap = time.monotonic(), None, None, 0.0
    content, reasoning, finish, usage, events = [], 0, None, None, 0
    timeline = []

    async def consume():
        nonlocal first, last, max_gap, finish, usage, events, reasoning
        async with client.stream("POST", url, json=payload, headers=headers, timeout=None) as resp:
            if resp.status_code != 200:
                body = (await resp.aread())[:200].decode("utf-8", "replace")
                return resp.status_code, body
            async for line in resp.aiter_lines():
                for kind, text in parse_sse_line(line):
                    now = time.monotonic()
                    if kind in ("content", "reasoning"):
                        first = first if first is not None else now - t0
                        if last is not None:
                            max_gap = max(max_gap, now - last)
                        last = now
                        events += 1
                        if kind == "content":
                            content.append(text)
                        else:
                            reasoning += len(text)
                        if events % 25 == 0:
                            timeline.append((round(now - t0, 1), sum(map(len, content)) + reasoning))
                    elif kind == "finish":
                        finish = text
                    elif kind == "usage":
                        usage = json.loads(text)
            return 200, ""

    try:
        status, err = await asyncio.wait_for(consume(), timeout=max_seconds)
    except asyncio.TimeoutError:
        status, err = 0, f"batas {max_seconds:.0f} detik terlampaui"
    return {
        "status": status, "error": err, "total_s": time.monotonic() - t0, "ttft_s": first, "max_gap_s": max_gap,
        "content": "".join(content), "reasoning_chars": reasoning, "finish": finish, "usage": usage,
        "events": events, "timeline": timeline,
    }


def summarize_quota(d):
    if not isinstance(d, dict):
        return "tidak terbaca"
    keep = {k: v for k, v in d.items() if isinstance(v, (int, float, str)) and k != "recent"}
    return json.dumps(keep, ensure_ascii=False)[:300]


async def main(args) -> int:
    cfg = LLMConfig.from_env()
    if args.model:
        cfg.model = args.model
    if not cfg.enabled:
        print("RADEON_API_KEY belum diisi (backend/.env). Tidak ada panggilan yang dilakukan.")
        return 1
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    base = {"model": cfg.model, "temperature": 0, "reasoning_effort": cfg.reasoning_effort or "none"}
    print(f"Model: {cfg.model}")
    async with httpx.AsyncClient() as client:
        async def quota(label):
            try:
                r = await client.get(f"{cfg.base_url}/usage", headers=headers, timeout=30)
                info = summarize_quota(r.json()) if r.status_code == 200 else f"HTTP {r.status_code}"
                print(f"[kuota {label}] {info}")
            except (httpx.HTTPError, ValueError) as e:
                print(f"[kuota {label}] gagal dibaca: {type(e).__name__}")

        await quota("sebelum")
        t = time.monotonic()
        try:
            r = await client.post(f"{cfg.base_url}/chat/completions", headers=headers, timeout=120, json={
                **base, "max_tokens": 50, "messages": [{"role": "user", "content": "Balas hanya angka: 137*46-89"}]})
            body = r.json() if r.status_code == 200 else {}
            txt = (body.get("choices") or [{}])[0].get("message", {}).get("content")
            print(f"[panggilan kecil] HTTP {r.status_code} dalam {time.monotonic() - t:.1f} detik, jawaban={txt!r}")
        except httpx.HTTPError as e:
            print(f"[panggilan kecil] gagal setelah {time.monotonic() - t:.1f} detik: {type(e).__name__}")
        if args.skip_stream:
            return 0

        parsed = parse_file(args.file, (SAMPLES_DIR / args.file).read_bytes())
        chunk = parsed["chunks"][args.chunk]
        n_sku = len(chunk["skus"])
        print(f"[streaming T1] {args.file} potongan {args.chunk} ({n_sku} SKU); batas {args.max_seconds:.0f} dtk")
        res = await stream_probe(client, f"{cfg.base_url}/chat/completions", headers,
                                 {**base, "max_tokens": cfg.max_tokens, "stream": True,
                                  "stream_options": {"include_usage": True},
                                  "response_format": {"type": "json_object"}, "messages": t1_messages(chunk["text"])},
                                 args.max_seconds)
        print(f"  status={res['status']} {res['error']}")
        ttft = "-" if res["ttft_s"] is None else f"{res['ttft_s']:.1f}"
        print(f"  total={res['total_s']:.1f} dtk | token pertama={ttft} dtk"
              f" | jeda terpanjang={res['max_gap_s']:.1f} dtk | selesai={res['finish']}")
        usage = res["usage"] or {}
        out_tok = usage.get("completion_tokens")
        est = out_tok if out_tok else round(len(res["content"]) / 3.5)
        gen_s = res["total_s"] - (res["ttft_s"] or 0)
        rate = f"{est / gen_s:.1f}" if gen_s > 0 and est else "-"
        tok = f"{out_tok} (dilaporkan)" if out_tok else f"~{est} (perkiraan)"
        print(f"  token keluar={tok} | kecepatan ~{rate} token/dtk"
              f" | teks nalar={res['reasoning_chars']} karakter | usage={usage or '-'}")
        print(f"  kemajuan (detik, karakter): {res['timeline'][:12]}")
        obj, _ = extract_json(res["content"]) if res["content"] else (None, None)
        recs, problems = sanitize_records(obj) if obj is not None else (None, ["tidak terbaca"])
        print(f"  hasil terbaca: {len(recs) if recs else 0} rekaman dari {n_sku} SKU | masalah: {problems[:2]}")
        await quota("sesudah")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--file", default="rina_F1.xlsx")
    ap.add_argument("--chunk", type=int, default=0)
    ap.add_argument("--max-seconds", type=float, default=290.0)
    ap.add_argument("--skip-stream", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
