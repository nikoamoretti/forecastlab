"""Price-math model for asset-price-threshold questions (backtest only).

Parses questions such as "Will Bitcoin dip to $78,000 October 26-November 1?",
"Will WTI Crude Oil (WTI) hit (HIGH) $100 in October?" or "Will the price of
Ethereum be above $2,200 on November 4?" and prices them under a martingale
lognormal model (log-price drift of -sigma^2/2):

* "hit / dip / reach at any time" questions: barrier-hit probability by the
  reflection principle;
* "above / below on a date" questions: terminal probability.

Inputs are the spot price at the forecast time and realized volatility from
daily closes dated before the forecast day. No market price or odds are used.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

CRYPTO = "crypto"
WEEKDAY = "weekday"

# asset key -> (regex on the title, calendar kind)
ASSETS = {
    "BTC": (r"\b(bitcoin|btc)\b", CRYPTO),
    "ETH": (r"\b(ethereum|eth)\b", CRYPTO),
    "SOL": (r"\b(solana|sol)\b", CRYPTO),
    "XRP": (r"\bxrp\b", CRYPTO),
    "WTI": (r"\b(wti|crude oil)\b", WEEKDAY),
    "GOLD": (r"\b(gold|xauusd)\b", WEEKDAY),
    "SILVER": (r"\b(silver|xagusd)\b", WEEKDAY),
    "NVDA": (r"\b(nvidia|nvda)\b", WEEKDAY),
    "SPY": (r"\b(s&p 500|spy)\b", WEEKDAY),
    "DGS10": (r"10-year\b.*\byield|\byield\b.*10-year", WEEKDAY),
}

TOUCH_UP, TOUCH_DOWN, ABOVE, BELOW = "touch_up", "touch_down", "above", "below"

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


def _month(token: str) -> int:
    t = token.lower()
    for name, n in _MONTHS.items():
        if name.startswith(t[:3]):
            return n
    raise ValueError(token)


@dataclass(frozen=True)
class ParsedQuestion:
    asset: str
    kind: str  # touch_up | touch_down | above | below
    threshold: float
    window_start: date  # equals window_end for "on date" questions
    window_end: date

    @property
    def calendar(self) -> str:
        return ASSETS[self.asset][1]

    @property
    def is_touch(self) -> bool:
        return self.kind in (TOUCH_UP, TOUCH_DOWN)


def _year_for(month: int, day: int, forecast_day: date, explicit: Optional[int]) -> date:
    if explicit:
        return date(explicit, month, day)
    for y in (forecast_day.year, forecast_day.year + 1, forecast_day.year - 1):
        d = _safe_date(y, month, day)
        if d and d >= forecast_day - timedelta(days=40):
            return d
    return _safe_date(forecast_day.year, month, day)


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _month_end(y: int, m: int) -> date:
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _parse_threshold(title: str, asset: str) -> Optional[float]:
    if asset == "DGS10":
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", title)
        return float(m.group(1)) if m else None
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", title)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_kind(title: str, detail: str = "") -> Optional[str]:
    kind = _parse_kind_title(title)
    if kind is None and re.search(r"any .{0,40}candle", detail, re.I):
        # untitled-verb markets, e.g. "Bitcoin $87,500 in October 2026?"
        if re.search(r"\bhigh\b[^.]{0,40}(equal to or )?(greater|higher)", detail, re.I):
            return TOUCH_UP
        if re.search(r"\blow\b[^.]{0,40}(equal to or )?(lower|less)", detail, re.I):
            return TOUCH_DOWN
    return kind


def _parse_kind_title(title: str) -> Optional[str]:
    t = title.lower()
    if re.search(r"\bdip to\b|\bfall to\b|\bdrop to\b|\(low\)", t):
        return TOUCH_DOWN
    if re.search(r"\breach\b|\(high\)", t):
        return TOUCH_UP
    if re.search(r"\babove\b|\bat least\b|\bover\b", t):
        return ABOVE
    if re.search(r"\bbelow\b|\bunder\b|\bat most\b", t):
        return BELOW
    return None


def _parse_window(title: str, detail: str, forecast_day: date, is_touch: bool):
    t = title
    ey = re.search(r"\b(20\d\d)\b", t)
    explicit = int(ey.group(1)) if ey else None
    # "October 26-November 1" / "October 5-11"
    m = re.search(_MON + r"\.?\s+(\d{1,2})\s*[-–]\s*(?:" + _MON + r"\.?\s+)?(\d{1,2})\b", t, re.I)
    if m:
        m1, d1, m2, d2 = _month(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
        m2 = _month(m2) if m2 else m1
        end = _year_for(m2, d2, forecast_day, explicit)
        if end is None:
            return None
        start = _safe_date(end.year - (m1 > m2), m1, d1)
        return (start, end) if start else None
    # "in October" (touch questions over a calendar month)
    m = re.search(r"\bin\s+" + _MON + r"\b(?!\s+\d{1,2}\b)", t, re.I)
    if m and is_touch:
        mo = _month(m.group(1))
        ref = _year_for(mo, 1, forecast_day, explicit)
        if ref is None:
            return None
        return ref.replace(day=1), _month_end(ref.year, mo)
    # "on November 4" / "on Oct 30, 2026"
    m = re.search(r"\bon\s+" + _MON + r"\.?\s+(\d{1,2})\b", t, re.I)
    if m and not is_touch:
        d = _year_for(_month(m.group(1)), int(m.group(2)), forecast_day, explicit)
        return (d, d) if d else None
    return None


def parse_question(title: str, detail: str, forecast_day: date) -> Optional[ParsedQuestion]:
    """Parse an asset-price question, or return None if it is not one we price."""
    title = (title or "").strip()
    if not title or re.search(r"\band\b|\bor\b", title, re.I):  # compound questions
        return None
    asset = None
    for key, (pattern, _cal) in ASSETS.items():
        if re.search(pattern, title, re.I):
            asset = key
            break
    if asset is None:
        return None
    kind = _parse_kind(title, detail or "")
    if kind is None:
        return None
    if asset == "DGS10" and kind not in (ABOVE, BELOW):
        return None
    threshold = _parse_threshold(title, asset)
    if threshold is None or threshold <= 0:
        return None
    window = _parse_window(title, detail or "", forecast_day, kind in (TOUCH_UP, TOUCH_DOWN))
    if window is None:
        return None
    start, end = window
    if end < forecast_day:
        return None
    return ParsedQuestion(asset, kind, threshold, start, end)


# ---- math ---------------------------------------------------------------

def _phi(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def barrier_hit_probability(spot: float, barrier: float, sigma: float, t: float, up: bool) -> float:
    """P(path touches the barrier within t) for a martingale lognormal price.

    sigma is volatility per unit time, t the horizon in the same unit.
    """
    if spot <= 0 or barrier <= 0:
        raise ValueError("prices must be positive")
    if (up and barrier <= spot) or (not up and barrier >= spot):
        return 1.0
    if sigma <= 0 or t <= 0:
        return 0.0
    nu = -0.5 * sigma * sigma
    b = math.log(barrier / spot)
    if not up:  # mirror: down-touch of S is up-touch of 1/S, whose log drift is -nu
        b, nu = -b, -nu
    s = sigma * math.sqrt(t)
    p = _phi((-b + nu * t) / s)
    expo = 2.0 * nu * b / (sigma * sigma)
    if expo < 700:
        p += math.exp(expo) * _phi((-b - nu * t) / s)
    return min(1.0, max(0.0, p))


def terminal_above_probability(spot: float, strike: float, sigma: float, t: float) -> float:
    """P(S_T > strike) for a martingale lognormal price."""
    if spot <= 0 or strike <= 0:
        raise ValueError("prices must be positive")
    if sigma <= 0 or t <= 0:
        return 1.0 if spot > strike else 0.0
    nu = -0.5 * sigma * sigma
    return _phi((math.log(spot / strike) + nu * t) / (sigma * math.sqrt(t)))


def realized_daily_vol(closes: Sequence[float], window: int = 30) -> Optional[float]:
    """Sample stdev of daily log returns over the last `window` closes."""
    xs = [c for c in closes if c and c > 0][-(window + 1):]
    if len(xs) < 11:
        return None
    r = [math.log(b / a) for a, b in zip(xs, xs[1:])]
    mean = sum(r) / len(r)
    return math.sqrt(sum((x - mean) ** 2 for x in r) / (len(r) - 1))


def _weekdays(after: date, upto: date) -> int:
    """Weekdays in (after, upto]."""
    n, d = 0, after + timedelta(days=1)
    while d <= upto:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def horizon(calendar: str, spot_date: date, end: date, terminal: bool) -> float:
    """Time from the spot close (end of spot_date) to the question's end, in vol units.

    Crypto trades every day: units are calendar days; the window ends 23:59 ET
    (about 5h after midnight UTC) and "on date" questions read the noon-ET
    (16:00 UTC) candle. Other assets: units are weekdays.
    """
    if calendar == CRYPTO:
        days = (end - spot_date).days
        return max(days - 0.33, 0.01) if terminal else days + 0.2
    return float(_weekdays(spot_date, end))


def price_model_probability(
    q: ParsedQuestion,
    spot: float,
    spot_date: date,
    daily_vol: float,
    highs_so_far: Sequence[float] = (),
    lows_so_far: Sequence[float] = (),
) -> float:
    """Probability of yes. highs/lows_so_far are daily extremes from the window
    start up to spot_date (data before the forecast day only)."""
    if q.kind == TOUCH_UP and any(h >= q.threshold for h in highs_so_far):
        return 1.0
    if q.kind == TOUCH_DOWN and any(l <= q.threshold for l in lows_so_far):
        return 1.0
    if q.is_touch:
        up = q.kind == TOUCH_UP
        t_total = horizon(q.calendar, spot_date, q.window_end, False)
        # Part of the window may lie in the future: the barrier only counts
        # once the window opens, so integrate over the price at that time.
        gap = (q.window_start - spot_date).days - 1
        if gap <= 0:
            return barrier_hit_probability(spot, q.threshold, daily_vol, t_total, up)
        t_gap = horizon(q.calendar, spot_date, q.window_start - timedelta(days=1), False)
        t_in = max(t_total - t_gap, 0.01)
        if t_gap <= 0:
            return barrier_hit_probability(spot, q.threshold, daily_vol, t_total, up)
        s = daily_vol * math.sqrt(t_gap)
        total, wsum = 0.0, 0.0
        for i in range(-20, 21):
            z = i * 0.15
            w = math.exp(-0.5 * z * z)
            s0 = spot * math.exp(-0.5 * daily_vol ** 2 * t_gap + s * z)
            total += w * barrier_hit_probability(s0, q.threshold, daily_vol, t_in, up)
            wsum += w
        return total / wsum
    t = horizon(q.calendar, spot_date, q.window_end, True)
    p_above = terminal_above_probability(spot, q.threshold, daily_vol, t)
    return p_above if q.kind == ABOVE else 1.0 - p_above
