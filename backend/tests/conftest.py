import pytest


@pytest.fixture(autouse=True)
def _isolate_from_real_ai(monkeypatch):
    """Tes tidak boleh memakai kunci asli dari .env maupun menyentuh anggaran proses yang tersisa."""
    monkeypatch.delenv("RADEON_API_KEY", raising=False)
    import app.main as main

    monkeypatch.setattr(main, "_BUDGET", None)
    main.app.dependency_overrides.clear()
    yield
    main.app.dependency_overrides.clear()
