"""Penjaga anggaran mode Live: batas harian, batas per IP per jam, dan sisa kuota akun dari header API.

Catatan: backend serverless dapat memiliki banyak instance; penghitung ini ada di memori per instance, jadi
batasnya bersifat upaya terbaik. Sisa kuota akun (dari header respons AMD) mencerminkan keadaan bersama.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_TRACKED_IPS = 5000


@dataclass
class Decision:
    ok: bool
    reason: str | None = None  # 'day' | 'ip' | 'quota'
    message: str = ""
    retry_after: int = 0


class Budget:
    def __init__(self, max_day: int = 500, max_ip_hour: int = 40, min_remaining_usd: float = 0.10, clock=time.time):
        self.max_day, self.max_ip_hour, self.min_remaining_usd = max_day, max_ip_hour, min_remaining_usd
        self.clock = clock
        self.day_key = self._day()
        self.used_today = 0
        self.ip_hits: dict[str, deque[float]] = {}
        self.remaining_usd: float | None = None

    def _day(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%d")

    def _roll(self) -> None:
        today = self._day()
        if today != self.day_key:
            self.day_key, self.used_today = today, 0

    def _quota_low(self) -> bool:
        return self.remaining_usd is not None and self.remaining_usd < self.min_remaining_usd

    def left(self) -> int:
        self._roll()
        if self._quota_low():
            return 0
        return max(0, self.max_day - self.used_today)

    def observe(self, remaining_usd: float | None) -> None:
        """Catat sisa kuota harian akun dari header respons terakhir."""
        if remaining_usd is not None:
            self.remaining_usd = remaining_usd

    def reserve(self, ip: str) -> Decision:
        self._roll()
        now = self.clock()
        if self._quota_low():
            return Decision(False, "quota", "Kuota AI harian akun hampir habis. Gunakan mode Replay atau Aturan.")
        if self.used_today >= self.max_day:
            return Decision(False, "day", "Batas harian mode Live sudah tercapai. Gunakan mode Replay atau Aturan.")
        hits = self.ip_hits.setdefault(ip, deque())
        while hits and now - hits[0] >= 3600:
            hits.popleft()
        if len(hits) >= self.max_ip_hour:
            wait = max(1, int(3600 - (now - hits[0])))
            minutes = max(1, round(wait / 60))
            return Decision(
                False, "ip",
                f"Anda sudah memakai {self.max_ip_hour} potongan Live dalam satu jam. "
                f"Coba lagi sekitar {minutes} menit lagi, atau gunakan mode Replay atau Aturan.",
                retry_after=wait,
            )
        hits.append(now)
        self.used_today += 1
        if len(self.ip_hits) > MAX_TRACKED_IPS:
            self.ip_hits = {k: v for k, v in self.ip_hits.items() if v and now - v[-1] < 3600}
        return Decision(True)
