"""Smoke test produksi. Pemakaian: python scripts/smoke_e2e.py https://<backend>.vercel.app"""
import json
import sys
import urllib.error
import urllib.request


def get(url: str, timeout: int = 90):
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        return r.status, json.loads(r.read())


def main(base: str) -> int:
    base = base.rstrip("/")
    try:
        status, body = get(f"{base}/api/health")
    except urllib.error.HTTPError as e:
        print(f"GAGAL /api/health: HTTP {e.code} {e.reason} ({base})")
        return 1
    except urllib.error.URLError as e:
        print(f"GAGAL /api/health: tidak terjangkau ({e.reason}); cek ejaan URL: {base}")
        return 1
    if status != 200 or body.get("status") != "ok":
        print(f"GAGAL /api/health: {status} {body}")
        return 1
    print("OK  /api/health", body)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("pemakaian: smoke_e2e.py <base_url_backend>")
    sys.exit(main(sys.argv[1]))
