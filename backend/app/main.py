import os
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.export import ExportError, build_export
from app.parse import MAX_BYTES, ParseError, parse_file
from app.rules import extract_rules
from app.settings import SAMPLES_DIR
from app.validate import summarize, validate_records

load_dotenv()

VERSION = "0.4.0"

SAMPLES = [
    {
        "name": "rina_F1.xlsx",
        "title": "Rina: fashion, satu baris per varian",
        "note": "Kasus mudah: bahasa Indonesia, harga Rp, satu barcode kosong.",
        "category": "fashion",
    },
    {
        "name": "andi_EA.xlsx",
        "title": "Andi: elektronik, dua sheet",
        "note": "Sheet Pricelist memuat berat berbeda dari sheet Spec; ada baris duplikat dan spesifikasi satu sel.",
        "category": "electronics",
    },
    {
        "name": "sinta_S3.xlsx",
        "title": "Sinta: kecantikan, baris judul dan sel gabungan",
        "note": "Ada baris judul di atas header, satu baris duplikat, dan satuan isi bersih campuran.",
        "category": "beauty",
    },
    {
        "name": "rina_F3.csv",
        "title": "Rina: fashion, CSV tanpa kode induk",
        "note": "Varian tercampur di nama produk; berat dengan koma desimal.",
        "category": "fashion",
    },
]
SAMPLE_NAMES = {s["name"] for s in SAMPLES}


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


def _ai_enabled() -> bool:
    return bool(os.getenv("RADEON_API_KEY"))


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": VERSION,
        "model": os.getenv("AI_MODEL", "DeepSeek-V4.1-Flash"),
        "ai_enabled": _ai_enabled(),
        # sementara = batas harian; budget.py (Langkah 6) menggantinya dengan sisa nyata
        "daily_ai_budget_left": int(os.getenv("MAX_AI_CHUNKS_PER_DAY", "500")),
    }


@app.get("/api/samples")
def samples() -> dict:
    # Mode replay ditambahkan setelah demo_cache terisi (Langkah 8).
    modes = ["rules"] + (["live"] if _ai_enabled() else [])
    return {"samples": [{**s, "modes": modes} for s in SAMPLES]}


@app.post("/api/parse")
def parse(
    file: Annotated[UploadFile | None, File()] = None,
    sample: Annotated[str | None, Form()] = None,
    category: Annotated[str | None, Form()] = None,
) -> dict:
    category = category or None
    if sample:
        if sample not in SAMPLE_NAMES:
            raise HTTPException(status_code=404, detail="File contoh tidak ditemukan.")
        filename, data = sample, (SAMPLES_DIR / sample).read_bytes()
    elif file is not None and file.filename:
        filename = file.filename
        data = file.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(
                status_code=413, detail="Berkas lebih dari 2 MB. Kecilkan berkas atau pecah menjadi beberapa bagian."
            )
    else:
        raise HTTPException(status_code=400, detail="Pilih berkas .xlsx/.csv atau salah satu file contoh.")
    try:
        result = parse_file(filename, data, category)
    except ParseError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Header tak baku -> extract_rules mengembalikan [] (hanya AI yang dapat membaca)
    rules_records = [r for c in result["chunks"] for r in extract_rules(c, result["category"])]
    return {**result, "rules_records": rules_records}


class ValidateRequest(BaseModel):
    records: list[dict[str, Any]] = Field(max_length=400)
    category: Literal["fashion", "electronics", "beauty"] | None = None


@app.post("/api/validate")
def validate(req: ValidateRequest) -> dict:
    records, issues = validate_records(req.records, req.category)
    return {"records": records, "issues": issues, "summary": summarize(issues)}


class ExportRequest(BaseModel):
    records: list[dict[str, Any]] = Field(max_length=400)
    category: Literal["fashion", "electronics", "beauty"] | None = None
    confirm_blockers: bool = False


@app.post("/api/export")
def export(req: ExportRequest):
    try:
        return build_export(req.records, req.category, req.confirm_blockers)
    except ExportError as e:
        if e.blocked:  # butuh konfirmasi eksplisit dari pengguna
            return JSONResponse(status_code=409, content={"detail": str(e), "blocked_skus": e.blocked})
        raise HTTPException(status_code=400, detail=str(e)) from e
