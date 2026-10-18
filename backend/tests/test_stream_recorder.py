import asyncio
import json

import httpx
import pytest

from app import cache, settings
from app.llm import LLMClient, LLMConfig, parse_sse_line
from app.recorder import record_all, record_chunk
from tests.helpers import ai_text, chunk_of


def ev(d: dict) -> bytes:
    return ("data: " + json.dumps(d) + "\n\n").encode()


def sse(text: str, finish: str | None = "stop", usage: dict | None = None, step: int = 15) -> bytes:
    out = b"".join(ev({"choices": [{"delta": {"content": text[i:i + step]}}]}) for i in range(0, len(text), step))
    if finish:
        out += ev({"choices": [{"delta": {}, "finish_reason": finish}]})
    if usage is not None:
        out += ev({"choices": [], "usage": usage})
    return out + b"data: [DONE]\n\n"


def llm_with(handler, **cfg):
    return LLMClient(LLMConfig(api_key="rc-test-key", **cfg), transport=httpx.MockTransport(handler))


MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def stream(llm, **kw):
    return asyncio.run(llm.stream_chat(MSGS, **kw))


def test_parse_sse_line_variants():
    assert parse_sse_line(": komentar") == [] and parse_sse_line("data: [DONE]") == [("done", "")]
    assert parse_sse_line("data: {rusak") == [] and parse_sse_line("data: [1]") == []
    line = 'data: {"choices":[{"delta":{"content":"ab","reasoning":"r"},"finish_reason":"stop"}],"usage":{"x":1}}'
    kinds = [k for k, _ in parse_sse_line(line)]
    assert kinds == ["usage", "content", "reasoning", "finish"]


def test_stream_success_assembles_content_usage_and_sends_stream_flags():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        body = sse('{"records": []}', usage={"completion_tokens": 7, "cost": 0.001, "x": 1})
        return httpx.Response(200, content=body, headers={"x-ratelimit-remaining-user-daily-usd": "0.9"})

    res = stream(llm_with(handler))
    assert res.ok and res.content == '{"records": []}' and res.finish_reason == "stop"
    assert res.usage == {"completion_tokens": 7, "cost": 0.001} and res.remaining_usd == 0.9
    assert seen["body"]["stream"] is True and seen["body"]["stream_options"] == {"include_usage": True}
    assert seen["body"]["messages"][0]["role"] == "system"


def test_stream_truncated_is_failure():
    res = stream(llm_with(lambda r: httpx.Response(200, content=sse('{"records": [', finish="length"))))
    assert not res.ok and res.reason == "truncated" and res.content == '{"records": ['


def test_stream_without_finish_is_interrupted():
    res = stream(llm_with(lambda r: httpx.Response(200, content=sse('{"rec', finish=None))))
    assert not res.ok and res.reason == "stream_interrupted"


def test_stream_http_errors_use_same_reasons_as_chat():
    for status, reason in ((429, "rate_limited"), (504, "upstream_error"), (401, "auth"), (400, "bad_request")):
        res = stream(llm_with(lambda r, s=status: httpx.Response(s, json={"e": 1})))
        assert not res.ok and res.reason == reason and res.status == status


def test_stream_idle_timeout_when_data_stalls_mid_stream():
    class Stall(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield ev({"choices": [{"delta": {"content": "{"}}]})
            await asyncio.sleep(5)

    res = stream(llm_with(lambda r: httpx.Response(200, stream=Stall())), idle_timeout=0.2)
    assert not res.ok and res.reason == "idle_timeout" and res.content == "{"


def test_stream_total_timeout_even_if_data_keeps_trickling():
    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                yield ev({"choices": [{"delta": {"content": "x"}}]})
                await asyncio.sleep(0.05)

    res = stream(llm_with(lambda r: httpx.Response(200, stream=Trickle())), idle_timeout=1.0, total_timeout=0.3)
    assert not res.ok and res.reason == "timeout"


def test_stream_network_error():
    def boom(request):
        raise httpx.ConnectError("down")

    assert stream(llm_with(boom)).reason == "network"


# ---------- perekam ----------
@pytest.fixture
def cdir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_CACHE_DIR", tmp_path)
    return tmp_path


def quiet(_msg):
    pass


def test_record_chunk_saves_and_replay_can_use_it(cdir):
    chunk, cat = chunk_of("rina_F1.xlsx")
    llm = llm_with(lambda r: httpx.Response(200, content=sse(ai_text(chunk, cat), usage={"completion_tokens": 9})))
    out = asyncio.run(record_chunk(llm, chunk, cat, log=quiet))
    assert out["status"] == "recorded" and out["metrics"]["records"] == 5 and out["metrics"]["expected"] == 5
    saved = cache.load(out["key"])
    assert saved["model"] == llm.cfg.model and saved["prompt_version"] == "t1_v0" and saved["recorded_at"]
    from app.extract import run_extract

    replay = asyncio.run(run_extract(chunk, cat, "replay", llm))
    assert replay["source"] == "replay" and len(replay["records"]) == 5


def test_record_chunk_skips_existing_unless_forced(cdir):
    chunk, cat = chunk_of("rina_F1.xlsx")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, content=sse(ai_text(chunk, cat)))

    llm = llm_with(handler)
    asyncio.run(record_chunk(llm, chunk, cat, log=quiet))
    again = asyncio.run(record_chunk(llm, chunk, cat, log=quiet))
    assert again["status"] == "cached" and calls["n"] == 1
    asyncio.run(record_chunk(llm, chunk, cat, force=True, log=quiet))
    assert calls["n"] == 2


def test_record_chunk_retries_then_succeeds(cdir):
    chunk, cat = chunk_of("rina_F1.xlsx")
    seq = [httpx.Response(504), httpx.Response(200, content=sse("bukan json")),
           httpx.Response(200, content=sse(ai_text(chunk, cat)))]
    calls = {"n": 0}

    def handler(request):
        r = seq[calls["n"]]
        calls["n"] += 1
        return r

    out = asyncio.run(record_chunk(llm_with(handler), chunk, cat, attempts=3, log=quiet))
    assert out["status"] == "recorded" and out["attempts"] == 3


def test_record_chunk_failure_saves_nothing(cdir):
    chunk, cat = chunk_of("rina_F1.xlsx")
    out = asyncio.run(record_chunk(llm_with(lambda r: httpx.Response(504)), chunk, cat, attempts=2, log=quiet))
    assert out["status"] == "failed" and out["reason"] == "upstream_error" and list(cdir.iterdir()) == []


def test_record_all_respects_concurrency_and_order(cdir):
    chunks = [chunk_of("rina_F1.xlsx", i) for i in range(2)] + [chunk_of("andi_EA.xlsx", 0)]
    active = {"now": 0, "max": 0}

    async def handler(request):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.05)
        active["now"] -= 1
        body = json.loads(request.content)["messages"][1]["content"]
        chunk = next(c for c, _ in chunks if c["text"] in body)
        cat = "electronics" if "andi_EA" in chunk["text"] else "fashion"
        return httpx.Response(200, content=sse(ai_text(chunk, cat)))

    jobs = [(f"j{i}", c, cat) for i, (c, cat) in enumerate(chunks)]
    results = asyncio.run(record_all(llm_with(handler), jobs, concurrency=2, log=quiet))
    assert [n for n, _ in results] == ["j0", "j1", "j2"] and all(r["status"] == "recorded" for _, r in results)
    assert active["max"] == 2
