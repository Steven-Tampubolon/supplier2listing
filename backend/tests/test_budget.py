from app.budget import Budget


class Clock:
    def __init__(self, t=1_760_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_daily_limit_then_reset_next_utc_day():
    c = Clock()
    b = Budget(max_day=2, max_ip_hour=99, clock=c)
    assert b.left() == 2
    assert b.reserve("a").ok and b.reserve("b").ok
    d = b.reserve("c")
    assert not d.ok and d.reason == "day" and "Replay" in d.message and b.left() == 0
    c.t += 86400
    assert b.left() == 2 and b.reserve("a").ok


def test_per_ip_limit_is_independent_and_expires():
    c = Clock()
    b = Budget(max_day=99, max_ip_hour=2, clock=c)
    assert b.reserve("1.1.1.1").ok and b.reserve("1.1.1.1").ok
    d = b.reserve("1.1.1.1")
    assert not d.ok and d.reason == "ip" and 0 < d.retry_after <= 3600 and "menit" in d.message
    assert b.reserve("2.2.2.2").ok  # IP lain tidak terpengaruh
    c.t += 3601
    assert b.reserve("1.1.1.1").ok


def test_low_account_quota_blocks_live_even_with_budget_left():
    b = Budget(max_day=500, min_remaining_usd=0.10)
    b.observe(0.05)
    d = b.reserve("x")
    assert not d.ok and d.reason == "quota" and b.left() == 0
    b.observe(0.80)
    assert b.reserve("x").ok


def test_observe_ignores_unknown_values():
    b = Budget()
    b.observe(None)
    assert b.remaining_usd is None
    b.observe(0.5)
    b.observe(None)
    assert b.remaining_usd == 0.5


def test_denied_reservations_do_not_consume_budget():
    b = Budget(max_day=10, max_ip_hour=1)
    assert b.reserve("a").ok
    for _ in range(5):
        assert not b.reserve("a").ok
    assert b.used_today == 1
