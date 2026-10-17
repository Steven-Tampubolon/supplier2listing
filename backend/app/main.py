import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

VERSION = "0.1.0"


def _origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(title="Supplier2Listing API", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins(),
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": VERSION,
        "model": os.getenv("AI_MODEL", "DeepSeek-V4.1-Flash"),
        "ai_enabled": bool(os.getenv("RADEON_API_KEY")),
        # sementara = batas harian; budget.py (Langkah 6) menggantinya dengan sisa nyata
        "daily_ai_budget_left": int(os.getenv("MAX_AI_CHUNKS_PER_DAY", "500")),
    }
