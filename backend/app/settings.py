"""Jalur berkas data. Semua data ada di dalam backend/ agar ikut terdeploy (Vercel hanya membundel root)."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
PROMPTS_DIR = BASE_DIR / "prompts"
SAMPLES_DIR = BASE_DIR / "samples"
DEMO_CACHE_DIR = BASE_DIR / "demo_cache"
