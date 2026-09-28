"""Live market data from eltdx and AxData.

eltdx is the execution feed: five-level quotes and 5-minute bars over the
Tongdaxin 7709 session. AxData normalizes the ETF snapshot and is also the
source of the Tencent quote payload. AxData's normalized snapshot drops the
IOPV fields, so the premium gate reads those fields from the same payload.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from axdata_core.adapters.tencent.request import TENCENT_QUOTE_URL

from .strategy import Bar
from .universe import BY_CODE, UNIVERSE

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "cache" / "m5.json"
HISTORY_PAGES = 4
PAGE_SIZE = 800


def load_history(client) -> dict[str, list[Bar]]:
    loaded: dict[str, list[Bar]] = {}
    for item in UNIVERSE:
        rows: list[Bar] = []
        for page in range(HISTORY_PAGES):
            series = client.bars.get(
                item.tdx_code,
                period="5m",
                start=page * PAGE_SIZE,
                count=PAGE_SIZE,
            )
            page_rows = bars_from_series(series)
            if not page_rows:
                break
            rows.extend(page_rows)
            if len(page_rows) < PAGE_SIZE:
                break
        unique = {bar.ts: bar for bar in rows}
        loaded[item.code] = [unique[key] for key in sorted(unique)]
    _write_cache(loaded)
    return loaded


def load_recent(client, current: dict[str, list[Bar]], count: int = 120) -> dict[str, list[Bar]]:
    """Merge the latest 5-minute bars into an existing history."""

    merged = {code: list(series) for code, series in current.items()}
    for item in UNIVERSE:
        fresh = bars_from_series(client.bars.get(item.tdx_code, period="5m", start=0, count=count))
        if not fresh:
            continue
        by_ts = {bar.ts: bar for bar in merged.get(item.code, [])}
        for bar in fresh:
            by_ts[bar.ts] = bar
        merged[item.code] = [by_ts[key] for key in sorted(by_ts)]
    _write_cache(merged)
    return merged


def bars_from_series(series) -> list[Bar]:
    rows: list[Bar] = []
    for bar in getattr(series, "bars", ()) or ():
        stamp = bar.time
        if stamp.tzinfo is not None:
            stamp = stamp.replace(tzinfo=None)
        rows.append(
            Bar(
                ts=stamp,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume_lots or 0),
            )
        )
    return rows


def load_cache() -> dict[str, list[Bar]]:
    if not CACHE_PATH.exists():
        return {}
    payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    loaded: dict[str, list[Bar]] = {}
    for code, rows in payload.items():
        loaded[code] = [
            Bar(
                ts=datetime.strptime(row["ts"], "%Y-%m-%d %H:%M:%S"),
                open=row["o"],
                high=row["h"],
                low=row["l"],
                close=row["c"],
                volume=row["v"],
            )
            for row in rows
        ]
    return loaded


def _write_cache(bars: dict[str, list[Bar]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        code: [
            {
                "ts": bar.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "o": bar.open,
                "h": bar.high,
                "l": bar.low,
                "c": bar.close,
                "v": bar.volume,
            }
            for bar in series
        ]
        for code, series in bars.items()
    }
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def eltdx_quotes(client) -> dict[str, dict]:
    snaps = client.helpers.full_quotes([item.tdx_code for item in UNIVERSE])
    parsed: dict[str, dict] = {}
    for snap in snaps:
        code = snap.code
        bid = snap.buy_levels[0].price if snap.buy_levels else None
        ask = snap.sell_levels[0].price if snap.sell_levels else None
        bid_vol = snap.buy_levels[0].volume if snap.buy_levels else None
        ask_vol = snap.sell_levels[0].volume if snap.sell_levels else None
        parsed[code] = {
            "last": snap.last_price,
            "open": snap.open_price,
            "high": snap.high_price,
            "low": snap.low_price,
            "pre_close": snap.pre_close_price,
            "amount": snap.amount,
            "volume": snap.total_hand,
            "bid": bid,
            "ask": ask,
            "bid_volume": bid_vol,
            "ask_volume": ask_vol,
        }
    return parsed


def axdata_snapshots(ax_client) -> dict[str, dict]:
    frame = ax_client.call(
        "etf_realtime_snapshot_tdx",
        code=[item.instrument_id for item in UNIVERSE],
        fields=[
            "instrument_id",
            "last_price",
            "pre_close",
            "open",
            "high",
            "low",
            "change_pct",
            "volume",
            "amount",
            "amplitude_pct",
        ],
    )
    parsed: dict[str, dict] = {}
    for row in frame.to_dict(orient="records"):
        code = str(row["instrument_id"]).split(".")[0]
        parsed[code] = {
            "last": _num(row.get("last_price")),
            "pre_close": _num(row.get("pre_close")),
            "open": _num(row.get("open")),
            "high": _num(row.get("high")),
            "low": _num(row.get("low")),
            "change_pct": _num(row.get("change_pct")),
            "volume": _num(row.get("volume")),
            "amount": _num(row.get("amount")),
            "amplitude_pct": _num(row.get("amplitude_pct")),
        }
    return parsed


def tencent_premiums() -> dict[str, dict]:
    """IOPV and premium sit on the Tencent payload AxData already requests."""

    codes = ",".join(item.tencent_code for item in UNIVERSE)
    url = TENCENT_QUOTE_URL.format(codes=codes)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com"})
    raw = urlopen(request, timeout=12).read()
    text = raw.decode("gbk", errors="replace")
    parsed: dict[str, dict] = {}
    for item in UNIVERSE:
        marker = f'v_{item.tencent_code}="'
        start = text.find(marker)
        if start < 0:
            continue
        body = text[start + len(marker) :]
        end = body.find('"')
        parts = body[:end].split("~")
        premium_pct = _num(parts[77]) if len(parts) > 78 else None
        iopv = _num(parts[78]) if len(parts) > 78 else None
        name = parts[1] if len(parts) > 1 else BY_CODE[item.code].name
        parsed[item.code] = {
            "name": name,
            "premium_pct": premium_pct,
            "iopv": iopv,
            "premium_rate": None if premium_pct is None else premium_pct / 100,
        }
    return parsed


def _num(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number
