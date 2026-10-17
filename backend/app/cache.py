"""Cache Replay: keluaran AI nyata per potongan. Kunci = hash(model + versi prompt + teks potongan)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app import settings


def chunk_key(model: str, prompt_version: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{prompt_version}\n{text}".encode()).hexdigest()


def _dir(directory: Path | None) -> Path:
    return directory if directory is not None else settings.DEMO_CACHE_DIR


def load(key: str, directory: Path | None = None) -> dict | None:
    path = _dir(directory) / f"{key}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return None
    return data


def save(key: str, payload: dict, directory: Path | None = None) -> Path:
    d = _dir(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{key}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
