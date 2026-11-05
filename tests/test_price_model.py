import math
import random
from datetime import date

import pytest

from forecastlab.price_model import (
    ABOVE, BELOW, TOUCH_DOWN, TOUCH_UP,
    barrier_hit_probability, parse_question, price_model_probability,
    realized_daily_vol, terminal_above_probability,
)

D = date(2026, 10, 28)


def test_parse_dip_week_window():
    q = parse_question("Will Bitcoin dip to $78,000 October 26-November 1?", "", D)
    assert (q.asset, q.kind, q.threshold) == ("BTC", TOUCH_DOWN, 78000.0)
    assert (q.window_start, q.window_end) == (date(2026, 10, 26), date(2026, 11, 1))


def test_parse_reach_same_month_range():
    q = parse_question("Will Ethereum reach $2,500 October 5-11?", "", date(2026, 10, 7))
    assert (q.asset, q.kind, q.window_start, q.window_end) == ("ETH", TOUCH_UP, date(2026, 10, 5), date(2026, 10, 11))


def test_parse_hit_high_low_month():
    q = parse_question("Will WTI Crude Oil (WTI) hit (HIGH) $100 in October?", "", date(2026, 10, 12))
    assert (q.asset, q.kind, q.threshold) == ("WTI", TOUCH_UP, 100.0)
    assert (q.window_start, q.window_end) == (date(2026, 10, 1), date(2026, 10, 31))
    q = parse_question("Will Gold (XAUUSD) hit (LOW) $3,900 in October?", "", date(2026, 10, 21))
    assert (q.asset, q.kind, q.threshold) == ("GOLD", TOUCH_DOWN, 3900.0)


def test_parse_above_on_date():
    q = parse_question("Will the price of Ethereum be above $2,200 on November 4?", "", date(2026, 11, 2))
    assert (q.asset, q.kind, q.threshold, q.window_end) == ("ETH", ABOVE, 2200.0, date(2026, 11, 4))
    assert q.window_start == q.window_end


def test_parse_yield_and_decimal_threshold():
    q = parse_question("Will the 10-year U.S. Treasury yield on Oct 30, 2026 be above 5.00%?", "", date(2026, 10, 27))
    assert (q.asset, q.kind, q.threshold, q.window_end) == ("DGS10", ABOVE, 5.0, date(2026, 10, 30))
    q = parse_question("Will XRP reach $1.80 in October?", "", date(2026, 10, 13))
    assert q.threshold == 1.8


def test_parse_verbless_title_uses_detail():
    detail = "resolve to Yes if any Binance 1 minute candle for BTC/USDT during October 2026 has a final High price equal to or greater than $87,500.00"
    q = parse_question("Bitcoin $87,500 in October 2026?", detail, date(2026, 10, 12))
    assert (q.kind, q.window_end) == (TOUCH_UP, date(2026, 10, 31))


def test_year_rollover():
    q = parse_question("Will Bitcoin reach $120,000 in January?", "", date(2026, 12, 28))
    assert q.window_start == date(2027, 1, 1)


@pytest.mark.parametrize("title", [
    "Will oil be at least $85 on November 2 and the Democrats win the Senate?",
    "Will NVIDIA be the largest company in the world by market cap on October 31, 2026?",
    "Will U.S. inflation (CPI, year over year) for August 2026 come in above 3.4%?",
    "Will Bitcoin be the best performer?",
    "Will the 10-year yield hit 5.5% in October?",
])
def test_unparseable_returns_none(title):
    assert parse_question(title, "", D) is None


def test_barrier_matches_monte_carlo():
    sigma, t, spot = 0.03, 7.0, 100.0
    rng = random.Random(1)
    steps, n = 70, 4000
    dt = t / steps
    up_hits = down_hits = 0
    for _ in range(n):
        x, hi, lo = 0.0, 0.0, 0.0
        for _ in range(steps):
            x += -0.5 * sigma**2 * dt + sigma * math.sqrt(dt) * rng.gauss(0, 1)
            hi, lo = max(hi, x), min(lo, x)
        up_hits += hi >= math.log(1.08)
        down_hits += lo <= math.log(0.92)
    # discrete monitoring misses some crossings, so MC sits a bit below the continuous value
    assert barrier_hit_probability(spot, 108, sigma, t, True) == pytest.approx(up_hits / n, abs=0.05)
    assert barrier_hit_probability(spot, 92, sigma, t, False) == pytest.approx(down_hits / n, abs=0.05)


def test_barrier_edges_and_monotonicity():
    assert barrier_hit_probability(100, 99, 0.02, 3, True) == 1.0
    assert barrier_hit_probability(100, 101, 0.02, 3, False) == 1.0
    assert barrier_hit_probability(100, 110, 0.0, 3, True) == 0.0
    a = barrier_hit_probability(100, 105, 0.02, 2, True)
    b = barrier_hit_probability(100, 105, 0.02, 4, True)
    assert 0 < a < b < 1
    assert barrier_hit_probability(100, 105, 0.02, 3, True) > terminal_above_probability(100, 105, 0.02, 3)


def test_terminal():
    assert terminal_above_probability(100, 100, 0.02, 5) == pytest.approx(0.5, abs=0.02)
    assert terminal_above_probability(100, 80, 0.02, 1) > 0.99
    assert terminal_above_probability(100, 120, 0.02, 1) < 0.01


def test_realized_vol():
    closes = [100 * math.exp(0.01 * ((-1) ** i)) ** (i % 2 + 1) for i in range(40)]
    assert realized_daily_vol(closes) > 0
    assert realized_daily_vol([100.0] * 31) == 0.0
    assert realized_daily_vol([100.0] * 5) is None


def test_probability_uses_elapsed_window_extremes():
    q = parse_question("Will Bitcoin reach $90,000 in October?", "", date(2026, 10, 12))
    p_hit = price_model_probability(q, 80000, date(2026, 10, 11), 0.03, highs_so_far=[91000], lows_so_far=[])
    assert p_hit == 1.0
    p = price_model_probability(q, 80000, date(2026, 10, 11), 0.03, highs_so_far=[85000], lows_so_far=[])
    assert 0.1 < p < 0.9


def test_probability_below_is_complement_and_future_window():
    q = parse_question("Will the price of Bitcoin be above $80,000 on October 30?", "", date(2026, 10, 28))
    above = price_model_probability(q, 80000, date(2026, 10, 27), 0.03)
    qb = q.__class__(q.asset, BELOW, q.threshold, q.window_start, q.window_end)
    assert above + price_model_probability(qb, 80000, date(2026, 10, 27), 0.03) == pytest.approx(1.0)
    fut = parse_question("Will Bitcoin reach $85,000 October 31-November 6?", "", date(2026, 10, 28))
    now = parse_question("Will Bitcoin reach $85,000 October 28-November 6?", "", date(2026, 10, 28))
    pf = price_model_probability(fut, 80000, date(2026, 10, 27), 0.03)
    pn = price_model_probability(now, 80000, date(2026, 10, 27), 0.03)
    assert 0 < pf < pn < 1
