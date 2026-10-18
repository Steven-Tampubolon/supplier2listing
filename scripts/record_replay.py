"""Rekam keluaran AI nyata untuk file contoh ke backend/demo_cache/ (bahan mode Replay).

Jalankan dari folder backend dengan venv aktif:
    python ../scripts/record_replay.py --dry-run     # lihat potongan dan status cache, tanpa memanggil API
    python ../scripts/record_replay.py               # rekam semua yang belum ada (bisa diulang; melanjutkan)
Pilihan: --files a.xlsx b.csv  --concurrency 3  --attempts 3  --idle-timeout 120  --total-timeout 900  --force
PENTING: jangan ubah parse.py/teks potongan setelah merekam; kunci cache = hash teks potongan.
Kunci dibaca dari backend/.env dan tidak pernah dicetak.
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app import cache  # noqa: E402
from app.llm import LLMClient, LLMConfig  # noqa: E402
from app.parse import parse_file  # noqa: E402
from app.prompts import T1_VERSION  # noqa: E402
from app.recorder import record_all  # noqa: E402
from app.settings import SAMPLES_DIR  # noqa: E402

DEFAULT_FILES = ["rina_F1.xlsx", "andi_EA.xlsx", "sinta_S3.xlsx", "rina_F3.csv"]
T0 = time.monotonic()


def log(msg: str) -> None:
    print(f"[{time.monotonic() - T0:6.0f}s] {msg}", flush=True)


async def main(args) -> int:
    cfg = LLMConfig.from_env()
    jobs = []
    for name in args.files:
        parsed = parse_file(name, (SAMPLES_DIR / name).read_bytes())
        for chunk in parsed["chunks"]:
            jobs.append((name, {**chunk, "chunk_id": f"{name}#{chunk['chunk_id']}"}, parsed["category"]))
    print(f"Model: {cfg.model} | prompt: {T1_VERSION} | {len(jobs)} potongan dari {len(args.files)} berkas")
    if args.dry_run:
        for _name, chunk, _cat in jobs:
            hit = cache.load(cache.chunk_key(cfg.model, T1_VERSION, chunk["text"])) is not None
            print(f"  {chunk['chunk_id']:28s} {len(chunk['skus'])} SKU  cache: {'ADA' if hit else 'belum'}")
        return 0
    if not cfg.enabled:
        print("RADEON_API_KEY belum diisi (backend/.env). Tidak ada panggilan yang dilakukan.")
        return 1
    log("mulai merekam (streaming; sabar, tiap potongan bisa 3-8 menit)")
    results = await record_all(
        LLMClient(cfg), jobs, concurrency=args.concurrency, attempts=args.attempts, idle_timeout=args.idle_timeout,
        total_timeout=args.total_timeout, force=args.force, log=log,
    )
    print("\nRingkasan:")
    for name, r in results:
        print(f"  {name:18s} {r['status']:9s} percobaan={r['attempts']} {r['reason'] or ''}")
    failed = [n for n, r in results if r["status"] == "failed"]
    print(f"\n{len(results) - len(failed)} dari {len(results)} potongan siap. "
          + ("Ulangi perintah yang sama untuk melanjutkan yang gagal." if failed else "Semua potongan sudah terekam."))
    return 2 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", default=DEFAULT_FILES)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--idle-timeout", type=float, default=120.0)
    ap.add_argument("--total-timeout", type=float, default=900.0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
