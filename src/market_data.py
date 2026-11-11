"""Read-only, validated market-data adapters.

The financial ledger never depends on this module. Provider failures degrade to
an unavailable/partial market response and never become fabricated values.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

GOLD_URL = "https://api.goldprice.dev/v1/carat?currency=INR"
TIGZIG_URL = "https://api.tigzig.com/v1/series"
NEWS_URL = "https://news.google.com/rss/search?q=India%20finance%20OR%20RBI%20OR%20gold&hl=en-IN&gl=IN&ceid=IN:en"
GOLD_HISTORY_URL = "https://standardbullion.com/api/v1/market/history"
FX_HISTORY_URL = "https://api.frankfurter.dev/v2/rates"
CACHE_TTL_SECONDS = 900
TIMEOUT_SECONDS = 5

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


class MarketProviderError(RuntimeError):
    pass


def _fetch(url: str, *, accept: str) -> tuple[bytes, int]:
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "SpendWiseAI/1.0 market-reader"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read(2_000_000), int(response.status)
    except urllib.error.HTTPError as error:
        raise MarketProviderError(f"upstream_http_{error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise MarketProviderError("upstream_unreachable") from error


def _cached(key: str) -> dict[str, Any] | None:
    with _cache_lock:
        item = _cache.get(key)
        if item and time.monotonic() - item[0] < CACHE_TTL_SECONDS:
            return item[1]
    return None


def _store(key: str, value: dict[str, Any]) -> dict[str, Any]:
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise MarketProviderError("invalid_numeric_value") from error
    if not result.is_finite():
        raise MarketProviderError("invalid_numeric_value")
    return result


def _paise(value: Any) -> int:
    return int((_decimal(value) * 100).quantize(Decimal("1")))


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def gold_snapshot() -> dict[str, Any]:
    cached = _cached("gold")
    if cached:
        return cached
    raw, _ = _fetch(GOLD_URL, accept="application/json")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MarketProviderError("invalid_json") from error
    if not isinstance(payload, dict) or payload.get("currency") != "INR":
        raise MarketProviderError("invalid_gold_response")
    timestamp = _timestamp(payload.get("timestamp"))
    if timestamp is None:
        raise MarketProviderError("invalid_gold_timestamp")
    result = {
        "status": "available",
        "source": "GoldPrice.dev (spot-derived reference; not a jeweller retail quote)",
        "updated_at": timestamp,
        "purity": "24K/22K",
        "unit": "INR per gram",
        "value_paise": _paise(payload["price_gram_24k"]),
        "price_22k_paise": _paise(payload["price_gram_22k"]),
        "history": [],
        "history_status": "unavailable_from_current_endpoint",
    }
    return _store("gold", result)


def _date_range(range_name: str) -> tuple[str, str]:
    from datetime import date, timedelta

    days = {"7d": 7, "1m": 31, "3m": 93, "1y": 366}
    if range_name not in days:
        raise MarketProviderError("unsupported_gold_range")
    end = date.today()
    return (end - timedelta(days=days[range_name] - 1)).isoformat(), end.isoformat()


def gold_history(range_name: str = "1m") -> dict[str, Any]:
    """Convert dated USD/troy-ounce history to dated INR/gram observations.

    Conversion uses only an FX rate published for the same calendar date. This
    intentionally drops weekends/holidays without inventing or carrying values.
    """
    cache_key = f"gold_history:{range_name}"
    cached = _cached(cache_key)
    if cached:
        return cached
    start, end = _date_range(range_name)
    query = urllib.parse.urlencode({"metal": "XAU", "range": range_name})
    raw, _ = _fetch(f"{GOLD_HISTORY_URL}?{query}", accept="application/json")
    try:
        bullion = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MarketProviderError("invalid_gold_history_json") from error
    points = bullion.get("points") if isinstance(bullion, dict) else None
    if not isinstance(points, list) or not points:
        raise MarketProviderError("invalid_gold_history_response")
    fx_query = urllib.parse.urlencode({"from": start, "to": end, "base": "USD", "quotes": "INR"})
    fx_raw, _ = _fetch(f"{FX_HISTORY_URL}?{fx_query}", accept="application/json")
    try:
        fx_payload = json.loads(fx_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MarketProviderError("invalid_fx_history_json") from error
    fx_rows = fx_payload if isinstance(fx_payload, list) else fx_payload.get("rates", []) if isinstance(fx_payload, dict) else []
    fx_by_date = {
        row.get("date"): _decimal(row.get("rate"))
        for row in fx_rows if isinstance(row, dict) and isinstance(row.get("date"), str) and row.get("rate") is not None
    }
    if not fx_by_date:
        raise MarketProviderError("missing_usdinr_history")
    converted = []
    eligible_points = 0
    grams_per_troy_ounce = Decimal("31.1035")
    for point in points:
        if not isinstance(point, dict) or not isinstance(point.get("t"), str) or point.get("price") is None:
            continue
        day = point["t"][:10]
        if day < start or day > end:
            continue
        eligible_points += 1
        fx = fx_by_date.get(day)
        if fx is None:
            continue
        inr_per_gram = _decimal(point["price"]) * fx / grams_per_troy_ounce
        converted.append({"date": point["t"], "value_paise": int((inr_per_gram * 100).quantize(Decimal("1")))})
    if len(converted) < 2:
        raise MarketProviderError("insufficient_matched_gold_history")
    converted.sort(key=lambda point: point["date"])
    first = converted[0]["value_paise"]
    last = converted[-1]["value_paise"]
    result = {
        "status": "available",
        "range": range_name,
        "history": converted,
        "source": "Standard Bullion XAU/USD history + Frankfurter USD/INR daily reference rates",
        "unit": "INR per gram, 24K-equivalent spot reference",
        "latest_observation": converted[-1]["date"],
        "change_paise": last - first,
        "change_percent": (last - first) / first * 100 if first else None,
        "available_ranges": ["7d", "1m", "3m", "1y"],
        "dropped_unmatched_dates": eligible_points - len(converted),
    }
    return _store(cache_key, result)


def _series_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "series", "observations", "results"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
    raise MarketProviderError("invalid_series_response")


def market_series() -> dict[str, Any]:
    cached = _cached("indices")
    if cached:
        return cached
    identifiers = ["^NSEI", "^BSESN", "^NSEBANK", "USDINR"]
    query = urllib.parse.urlencode({"ids": ",".join(identifiers)})
    raw, _ = _fetch(f"{TIGZIG_URL}?{query}", accept="application/json")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MarketProviderError("invalid_json") from error
    rows = _series_rows(payload)
    observations: list[dict[str, Any]] = []
    for row in rows:
        date_value = row.get("date") or row.get("timestamp") or row.get("observed_at")
        if not isinstance(date_value, str):
            continue
        values: dict[str, float] = {}
        for identifier in identifiers:
            value = row.get(identifier)
            if value is None:
                continue
            try:
                values[identifier] = float(_decimal(value))
            except MarketProviderError:
                continue
        if values:
            observations.append({"date": date_value, "values": values})
    if not observations:
        raise MarketProviderError("no_valid_series_observations")
    observations.sort(key=lambda row: row["date"])
    instruments = []
    for identifier in identifiers:
        points = [{"date": row["date"], "value": row["values"][identifier]} for row in observations if identifier in row["values"]]
        if not points:
            instruments.append({"symbol": identifier, "status": "unavailable", "history": []})
            continue
        latest = points[-1]
        previous = points[-2] if len(points) > 1 else None
        change = latest["value"] - previous["value"] if previous else None
        change_percent = (change / previous["value"] * 100) if previous and previous["value"] else None
        instruments.append({
            "symbol": identifier,
            "status": "available",
            "value": latest["value"],
            "change": change,
            "change_percent": change_percent,
            "observed_at": latest["date"],
            "history": points[-90:],
            "source": "TigZig TREMOR (publisher/source metadata supplied by provider; delayed/end-of-day)",
        })
    return _store("indices", {"status": "partial" if any(item["status"] == "available" for item in instruments) else "unavailable", "instruments": instruments})


def _strip_markup(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", value))).strip()


def news_snapshot() -> dict[str, Any]:
    cached = _cached("news")
    if cached:
        return cached
    raw, _ = _fetch(NEWS_URL, accept="application/rss+xml, application/xml, text/xml")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise MarketProviderError("invalid_rss_response") from error
    items = []
    for item in root.findall(".//item")[:6]:
        title = _strip_markup(item.findtext("title", ""))
        link = item.findtext("link", "").strip()
        published = _strip_markup(item.findtext("pubDate", ""))
        publisher = _strip_markup(item.findtext("source", "")) or "Google News RSS"
        if title and link.startswith(("https://", "http://")):
            items.append({"headline": title, "publisher": publisher, "published_at": published, "url": link})
    if not items:
        raise MarketProviderError("no_valid_news_items")
    return _store("news", {"status": "available", "source": "Google News RSS", "items": items})


def market_pulse(force_refresh: bool = False, gold_range: str = "1m") -> dict[str, Any]:
    if force_refresh:
        with _cache_lock:
            _cache.clear()
    errors: dict[str, str] = {}
    try:
        gold = gold_snapshot()
    except MarketProviderError as error:
        errors["gold"] = str(error)
        gold = {"status": "unavailable", "source": "GoldPrice.dev", "updated_at": None, "purity": "24K/22K", "unit": "INR per gram", "value_paise": None, "price_22k_paise": None, "history": [], "history_status": "unavailable"}
    try:
        historical = gold_history(gold_range)
        gold["historical"] = historical
    except MarketProviderError as error:
        errors["gold_history"] = str(error)
        gold["historical"] = {"status": "unavailable", "range": gold_range, "history": [], "available_ranges": ["7d", "1m", "3m", "1y"], "source": "Standard Bullion + Frankfurter"}
    try:
        indices = market_series()
    except MarketProviderError as error:
        errors["indices"] = str(error)
        indices = {"status": "unavailable", "instruments": []}
    try:
        news = news_snapshot()
    except MarketProviderError as error:
        errors["news"] = str(error)
        news = {"status": "unavailable", "source": "Google News RSS", "items": []}
    return {
        "status": "available" if not errors else "partial" if len(errors) < 3 else "unavailable",
        "source": "GoldPrice.dev / TigZig TREMOR / Google News RSS",
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "gold": gold,
        "indices": indices["instruments"],
        "news": news["items"],
        "education": [
            {"title": "Build a cash buffer first", "body": "Keep a practical emergency reserve before taking market risk.", "kind": "education"},
            {"title": "Diversification reduces concentration", "body": "Spread risk across goals, time horizons and asset types.", "kind": "education"},
            {"title": "SIPs reward consistency, not certainty", "body": "Regular investing can build a habit, but returns are never guaranteed.", "kind": "education"},
        ],
        "provider_errors": errors,
    }
