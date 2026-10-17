"""Smoke test produksi. Pemakaian: python scripts/smoke_e2e.py https://<backend>.onrender.com"""
import json
import sys
import urllib.request


def get(url: str, timeout: int = 90):
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        return r.status, json.loads(r.read())


def main(base: str) -> int:
    base = base.rstrip("/")
    status, body = get(f"{base}/api/health")
    assert status == 200 and body.get("status") == "ok", f"health gagal: {status} {body}"
    print("OK  /api/health", body)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("pemakaian: smoke_e2e.py <base_url_backend>")
    sys.exit(main(sys.argv[1]))
