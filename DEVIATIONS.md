# Deviations

(kosong)

## D-001 (Langkah 2): kode harness dikecualikan dari lint
- Apa: `backend/app/harness/` (salinan `schema.py`, `baseline.py`, `common.py` dari bake-off) ada di `extend-exclude` pada `ruff.toml`.
- Mengapa: kode itu sudah teruji di bake-off; merapikan gaya berisiko mengubah perilaku parser. Kode baru tetap di-lint (E, F, I, B).
