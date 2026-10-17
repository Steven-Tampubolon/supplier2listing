# Deviations

(kosong)

## D-001 (Langkah 2): kode harness dikecualikan dari lint
- Apa: `backend/app/harness/` (salinan `schema.py`, `baseline.py`, `common.py` dari bake-off) ada di `extend-exclude` pada `ruff.toml`.
- Mengapa: kode itu sudah teruji di bake-off; merapikan gaya berisiko mengubah perilaku parser. Kode baru tetap di-lint (E, F, I, B).

## D-002 (Langkah 4): backend di Vercel Hobby, bukan Render
- Apa: Render free meminta kartu kredit pada akun pemilik produk. Backend dideploy sebagai FastAPI di Vercel Hobby (root `backend/`, entrypoint `backend/main.py`).
- Dampak: tidak ada proses yang hidup terus (serverless); penghitung anggaran di memori tidak dibagi antar instance (ditangani di Langkah 6). Vercel Hobby hanya untuk penggunaan non-komersial; dicatat di README. Bagian C dokumen sprint (Render) tidak berlaku.
