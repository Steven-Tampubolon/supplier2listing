"""Klien AMD Token Factory (kompatibel OpenAI): timeout total, satu ulangan, tanpa mencatat isi atau kunci."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("s2l.llm")

DEFAULT_BASE = "https://developer.amd.com.cn/radeon/api/v1"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_WAIT = 20.0
MIN_TIME_FOR_RETRY = 20.0


def parse_sse_line(line: str) -> list[tuple[str, str]]:
    """Daftar (jenis, teks) dari satu baris SSE: content, reasoning, usage, finish, atau done."""
    if not line.startswith("data:"):
        return []
    data = line[5:].strip()
    if data == "[DONE]":
        return [("done", "")]
    try:
        obj = json.loads(data)
    except ValueError:
        return []
    if not isinstance(obj, dict):
        return []
    out: list[tuple[str, str]] = []
    if isinstance(obj.get("usage"), dict):
        out.append(("usage", json.dumps(obj["usage"])))
    for ch in obj.get("choices") or []:
        if not isinstance(ch, dict):
            continue
        delta = ch.get("delta") or {}
        if delta.get("content"):
            out.append(("content", delta["content"]))
        for k in ("reasoning", "reasoning_content"):
            if delta.get(k):
                out.append(("reasoning", delta[k]))
        if ch.get("finish_reason"):
            out.append(("finish", ch["finish_reason"]))
    return out


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class LLMConfig:
    api_key: str | None = None
    base_url: str = DEFAULT_BASE
    model: str = "DeepSeek-V4.1-Flash"
    reasoning_effort: str | None = "none"
    timeout: float = 120.0  # per panggilan (batas total, bukan per fase)
    deadline: float = 240.0  # total seluruh percobaan; Vercel Hobby membatasi fungsi 300 detik
    max_tokens: int = 6000

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> LLMConfig:
        return cls(
            api_key=os.getenv("RADEON_API_KEY") or None,
            base_url=(os.getenv("RADEON_BASE_URL") or DEFAULT_BASE).rstrip("/"),
            model=os.getenv("AI_MODEL") or "DeepSeek-V4.1-Flash",
            reasoning_effort=os.getenv("AI_REASONING_EFFORT", "none") or None,
            timeout=_float_env("AI_TIMEOUT_S", 120.0),
            deadline=_float_env("AI_DEADLINE_S", 240.0),
        )


@dataclass
class ChatResult:
    ok: bool
    content: str = ""
    reason: str | None = None  # timeout | network | rate_limited | upstream_error | empty_response | auth | bad_request
    status: int = 0
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    remaining_usd: float | None = None
    latency_ms: int = 0
    attempts: list[dict] = field(default_factory=list)


def _remaining_usd(headers: httpx.Headers) -> float | None:
    raw = headers.get("x-ratelimit-remaining-user-daily-usd")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _retry_after(headers: httpx.Headers) -> float | None:
    try:
        return float(headers["retry-after"]) if "retry-after" in headers else None
    except ValueError:
        return None


def _reason_for_status(status: int) -> str:
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "auth"
    if status >= 500:
        return "upstream_error"
    return "bad_request"


class LLMClient:
    def __init__(self, cfg: LLMConfig, transport: httpx.AsyncBaseTransport | None = None, sleep=asyncio.sleep,
                 clock=time.monotonic):
        self.cfg, self.transport, self.sleep, self.clock = cfg, transport, sleep, clock

    def payload(self, messages: list[dict]) -> dict:
        p = {
            "model": self.cfg.model, "messages": messages, "temperature": 0, "max_tokens": self.cfg.max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.cfg.reasoning_effort:
            p["reasoning_effort"] = self.cfg.reasoning_effort
        return p

    async def _once(self, payload: dict, timeout: float) -> tuple[int, httpx.Headers, dict | None, str | None]:
        """(status, headers, body, galat). status 0 = timeout/jaringan; galat 'timeout' atau 'network'."""
        headers = {"Authorization": f"Bearer {self.cfg.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(transport=self.transport) as client:
                resp = await asyncio.wait_for(
                    client.post(f"{self.cfg.base_url}/chat/completions", json=payload, headers=headers,
                                timeout=timeout),
                    timeout=timeout,
                )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return 0, httpx.Headers(), None, "timeout"
        except httpx.TransportError:
            return 0, httpx.Headers(), None, "network"
        try:
            body = resp.json()
        except ValueError:
            body = None
        return resp.status_code, resp.headers, body if isinstance(body, dict) else None, None

    async def chat(self, messages: list[dict]) -> ChatResult:
        started = self.clock()
        payload = self.payload(messages)
        attempts: list[dict] = []
        last = ChatResult(ok=False, reason="network")
        remaining_usd: float | None = None
        for n in range(2):  # satu percobaan + satu ulangan
            left = self.cfg.deadline - (self.clock() - started)
            if n > 0 and left < MIN_TIME_FOR_RETRY:
                break
            t0 = self.clock()
            status, headers, body, err = await self._once(payload, max(1.0, min(self.cfg.timeout, left)))
            attempts.append({"status": status, "latency_ms": int((self.clock() - t0) * 1000)})
            remaining_usd = _remaining_usd(headers) if status else remaining_usd

            content, finish = "", None
            if status == 200 and body is not None:
                try:
                    choice = body["choices"][0]
                    content, finish = choice["message"].get("content") or "", choice.get("finish_reason")
                except (KeyError, IndexError, TypeError, AttributeError):
                    content = ""
            if status == 200 and content.strip():
                usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                return ChatResult(
                    ok=True, content=content, status=200, finish_reason=finish,
                    usage={k: usage[k] for k in ("prompt_tokens", "completion_tokens", "cost") if k in usage},
                    remaining_usd=remaining_usd, latency_ms=int((self.clock() - started) * 1000), attempts=attempts,
                )

            if status == 0:
                reason, retryable = err or "network", err == "network"
            elif status == 200:
                reason, retryable = "empty_response", True
            else:
                reason, retryable = _reason_for_status(status), status in RETRY_STATUSES
            last = ChatResult(
                ok=False, reason=reason, status=status, finish_reason=finish, remaining_usd=remaining_usd,
                latency_ms=int((self.clock() - started) * 1000), attempts=attempts,
            )
            log.warning("llm gagal: status=%s reason=%s attempt=%s", status, reason, n + 1)
            if not retryable or n == 1:
                break
            wait = _retry_after(headers) if status == 429 else None
            await self.sleep(min(wait if wait is not None else 1.0 + random.random(), MAX_RETRY_WAIT))
        last.latency_ms = int((self.clock() - started) * 1000)
        last.attempts = attempts
        last.remaining_usd = remaining_usd
        return last

    async def stream_chat(
        self, messages: list[dict], *, idle_timeout: float = 120.0, total_timeout: float = 900.0
    ) -> ChatResult:
        """Panggilan streaming untuk keluaran panjang (perekaman Replay). Tanpa ulangan otomatis: pemanggil mengulang.

        Berhasil hanya bila finish_reason == 'stop'. Gagal: timeout (total), idle_timeout (jeda antar data),
        truncated (length), stream_interrupted (terputus di tengah), serta status HTTP seperti pada chat().
        """
        payload = {**self.payload(messages), "stream": True, "stream_options": {"include_usage": True}}
        headers = {"Authorization": f"Bearer {self.cfg.api_key}", "Content-Type": "application/json"}
        started = self.clock()
        st: dict = {"status": 0, "parts": [], "finish": None, "usage": {}, "remaining": None, "reason": None}

        async def consume() -> None:
            async with httpx.AsyncClient(transport=self.transport) as client:
                async with client.stream("POST", f"{self.cfg.base_url}/chat/completions", json=payload,
                                         headers=headers, timeout=None) as resp:
                    st["status"], st["remaining"] = resp.status_code, _remaining_usd(resp.headers)
                    if resp.status_code != 200:
                        await resp.aread()
                        return
                    lines = resp.aiter_lines().__aiter__()
                    while True:
                        try:
                            line = await asyncio.wait_for(lines.__anext__(), idle_timeout)
                        except StopAsyncIteration:
                            return
                        except asyncio.TimeoutError:
                            st["reason"] = "idle_timeout"
                            return
                        for kind, text in parse_sse_line(line):
                            if kind == "content":
                                st["parts"].append(text)
                            elif kind == "finish":
                                st["finish"] = text
                            elif kind == "usage":
                                u = json.loads(text)
                                keys = ("prompt_tokens", "completion_tokens", "cost")
                                st["usage"] = {k: u[k] for k in keys if k in u}

        try:
            await asyncio.wait_for(consume(), total_timeout)
        except asyncio.TimeoutError:
            st["reason"] = "timeout"
        except httpx.TransportError:
            st["reason"] = "stream_interrupted" if st["parts"] else "network"
        content = "".join(st["parts"])
        ms = int((self.clock() - started) * 1000)
        reason = st["reason"]
        if reason is None:
            if st["status"] != 200:
                reason = _reason_for_status(st["status"])
            elif st["finish"] == "length":
                reason = "truncated"
            elif st["finish"] != "stop" or not content.strip():
                reason = "stream_interrupted" if content else "empty_response"
        return ChatResult(
            ok=reason is None, content=content, reason=reason, status=st["status"], finish_reason=st["finish"],
            usage=st["usage"], remaining_usd=st["remaining"], latency_ms=ms,
            attempts=[{"status": st["status"], "latency_ms": ms}],
        )
