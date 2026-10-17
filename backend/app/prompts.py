"""Pemuat prompt tersimpan. Berkas memakai pemisah '---USER---' antara pesan system dan user."""
from __future__ import annotations

import json

from app import settings

T1_VERSION = "t1_v0"


def load_prompt(name: str) -> tuple[str, str]:
    raw = (settings.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    system, user = raw.split("---USER---")
    return system.strip(), user.strip()


def t1_messages(chunk_text: str) -> list[dict]:
    """Satu pesan system di indeks 0 (bentuk universal yang diterima semua model), lalu satu pesan user."""
    system, user = load_prompt(T1_VERSION)
    taxonomy = "\n".join(json.loads((settings.CONFIG_DIR / "taxonomy.json").read_text(encoding="utf-8")))
    system = system.replace("{{TAXONOMY}}", taxonomy)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.replace("{{TEXT}}", chunk_text)},
    ]
