import asyncio
import json
import logging

import httpx

from tests.helpers import chat_body, make_llm, ok_handler, sequence_handler

MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def run(llm):
    return asyncio.run(llm.chat(MSGS))


def resp(status, **kw):
    return httpx.Response(status, **kw)


def test_request_shape_and_success_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json=chat_body('{"records": []}'), headers={"x-ratelimit-remaining-user-daily-usd": "0.84"}
        )

    res = run(make_llm(handler))
    assert res.ok and res.content == '{"records": []}'
    assert seen["url"].endswith("/chat/completions") and seen["auth"] == "Bearer rc-test-key"
    b = seen["body"]
    assert b["model"] == "DeepSeek-V4.1-Flash" and b["temperature"] == 0
    assert b["response_format"] == {"type": "json_object"} and b["reasoning_effort"] == "none"
    assert b["messages"][0]["role"] == "system" and len(b["messages"]) == 2
    assert res.usage == {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.001}
    assert res.remaining_usd == 0.84 and res.attempts == [{"status": 200, "latency_ms": res.attempts[0]["latency_ms"]}]


def test_reasoning_effort_omitted_when_not_configured():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=chat_body("{}"))

    run(make_llm(handler, reasoning_effort=None))
    assert "reasoning_effort" not in seen["body"]


def test_429_is_retried_once_honouring_retry_after():
    h = sequence_handler([resp(429, headers={"Retry-After": "3"}), resp(200, json=chat_body("{}"))])
    llm = make_llm(h)
    res = run(llm)
    assert res.ok and [a["status"] for a in res.attempts] == [429, 200]
    assert llm.sleeps == [3.0] and h.calls["n"] == 2


def test_retry_after_is_capped():
    h = sequence_handler([resp(429, headers={"Retry-After": "60"}), resp(200, json=chat_body("{}"))])
    llm = make_llm(h)
    assert run(llm).ok and llm.sleeps == [20.0]


def test_429_twice_gives_rate_limited():
    h = sequence_handler([resp(429, headers={"Retry-After": "1"})])
    res = run(make_llm(h))
    assert not res.ok and res.reason == "rate_limited" and h.calls["n"] == 2


def test_504_then_success():
    h = sequence_handler([resp(504, text="gateway timeout"), resp(200, json=chat_body("{}"))])
    res = run(make_llm(h))
    assert res.ok and h.calls["n"] == 2


def test_upstream_500_twice_fails_after_exactly_one_retry():
    h = sequence_handler([resp(500, json={"error": "upstream"})])
    res = run(make_llm(h))
    assert not res.ok and res.reason == "upstream_error" and h.calls["n"] == 2


def test_empty_content_retried_then_ok():
    h = sequence_handler([resp(200, json=chat_body("", finish="length")), resp(200, json=chat_body("{}"))])
    res = run(make_llm(h))
    assert res.ok and h.calls["n"] == 2


def test_empty_content_twice_fails():
    h = sequence_handler([resp(200, json=chat_body("   ", finish="length"))])
    res = run(make_llm(h))
    assert not res.ok and res.reason == "empty_response" and res.finish_reason == "length" and h.calls["n"] == 2


def test_malformed_200_body_counts_as_empty():
    h = sequence_handler([resp(200, json={"unexpected": True})])
    assert run(make_llm(h)).reason == "empty_response"


def test_timeout_is_not_retried():
    h = sequence_handler([httpx.ReadTimeout("slow")])
    res = run(make_llm(h))
    assert not res.ok and res.reason == "timeout" and res.status == 0 and h.calls["n"] == 1


def test_total_timeout_is_enforced_even_if_server_trickles():
    async def slow(request):
        await asyncio.sleep(2)
        return httpx.Response(200, json=chat_body("{}"))

    res = run(make_llm(slow, timeout=0.05))
    assert not res.ok and res.reason == "timeout"


def test_network_error_retried_once():
    h = sequence_handler([httpx.ConnectError("down"), resp(200, json=chat_body("{}"))])
    res = run(make_llm(h))
    assert res.ok and h.calls["n"] == 2


def test_auth_and_bad_request_not_retried():
    for status, reason in ((401, "auth"), (403, "auth"), (400, "bad_request"), (404, "bad_request")):
        h = sequence_handler([resp(status, json={"error": "x"})])
        res = run(make_llm(h))
        assert not res.ok and res.reason == reason and h.calls["n"] == 1


def test_no_retry_when_deadline_too_close():
    h = sequence_handler([resp(429, headers={"Retry-After": "1"}), resp(200, json=chat_body("{}"))])
    res = run(make_llm(h, deadline=10.0))
    assert not res.ok and h.calls["n"] == 1


def test_key_and_content_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    h = sequence_handler([resp(500, text="boom rc-test-key")])
    run(make_llm(h))
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "rc-test-key" not in text and "Bearer" not in text


def test_ok_handler_helper_counts_calls():
    h = ok_handler("{}")
    run(make_llm(h))
    assert h.calls["n"] == 1
