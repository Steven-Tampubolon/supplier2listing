# Deviations

(kosong)

## D-001 (Langkah 2): kode harness dikecualikan dari lint
- Apa: `backend/app/harness/` (salinan `schema.py`, `baseline.py`, `common.py` dari bake-off) ada di `extend-exclude` pada `ruff.toml`.
- Mengapa: kode itu sudah teruji di bake-off; merapikan gaya berisiko mengubah perilaku parser. Kode baru tetap di-lint (E, F, I, B).

## D-002 (Langkah 4): backend di Vercel Hobby, bukan Render
- Apa: Render free meminta kartu kredit pada akun pemilik produk. Backend dideploy sebagai FastAPI di Vercel Hobby (root `backend/`, entrypoint `backend/main.py`).
- Dampak: tidak ada proses yang hidup terus (serverless); penghitung anggaran di memori tidak dibagi antar instance (ditangani di Langkah 6). Vercel Hobby hanya untuk penggunaan non-komersial; dicatat di README. Bagian C dokumen sprint (Render) tidak berlaku.

## D-003 (Langkah 5A): config, prompts, samples dipindah ke backend/
- Apa: struktur repo di dokumen sprint menaruhnya di akar; kini di `backend/config`, `backend/prompts`, `backend/samples`.
- Mengapa: Vercel hanya membundel Root Directory (`backend`); berkas di luarnya tidak ikut terdeploy.
- Dampak: tidak ada pada perilaku; jalur dibaca lewat `app/settings.py`.

## D-004 (Langkah 5B): validator disetel dengan melihat seluruh gold, termasuk split test
- Apa: pola klaim, nama generik, dan aturan kontradiksi judul dikalibrasi terhadap 13 file non-PDF di gold (semua split, karena file tidak dipisah per split). Dokumen dataset menyebut split test terkunci.
- Dampak: angka kecocokan dengan gold (semua kode deterministik cocok kecuali atribut yang hanya ada di gambar) TIDAK boleh dilaporkan sebagai akurasi. Evaluasi sebenarnya adalah pilot dengan seller.
- Keterbatasan: field `spec:*` (atribut yang ada di SKU saudara tetapi hilang di SKU ini) belum dicakup; baris dengan beberapa SKU dalam satu sel hanya dibaca AI.

## D-005 (Langkah 5B): header tak baku tidak ditolak
- Apa: file dengan judul kolom tak dikenali (mis. `Art.No`, `Desc`, `Mrk`) tetap dipotong per 5 baris untuk AI; parser aturan melewatinya dengan peringatan (`header_recognized: false`, `rules_records: []`).
- Mengapa: 4 dari 16 file non-PDF di gold seperti itu; menolaknya menutup jalur AI. Sinonim tidak ditambah karena `Desc` pada file itu bermakna nama produk, bukan deskripsi (menebak = salah).
