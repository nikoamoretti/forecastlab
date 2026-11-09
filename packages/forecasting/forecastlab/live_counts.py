"""Running tallies for count/total questions and a price-math reference line, for the research prompts and the digest.

Count/total questions ("how many posts", "go down 5-7 times", "commitments exceed $20M", "between 400 and 450") are
settled by a running count on one named page. The researcher must read that count and report it as a structured
``tally``; the digest shows it first with a straight-line projection to the deadline. For asset-price questions the
researcher reports the latest spot price and 30-day volatility as ``spot`` and the digest adds one reference line from
``price_model``. No prediction-market price or odds are used anywhere here.

Standard library only (the Mac may run Python 3.9).
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc  # noqa: UP017 -- datetime.UTC needs Python 3.11; the Mac may run 3.9

COUNT_NOUN = r"(?:posts?|tweets?|retweets?|outages?|times|incidents?|launches|strikes|attacks|games|goals|deaths|casualties|downloads|signatures|signers|mentions)"
MONEY = r"\$\s?\d[\d,.]*\s?(?:k|m|b|t|thousand|million|billion|trillion)?\b"
TOTAL_NOUN = (r"(?:commitments?|raised|raise|total|sales?|funding|deposits?|donations?|pledges?|volume|tvl|"
              r"revenue|contributions?|subscribers?|users)")
COUNT_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"\bhow many\b",
    r"\bnumber of\b",
    rf"\b\d[\d,]*\s*(?:-|–|to)\s*\d[\d,]*\s+{COUNT_NOUN}\b",
    rf"\b(?:at least|at most|more than|fewer than|less than|exactly|over|under|exceed|exceeds)\s+\d[\d,]*\s+{COUNT_NOUN}\b",
    rf"\b(?:post|posts|tweet|tweets|go down|goes down|be down|have)\b.{{0,40}}\b\d[\d,]*\s*(?:-|–|to)\s*\d[\d,]*\s+{COUNT_NOUN}\b",
    r"\bbetween\s+\$?\d[\d,.]*\s*(?:k|m|b)?\s+and\s+\$?\d[\d,.]*",
    rf"\b(?:exceed|exceeds|surpass|top|reach|at least|more than|over)\b[^.?]{{0,40}}{MONEY}",
    r"\b(?:posts?|tweets?)\b.{0,60}\b(?:between|at least|more than|fewer than|exceed)\b",
)]
# A money threshold alone ("exceed $100") is not a count question; it needs a total-like noun with it.
MONEY_PATTERN = COUNT_PATTERNS[6]
TOTAL_WORDS = re.compile(rf"\b{TOTAL_NOUN}\b", re.IGNORECASE)

SUFFIX = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9, "t": 1e12, "trillion": 1e12}
NUMBER = re.compile(r"\$?\s?(\d[\d,]*(?:\.\d+)?)\s?(thousand|million|billion|trillion|k|m|b|t)?\b", re.IGNORECASE)


def is_price_question(task: dict[str, str], today: str) -> bool:
    return parse_price(task, today) is not None


def parse_price(task: dict[str, str], today: str) -> Any:
    """The price_model parse of an asset-price question, or None (also None if the model cannot be imported)."""
    try:
        from forecastlab.price_model import parse_question
        day = datetime.strptime(today[:10], "%Y-%m-%d").date()
        return parse_question(task.get("question", ""), task.get("criteria", ""), day)
    except Exception:  # noqa: BLE001 -- the digest and the prompts must never break on this
        return None


def is_count_question(task: dict[str, str], today: str = "") -> bool:
    """True for questions settled by a running count or total (posts, outages, amount raised), not asset prices."""
    title, detail = task.get("question", ""), task.get("criteria", "")
    if today and parse_price(task, today) is not None:
        return False
    text = f"{title} {detail}"
    for pattern in COUNT_PATTERNS:
        if pattern.search(title) and (pattern is not MONEY_PATTERN or TOTAL_WORDS.search(text)):
            return True
    return any(p.search(detail) for p in COUNT_PATTERNS[:6]) and bool(
        re.search(rf"\b{COUNT_NOUN}\b|\b{TOTAL_NOUN}\b|how many|number of", title, re.IGNORECASE))


def to_number(text: str) -> float | None:
    found = NUMBER.fullmatch(text.strip())
    if not found:
        return None
    value = float(found.group(1).replace(",", ""))
    return value * SUFFIX.get((found.group(2) or "").lower(), 1.0)


def thresholds(task: dict[str, str]) -> list[float]:
    """The numbers a count question is about, from its title (or, failing that, its resolution text), e.g. 5 and 7."""
    for text in (task.get("question", ""), task.get("criteria", "")):
        found: list[float] = []
        unit = r"(?:k|m|b|million|billion)?"
        both = rf"(\$?\s?\d[\d,.]*\s?{unit})"
        between = (re.search(rf"\bbetween\s+{both}\s*(?:-|–|to|and)\s*{both}\b", text, re.IGNORECASE)
                   or re.search(rf"\b{both}\s*(?:-|–|to)\s*{both}\s+{COUNT_NOUN}\b", text, re.IGNORECASE))
        if between:
            found = [n for n in (to_number(between.group(1).rstrip(".,")), to_number(between.group(2).rstrip(".,"))) if n is not None]
        if not found:
            single = re.search(r"\b(?:exceed|exceeds|surpass|top|reach|at least|more than|over|fewer than|less than|"
                               r"at most|under|above|below)\s+(\$?\s?\d[\d,]*(?:\.\d+)?\s?(?:thousand|million|billion|trillion|k|m|b|t)?)\b",
                               text, re.IGNORECASE)
            if single:
                number = to_number(single.group(1))
                found = [number] if number is not None else []
        if found:
            return sorted(set(found))
    return []


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = to_number(value.replace("US", ""))
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _stamp(value: Any) -> str:
    return " ".join(value.split())[:40] if isinstance(value, str) else ""


def clean_tally(raw: Any, is_bad_url: Any) -> dict[str, Any] | None:
    """A reported tally as {value, unit, source_url, read_at, earlier_value?, earlier_at?}, or None if unusable."""
    if not isinstance(raw, dict):
        return None
    value, url = _num(raw.get("value")), raw.get("source_url")
    if value is None or value < 0 or not isinstance(url, str) or not url.startswith(("http://", "https://")) or is_bad_url(url):
        return None
    out: dict[str, Any] = {"value": value, "unit": _stamp(raw.get("unit")), "source_url": url[:500],
                           "read_at": _stamp(raw.get("read_at"))}
    earlier, earlier_at = _num(raw.get("earlier_value")), _stamp(raw.get("earlier_at"))
    if earlier is not None and earlier_at:
        out.update(earlier_value=earlier, earlier_at=earlier_at)
    return out


def clean_spot(raw: Any, is_bad_url: Any) -> dict[str, Any] | None:
    """A reported spot as {price, read_at, source_url, daily_vol?}; daily_vol is the 30-day stdev of daily log returns."""
    if not isinstance(raw, dict):
        return None
    price, url = _num(raw.get("price")), raw.get("source_url")
    if price is None or price <= 0 or not isinstance(url, str) or not url.startswith(("http://", "https://")) or is_bad_url(url):
        return None
    out: dict[str, Any] = {"asset": _stamp(raw.get("asset")), "price": price, "source_url": url[:500],
                           "read_at": _stamp(raw.get("read_at"))}
    vol = _num(raw.get("daily_vol_30d"))
    if vol is not None and vol > 0.5:  # reported as a percent
        vol /= 100.0
    if vol is not None and 0.0005 <= vol <= 0.5:
        out["daily_vol"] = vol
    return out


def pick_latest(parts: list[Any]) -> Any:
    """The entry read last (by read_at where it parses), later passes winning ties; None if there are none."""
    found = [p for p in parts if p]
    if not found:
        return None
    floor = datetime.min.replace(tzinfo=UTC)
    return max(enumerate(found), key=lambda row: (when(row[1].get("read_at", "")) or floor, row[0]))[1]


def when(text: str) -> datetime | None:
    """A reported timestamp as an aware UTC datetime, or None."""
    value = re.sub(r"\s*(UTC|GMT)\s*$", "", (text or "").strip(), flags=re.IGNORECASE).replace("Z", "+00:00")
    for candidate in (value, value.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def fmt(value: float) -> str:
    for size, name in ((1e9, "B"), (1e6, "M")):  # 1.0M and up
        if abs(value) >= size:
            return f"{value / size:,.1f}{name}"
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}".rstrip("0").rstrip(".")


NO_TALLY = "no running tally read for a count/total question; not forecast"


def needs_tally(found: dict[str, Any], task: dict[str, str], today: str) -> bool:
    """True for a count/total question whose research did not read the live number: it must not be forecast."""
    return is_count_question(task, today) and not found.get("tally")


def tally_block(found: dict[str, Any], task: dict[str, str], today: str) -> str:
    """The top-of-digest block for a count question: the running tally, a projection, and the rate each threshold needs."""
    tally = found.get("tally")
    if not tally:
        return ("WARNING: no running tally found. This looks like a count/total question, but the research did not "
                "read the current count or total from the page that settles it. Do not forecast from a typical "
                "rate alone: treat any estimate as weakly grounded.")
    unit = f" {tally['unit']}" if tally.get("unit") else ""
    read = when(tally.get("read_at", ""))
    lines = [f"LIVE TALLY: {fmt(tally['value'])}{unit}, read {tally.get('read_at') or 'at an unstated time'} "
             f"from {tally['source_url']}"]
    now = datetime.strptime(today[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    if read and (now - read) > timedelta(days=2):
        lines.append(f"WARNING: this reading is {(now - read).days} days old; the count has moved since.")
    rate = None
    earlier = when(tally.get("earlier_at", "")) if "earlier_value" in tally else None
    if earlier and read and read > earlier:
        days = (read - earlier).total_seconds() / 86400
        rate = (tally["value"] - tally["earlier_value"]) / days
        lines.append(f"Earlier point: {fmt(tally['earlier_value'])}{unit} at {tally['earlier_at']} -> rate "
                     f"{fmt(rate)}{unit} per day over {days:.1f} days.")
    elif "earlier_value" in tally:
        lines.append("Earlier point reported but its time could not be read; no rate computed.")
    else:
        lines.append("No earlier point reported, so no rate.")
    deadline = task.get("resolves_on") or ""
    left = None
    if read and DATE.match(deadline[:10]):
        end = datetime.strptime(deadline[:10], "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1)
        left = max((end - read).total_seconds() / 86400, 0.0)
        lines.append(f"Deadline {deadline[:10]}: {left:.1f} days left from the reading.")
    elif deadline:
        lines.append(f"Deadline {deadline[:10]}; time left could not be worked out.")
    if rate is not None and left is not None:
        lines.append(f"Projection at the current rate (straight line; ignores bursts, slowdowns and a rate that is "
                     f"changing): {fmt(tally['value'] + rate * left)}{unit} by the deadline.")
    for level in thresholds(task):
        if tally["value"] >= level:
            lines.append(f"Threshold {fmt(level)}: already reached by this reading.")
        elif left is not None and left > 0:
            need = (level - tally["value"]) / left
            vs = f" (current rate {fmt(rate)}/day)" if rate is not None else ""
            lines.append(f"Threshold {fmt(level)}: needs {fmt(level - tally['value'])} more, i.e. {fmt(need)}{unit} "
                         f"per day{vs}.")
        else:
            lines.append(f"Threshold {fmt(level)}: needs {fmt(level - tally['value'])} more.")
    return "\n".join(lines)


DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRICE_LINE = "Price math (thin-tailed, tends to underrate sudden dips)"


def price_line(found: dict[str, Any], task: dict[str, str], today: str) -> str:
    """One reference line from the lognormal price model, or '' when the question or the inputs do not allow it."""
    spot = found.get("spot")
    if not spot or not spot.get("daily_vol"):
        return ""
    try:
        from forecastlab.price_model import price_model_probability
        parsed = parse_price(task, today)
        if parsed is None:
            return ""
        read = when(spot.get("read_at", ""))
        spot_day = (read or datetime.strptime(today[:10], "%Y-%m-%d").replace(tzinfo=UTC)).date()
        p = price_model_probability(parsed, spot["price"], spot_day, spot["daily_vol"])
    except Exception:  # noqa: BLE001 -- a reference line must never break the digest
        return ""
    return (f"{PRICE_LINE}: {p:.0%}. Reference only, not the call: spot {fmt(spot['price'])} read "
            f"{spot.get('read_at') or 'at an unstated time'}, 30-day daily volatility {spot['daily_vol']:.2%}.")


def digest_head(found: dict[str, Any], task: dict[str, str], today: str) -> list[str]:
    """Lines for the top of one question's digest ([] for questions that are neither counts nor priced assets)."""
    if is_count_question(task, today):
        return [tally_block(found, task, today)]
    line = price_line(found, task, today)
    return [line] if line else []


