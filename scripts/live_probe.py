"""Uji langsung SATU potongan ke API AMD. Memakai kuota (sekitar US$0,001-0,002) dan bisa makan waktu sampai ~4 menit.

Jalankan dari folder backend dengan venv aktif:   python ../scripts/live_probe.py
Pilihan:  --file andi_EA.xlsx   --chunk 0   --record   (--record menyimpan hasil ke demo_cache/ untuk mode Replay)
Kunci dibaca dari backend/.env atau lingkungan; tidak pernah dicetak.
"""
import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app.extract import run_extract  # noqa: E402
from app.llm import LLMClient, LLMConfig  # noqa: E402
from app.parse import parse_file  # noqa: E402
from app.settings import SAMPLES_DIR  # noqa: E402


async def main(args) -> int:
    cfg = LLMConfig.from_env()
    if not cfg.enabled:
        print("RADEON_API_KEY belum diisi (backend/.env). Tidak ada panggilan yang dilakukan.")
        return 1
    parsed = parse_file(args.file, (SAMPLES_DIR / args.file).read_bytes())
    chunk = parsed["chunks"][args.chunk]
    print(f"Model: {cfg.model} | berkas: {args.file} | potongan {args.chunk} ({len(chunk['skus'])} SKU)")
    print("Memanggil API... (mohon tunggu)")
    out = await run_extract(chunk, parsed["category"], "live", LLMClient(cfg), save_cache=args.record)
    why = f" (cadangan: {out['fallback_reason']})" if out["fallback_reason"] else ""
    statuses = [a["status"] for a in out["attempts"]]
    print(f"\nsumber        : {out['source']}{why}")
    print(f"latensi       : {out['latency_ms'] / 1000:.1f} detik | percobaan: {statuses}")
    print(f"token/biaya   : {out['usage'] or '-'}")
    print(f"sisa kuota    : {out['_remaining_usd']} USD")
    print(f"rekaman       : {len(out['records'])} dari {len(chunk['skus'])} SKU | diisi aturan: {out['rules_filled']} "
          f"| SKU karangan dibuang: {out['dropped_skus']} | bukti diturunkan: {out['evidence_downgraded']}")
    for r in out["records"][:3]:
        f = r["fields"]
        print(f"  {r['supplier_sku']}: gtin={f.get('gtin', {}).get('value')!r}/{f.get('gtin', {}).get('status')} "
              f"isu={[i['code'] for i in r['issues']]}")
    if args.record and out["source"] == "ai":
        print("\nHasil disimpan ke demo_cache/ untuk mode Replay.")
    return 0 if out["source"] == "ai" else 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="rina_F1.xlsx")
    ap.add_argument("--chunk", type=int, default=0)
    ap.add_argument("--record", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
