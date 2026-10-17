import copy
import json

import httpx

from app.llm import LLMClient, LLMConfig
from app.parse import parse_file
from app.rules import extract_rules
from app.settings import SAMPLES_DIR


def parsed(name: str) -> dict:
    return parse_file(name, (SAMPLES_DIR / name).read_bytes())


def chunk_of(name: str, i: int = 0) -> tuple[dict, str | None]:
    r = parsed(name)
    return r["chunks"][i], r["category"]


def ai_records(chunk: dict, category: str | None) -> list[dict]:
    """Keluaran 'AI' yang sempurna: hasil parser aturan tanpa description (sesuai prompt)."""
    recs = copy.deepcopy(extract_rules(chunk, category))
    for r in recs:
        r["fields"].pop("description", None)
        r.pop("source_row_count", None)
    return recs


def ai_text(chunk: dict, category: str | None, mutate=None) -> str:
    recs = ai_records(chunk, category)
    if mutate:
        mutate(recs)
    return json.dumps({"records": recs}, ensure_ascii=False)


def chat_body(content: str, finish: str = "stop", usage: dict | None = None) -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.001},
    }


def make_llm(handler, **cfg) -> LLMClient:
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    client = LLMClient(
        LLMConfig(api_key="rc-test-key", **cfg), transport=httpx.MockTransport(handler), sleep=fake_sleep
    )
    client.sleeps = sleeps  # type: ignore[attr-defined]
    return client


def ok_handler(text: str, headers: dict | None = None):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=chat_body(text), headers=headers or {})

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


def sequence_handler(responses: list):
    """Setiap panggilan mengambil satu elemen: httpx.Response atau Exception yang dilempar."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        item = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    handler.calls = calls  # type: ignore[attr-defined]
    return handler
