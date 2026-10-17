from app import cache, settings


def test_key_depends_on_model_prompt_and_text():
    k = cache.chunk_key("m1", "t1_v0", "teks")
    assert k == cache.chunk_key("m1", "t1_v0", "teks")
    assert len({k, cache.chunk_key("m2", "t1_v0", "teks"), cache.chunk_key("m1", "t1_v1", "teks"),
                cache.chunk_key("m1", "t1_v0", "teks2")}) == 4


def test_roundtrip_and_miss(tmp_path):
    payload = {"records": [{"supplier_sku": "A"}], "recorded_at": "2026-10-18"}
    cache.save("abc", payload, tmp_path)
    assert cache.load("abc", tmp_path) == payload
    assert cache.load("nope", tmp_path) is None


def test_corrupt_or_wrong_shape_is_a_miss(tmp_path):
    (tmp_path / "bad.json").write_text("{tidak json", encoding="utf-8")
    (tmp_path / "shape.json").write_text('{"records": "bukan daftar"}', encoding="utf-8")
    assert cache.load("bad", tmp_path) is None and cache.load("shape", tmp_path) is None


def test_default_directory_follows_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_CACHE_DIR", tmp_path)
    cache.save("k", {"records": []})
    assert (tmp_path / "k.json").exists() and cache.load("k") == {"records": []}
