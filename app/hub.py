"""In-memory desk: history, paper book, and the two live feeds."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

from eltdx import TdxClient

os.environ.setdefault("AXDATA_TDX_TIMEOUT", "20")

import axdata as ax

from . import feeds
from .journal import Journal
from .strategy import (
    MIN_PRICE,
    day_review,
    detect_signal,
    premium_allocation,
    build_guidance,
    run_book,
    spread_block,
    summarize,
)
from .universe import BY_CODE, UNIVERSE

CN = timezone(timedelta(hours=8))
QUOTE_SECONDS = 15
BAR_REFRESH_SECONDS = 45
ELTDX_TIMEOUT = 20.0


class Hub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ready = False
        self.loading = False
        self.error: str | None = None
        self.bars = {}
        self.book = None
        self.summary: dict | None = None
        self.quotes: list[dict] = []
        self.sources = {
            "eltdx": {"ok": False, "detail": "未连接"},
            "axdata": {"ok": False, "detail": "未连接"},
            "premium": {"ok": False, "detail": "未取到 IOPV"},
        }
        self.updated_at: str | None = None
        self.auto_error: str | None = None
        self.last_bar_fetch = 0.0
        self._eltdx = None
        self._ax = None
        self._ax_session = None
        self._feed_lock = threading.Lock()
        self._stop = threading.Event()
        self._auto_started = False
        self.journal = Journal()

    def bootstrap(self) -> None:
        with self.lock:
            self.loading = True
            self.error = None
        try:
            self._eltdx = TdxClient(timeout=ELTDX_TIMEOUT, heartbeat_interval=30)
            self._ax = ax.AxDataClient()
            self._open_ax_session()
            try:
                self.bars = feeds.load_history(self._eltdx)
                history_source = "eltdx"
            except Exception as exc:
                cached = feeds.load_cache()
                if not cached:
                    raise exc
                self.bars = cached
                history_source = f"缓存（eltdx 拉取失败：{exc}）"
            self.book = run_book(self.bars)
            self.summary = summarize(self.book)
            self.summary["sample"] = _sample_span(self.bars)
            self.summary["history_source"] = history_source
            self.last_bar_fetch = time.time()
            self.refresh_quotes()
            with self.lock:
                self.ready = True
                self.error = None
            self._start_auto()
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.ready = False
        finally:
            with self.lock:
                self.loading = False

    def _start_auto(self) -> None:
        if self._auto_started:
            return
        self._auto_started = True
        threading.Thread(target=self._auto_loop, name="etf-auto", daemon=True).start()

    def _auto_loop(self) -> None:
        while not self._stop.wait(QUOTE_SECONDS):
            if not self.ready:
                continue
            self._refresh_cycle(force_bars=False)

    def refresh_now(self) -> dict:
        self._refresh_cycle(force_bars=True)
        return self.snapshot()

    def _refresh_cycle(self, force_bars: bool) -> None:
        with self._feed_lock:
            try:
                self._reload_bars(force=force_bars)
            except Exception as exc:
                with self.lock:
                    self.auto_error = str(exc)
            self.refresh_quotes()

    def _reload_bars(self, force: bool) -> None:
        now = datetime.now(CN)
        if not bars_refresh_due(self.last_bar_fetch, time.time(), now, force=force):
            return
        updated = feeds.load_recent(self._eltdx, self.bars)
        book = run_book(updated)
        summary = summarize(book)
        summary["sample"] = _sample_span(updated)
        summary["history_source"] = "eltdx"
        with self.lock:
            self.bars = updated
            self.book = book
            self.summary = summary
            self.last_bar_fetch = time.time()
            self.auto_error = None

    def _open_ax_session(self) -> None:
        if self._ax is None:
            self._ax = ax.AxDataClient()
        if self._ax_session is not None:
            return
        session = self._ax.session("tdx")
        session.open()
        self._ax_session = session

    def _reset_eltdx(self) -> None:
        client = self._eltdx
        self._eltdx = TdxClient(timeout=ELTDX_TIMEOUT, heartbeat_interval=30)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _reset_ax_session(self) -> None:
        session = self._ax_session
        self._ax_session = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        self._open_ax_session()

    def refresh_quotes(self) -> None:
        eltdx_rows: dict = {}
        ax_rows: dict = {}
        premium_rows: dict = {}
        eltdx_error = None
        ax_error = None
        premium_error = None
        started = time.perf_counter()
        try:
            eltdx_rows = feeds.eltdx_quotes(self._eltdx)
        except Exception as exc:
            eltdx_error = feeds.brief_feed_error(exc)
            try:
                self._reset_eltdx()
                eltdx_rows = feeds.eltdx_quotes(self._eltdx)
                eltdx_error = None
            except Exception as retry_exc:
                eltdx_error = feeds.brief_feed_error(retry_exc)
        eltdx_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        try:
            if self._ax_session is None:
                self._open_ax_session()
            ax_rows = feeds.axdata_snapshots(self._ax_session)
        except Exception as exc:
            ax_error = feeds.brief_feed_error(exc)
            try:
                self._reset_ax_session()
                ax_rows = feeds.axdata_snapshots(self._ax_session)
                ax_error = None
            except Exception as retry_exc:
                ax_error = feeds.brief_feed_error(retry_exc)
        ax_ms = (time.perf_counter() - started) * 1000
        try:
            premium_rows = feeds.tencent_premiums()
        except Exception as exc:
            premium_error = feeds.brief_feed_error(exc)

        quotes = []
        for item in UNIVERSE:
            live = eltdx_rows.get(item.code, {})
            peer = ax_rows.get(item.code, {})
            premium = premium_rows.get(item.code, {})
            last = live.get("last") if live.get("last") is not None else peer.get("last")
            rate = premium.get("premium_rate")
            alloc, premium_note = premium_allocation(rate if rate is not None else 0.0 if premium else None)
            if not premium:
                alloc, premium_note = premium_allocation(None)
                premium_note = "IOPV 暂缺，实盘仓位按研究仓位并标黄"
            spread_note = spread_block(live.get("bid"), live.get("ask"))
            signal = _latest_signal(item.code, self.bars.get(item.code, []))
            reasons = []
            if last is not None and last < MIN_PRICE:
                reasons.append(f"最新价 {last:.3f} 低于 2 元，1 个最小价位会吞掉 0.35% 目标")
            if alloc is None:
                reasons.append(premium_note)
            elif rate is not None and rate > 0.005:
                reasons.append(premium_note)
            if spread_note:
                reasons.append(spread_note)
            quotes.append(
                {
                    "code": item.code,
                    "name": premium.get("name") or item.name,
                    "exchange": item.exchange,
                    "class_id": item.class_id,
                    "style": item.style,
                    "note": item.note,
                    "last": last,
                    "eltdx": live,
                    "axdata": peer,
                    "basis_bps": _basis_bps(live.get("last"), peer.get("last")),
                    "iopv": premium.get("iopv"),
                    "premium_pct": premium.get("premium_pct"),
                    "premium_note": premium_note,
                    "signal": signal,
                    "blocked": bool(reasons),
                    "block_reasons": reasons,
                    "tradable_price": last is not None and last >= MIN_PRICE,
                }
            )
        with self.lock:
            self.quotes = quotes
            self.updated_at = datetime.now(CN).strftime("%Y-%m-%d %H:%M:%S")
            self.sources = {
                "eltdx": {
                    "ok": eltdx_error is None and bool(eltdx_rows),
                    "detail": eltdx_error or f"五档快照 {len(eltdx_rows)} 只",
                    "latency_ms": round(eltdx_ms),
                },
                "axdata": {
                    "ok": ax_error is None and bool(ax_rows),
                    "detail": ax_error or f"ETF 快照 {len(ax_rows)} 只",
                    "latency_ms": round(ax_ms),
                },
                "premium": {
                    "ok": premium_error is None and bool(premium_rows),
                    "detail": premium_error or "腾讯 IOPV 字段（AxData 同一条快照报文）",
                },
            }

    def snapshot(self) -> dict:
        with self.lock:
            summary = self.summary or {}
            trades = []
            if self.book is not None:
                trades = [trade.__dict__ for trade in self.book.trades]
            equity = list(self.book.equity_points) if self.book is not None else []
            guidance = None
            if self.book is not None:
                quote_map = {
                    row["code"]: {
                        "last": row.get("last"),
                        "bid": (row.get("eltdx") or {}).get("bid"),
                        "ask": (row.get("eltdx") or {}).get("ask"),
                        "premium_rate": None if row.get("premium_pct") is None else row["premium_pct"] / 100,
                        "name": row.get("name"),
                    }
                    for row in self.quotes
                }
                now = datetime.now(CN)
                guidance = build_guidance(self.book, quote_map, now)
                if guidance and guidance.get("action") == "wait":
                    review = day_review(self.bars, now.strftime("%Y-%m-%d"))
                    notes = _review_lines(review)
                    if notes:
                        guidance["steps"] = notes + list(guidance.get("steps") or [])
            else:
                now = datetime.now(CN)
            journal = self.journal.record(guidance, trades, now)
            return {
                "ready": self.ready,
                "loading": self.loading,
                "error": self.error,
                "updated_at": self.updated_at,
                "sources": self.sources,
                "summary": summary,
                "quotes": self.quotes,
                "trades": trades,
                "equity": equity,
                "session_open": _session_open(),
                "auto": True,
                "guidance": guidance,
                "journal": journal,
            }


def _review_lines(review: dict) -> list[str]:
    lines = []
    signals = review.get("signals") or []
    quiet = review.get("quiet") or []
    cheap = review.get("cheap") or []
    if signals:
        text = "；".join(
            f"{item['time']} {BY_CODE[item['code']].name if item['code'] in BY_CODE else item['code']} {item['code']} "
            f"{'回调' if item['kind'] == 'pullback' else '突破'} 限价 {item['limit']:.3f}"
            for item in signals
        )
        lines.append(f"今天已经出现过买点：{text}。只在信号后的 5 分钟内挂限价，过时不追。")
    elif quiet:
        text = "，".join(
            f"{BY_CODE[item['code']].name if item['code'] in BY_CODE else item['code']} 振幅 {item['range_pct']:.2f}%"
            for item in quiet
        )
        lines.append(f"价格不低于 2 元的标的今天还没有买点：{text}。")
    if cheap:
        names = "、".join(BY_CODE[code].name if code in BY_CODE else code for code in cheap)
        lines.append(f"低于 2 元不做：{names}。")
    return lines


def in_bar_window(now: datetime) -> bool:
    current = now.astimezone(CN) if now.tzinfo else now.replace(tzinfo=CN)
    if current.weekday() >= 5:
        return False
    minute = current.hour * 60 + current.minute
    return (9 * 60 + 25) <= minute <= (15 * 60 + 10)


def bars_refresh_due(last_fetch: float, now_ts: float, now: datetime, force: bool = False) -> bool:
    if not in_bar_window(now):
        return False
    if force:
        return True
    return now_ts - last_fetch >= BAR_REFRESH_SECONDS


def _latest_signal(code: str, bars) -> dict | None:
    if not bars:
        return None
    last_day = bars[-1].day
    day_bars = [bar for bar in bars if bar.day == last_day]
    found = None
    for idx in range(8, len(day_bars)):
        signal = detect_signal(code, day_bars[: idx + 1])
        if signal is not None:
            found = {
                "kind": signal.kind,
                "limit": signal.limit,
                "reason": signal.reason,
                "time": day_bars[idx].ts.strftime("%H:%M"),
            }
    return found


def _basis_bps(left, right) -> float | None:
    if left is None or right is None or right == 0:
        return None
    return (left - right) / right * 10000


def _sample_span(bars: dict) -> dict:
    stamps = [bar.ts for series in bars.values() for bar in series]
    if not stamps:
        return {}
    return {
        "start": min(stamps).strftime("%Y-%m-%d"),
        "end": max(stamps).strftime("%Y-%m-%d"),
        "symbols": len(bars),
        "bars": sum(len(series) for series in bars.values()),
    }


def _session_open(now: datetime | None = None) -> bool:
    current = now or datetime.now(CN)
    if current.weekday() >= 5:
        return False
    minute = current.hour * 60 + current.minute
    return (9 * 60 + 30) <= minute <= (11 * 60 + 30) or (13 * 60) <= minute <= (15 * 60)


HUB = Hub()