# ---- prompts -----------------------------------------------------------------

TALLY_JSON = (',\n "tally": {"value": <the number now, as a plain number>, "unit": "what is counted", '
              '"source_url": "the page you read it from", "read_at": "YYYY-MM-DDTHH:MM UTC, when you read it", '
              '"earlier_value": <the count at an earlier point, or null>, "earlier_at": "its YYYY-MM-DDTHH:MM UTC '
              'time, or null"}, or null if you could not read one')
SPOT_JSON = (',\n "spot": {"asset": "...", "price": <latest spot price, plain number>, "source_url": "...", '
             '"read_at": "YYYY-MM-DDTHH:MM UTC, the price\'s timestamp", "daily_vol_30d": <standard deviation of daily '
             'log returns of closing prices over the last 30 days, a decimal such as 0.025>}, or null if you could not read one')
TALLY_RULES = (
    "THIS IS A COUNT/TOTAL QUESTION. Before anything else: find the resolution source named in the question "
    "and its resolution text (the exact page, account, dashboard or official tracker that settles it), open it, "
    "and read the current running count or total. Report it in the `tally` field with the number, that page's URL "
    "and the time you read it. If the page or its history shows the count at an earlier point (an earlier dated "
    "post, archive copy, daily figure or snapshot), also give that count and its time so a rate can be computed. "
    "Do not substitute an article's summary or a typical rate for the count itself. If you truly cannot read the "
    "count, set `tally` to null and say why under uncertainties.\n\n"
)
SPOT_RULES = (
    "THIS IS AN ASSET-PRICE QUESTION. Report in the `spot` field the latest spot price of the asset with its "
    "timestamp and source URL (an exchange, index provider or official data page), and the standard deviation of "
    "its daily log returns over the last 30 days if you can compute it from daily closes. This is the price of the "
    "asset itself.\n\n"
)


def live_rules(task: dict[str, str], today: str) -> tuple[str, str]:
    """(instructions, extra JSON fields) for this question's prompts; empty strings for an ordinary question."""
    if is_count_question(task, today):
        return TALLY_RULES, TALLY_JSON
    if parse_price(task, today) is not None:
        return SPOT_RULES, SPOT_JSON
    return "", ""
