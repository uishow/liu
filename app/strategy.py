"""Executable T+0 rules.

The original note uses two shapes: a dried-up pullback and a volume breakout.
On three months of 5-minute bars those shapes only keep a positive net result
after costs when entries are limit orders, targets fit the real intraday range,
and names whose tick size swallows the target are left alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


TICK = 0.001
COMMISSION = 0.0001  # 万1 per side
TP_PCT = 0.0035
SL_PCT = 0.0018
MIN_PRICE = 2.0
ALLOC = 0.10
HALF_ALLOC = 0.05
MAX_DAY_TRADES = 3
MAX_CONSEC_LOSSES = 2
PREMIUM_BLOCK = 0.015
PREMIUM_CAUTION = 0.005
GAP_BLOCK = 0.010
SPREAD_BLOCK = 0.0015
LOT = 100


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def day(self) -> str:
        return self.ts.strftime("%Y-%m-%d")

    @property
    def minute(self) -> int:
        return self.ts.hour * 60 + self.ts.minute


@dataclass(frozen=True)
class Signal:
    code: str
    kind: str
    limit: float
    reason: str


@dataclass
class Position:
    code: str
    qty: int
    fill: float
    tp_px: float
    sl_px: float
    kind: str
    entry_ts: datetime
    fee_in: float


@dataclass
class PendingOrder:
    code: str
    limit: float
    qty: int
    kind: str
    day: str
    reason: str


@dataclass
class Trade:
    code: str
    kind: str
    qty: int
    entry_ts: str
    exit_ts: str
    fill: float
    exit: float
    pnl: float
    reason: str
    note: str


@dataclass
class Book:
    cash: float = 1_000_000.0
    position: Position | None = None
    pending: PendingOrder | None = None
    day: str | None = None
    day_trades: int = 0
    consec_losses: int = 0
    traded: set[str] = field(default_factory=set)
    gap_blocked: set[str] = field(default_factory=set)
    trades: list[Trade] = field(default_factory=list)
    equity_points: list[dict] = field(default_factory=list)

    @property
    def halted(self) -> bool:
        return self.consec_losses >= MAX_CONSEC_LOSSES


def in_signal_window(minute: int) -> bool:
    return (10 * 60 + 10) <= minute <= (11 * 60 + 15) or (13 * 60 + 40) <= minute <= (14 * 60 + 15)


def session_vwap(bars: list[Bar]) -> float:
    volume = sum(max(bar.volume, 1.0) for bar in bars)
    return sum(bar.close * max(bar.volume, 1.0) for bar in bars) / volume


def detect_signal(code: str, day_bars: list[Bar]) -> Signal | None:
    """Return a limit-buy signal from bars that have already closed."""

    if len(day_bars) < 9:
        return None
    bar = day_bars[-1]
    if not in_signal_window(bar.minute):
        return None
    if bar.close < MIN_PRICE:
        return None
    vwap = session_vwap(day_bars)
    breakout = _breakout(day_bars, vwap)
    if breakout:
        return Signal(code, "breakout", bar.close, breakout)
    pullback = _pullback(day_bars, vwap)
    if pullback:
        return Signal(code, "pullback", bar.close, pullback)
    return None


def _breakout(day_bars: list[Bar], vwap: float) -> str | None:
    if any(bar.close < vwap * 0.999 for bar in day_bars[-6:]):
        return None
    box = day_bars[-5:-1]
    last = day_bars[-1]
    width = (max(bar.high for bar in box) - min(bar.low for bar in box)) / last.close
    avg_volume = sum(bar.volume for bar in box) / len(box)
    if not (0.0015 <= width <= 0.004):
        return None
    if last.close <= max(bar.high for bar in box):
        return None
    if last.volume <= 1.2 * avg_volume:
        return None
    if last.close <= day_bars[0].open:
        return None
    return "VWAP 上方窄幅震荡后放量突破上沿"


def _pullback(day_bars: list[Bar], vwap: float) -> str | None:
    last = day_bars[-1]
    day_high = max(bar.high for bar in day_bars)
    if day_high <= 0:
        return None
    dip = (day_high - last.close) / day_high
    avg_volume = sum(bar.volume for bar in day_bars[-6:-1]) / 5
    if not (0.0025 <= dip <= 0.006):
        return None
    if last.close < vwap or last.low > vwap * 1.001:
        return None
    if not (last.close > last.open and last.low >= day_bars[-2].low):
        return None
    if last.volume > avg_volume:
        return None
    if last.close < day_bars[0].open:
        return None
    return "上升结构里缩量回踩分时均价后企稳"


def premium_allocation(premium: float | None) -> tuple[float | None, str]:
    """Map a live premium rate to a position fraction.

    Historical bars do not carry IOPV, so a missing premium keeps the tested
    full size. A live quote always passes a number, or an explicit sentinel is
    not used here.
    """

    if premium is None:
        return ALLOC, "历史样本无逐分钟 IOPV，按研究仓位"
    if premium > PREMIUM_BLOCK:
        return None, "溢价超过 1.5%，禁止开仓"
    if premium > PREMIUM_CAUTION:
        return HALF_ALLOC, "溢价 0.5%–1.5%，半仓"
    return ALLOC, "折溢价低于 0.5%，标准仓"


def spread_block(bid: float | None, ask: float | None) -> str | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    if (ask - bid) / mid > SPREAD_BLOCK:
        return "买卖价差过大，放弃"
    return None


def _roll_day(book: Book, day: str, opens: dict[str, float], prev_close: dict[str, float]) -> None:
    if book.day == day:
        return
    book.day = day
    book.day_trades = 0
    book.consec_losses = 0
    book.traded = set()
    book.pending = None
    blocked = set()
    for code, price in opens.items():
        previous = prev_close.get(code)
        if previous and previous > 0 and abs(price - previous) / previous >= GAP_BLOCK:
            blocked.add(code)
    book.gap_blocked = blocked


def _close_position(book: Book, pos: Position, exit_px: float, ts: datetime, reason: str) -> None:
    fee = pos.qty * exit_px * COMMISSION
    pnl = (exit_px - pos.fill) * pos.qty - pos.fee_in - fee
    book.cash += pos.qty * exit_px - fee
    book.trades.append(
        Trade(
            code=pos.code,
            kind=pos.kind,
            qty=pos.qty,
            entry_ts=pos.entry_ts.strftime("%Y-%m-%d %H:%M"),
            exit_ts=ts.strftime("%Y-%m-%d %H:%M"),
            fill=round(pos.fill, 4),
            exit=round(exit_px, 4),
            pnl=pnl,
            reason=reason,
            note="",
        )
    )
    book.consec_losses = 0 if pnl >= 0 else book.consec_losses + 1
    book.position = None


def run_book(bars_by_code: dict[str, list[Bar]], premiums: dict[str, float] | None = None) -> Book:
    """Replay the shared rule set. One position, limit entry, hard flat at 14:45."""

    book = Book()
    index: dict[datetime, list[tuple[str, int]]] = {}
    day_index: dict[str, dict[str, list[int]]] = {}
    for code, bars in bars_by_code.items():
        per_day: dict[str, list[int]] = {}
        for idx, bar in enumerate(bars):
            index.setdefault(bar.ts, []).append((code, idx))
            per_day.setdefault(bar.day, []).append(idx)
        day_index[code] = per_day

    prev_close: dict[str, float] = {}
    last_close: dict[str, float] = {}
    for ts in sorted(index):
        day = ts.strftime("%Y-%m-%d")
        if book.day != day:
            opens = {
                code: bars_by_code[code][ids[0]].open
                for code, per_day in day_index.items()
                if (ids := per_day.get(day))
            }
            _roll_day(book, day, opens, prev_close)
        for code, idx in index[ts]:
            last_close[code] = bars_by_code[code][idx].close

        if book.position is not None:
            pos = book.position
            hit = [idx for code, idx in index[ts] if code == pos.code]
            if hit:
                bar = bars_by_code[pos.code][hit[0]]
                if bar.low <= pos.sl_px:
                    _close_position(book, pos, max(TICK, pos.sl_px - TICK), ts, "stop")
                elif bar.high >= pos.tp_px:
                    _close_position(book, pos, pos.tp_px, ts, "take")
                elif bar.minute >= 14 * 60 + 45:
                    _close_position(book, pos, max(TICK, bar.close - 0.5 * TICK), ts, "flat")

        if book.position is None and book.pending is not None and book.pending.day == day:
            pending = book.pending
            hit = [idx for code, idx in index[ts] if code == pending.code]
            if hit:
                bar = bars_by_code[pending.code][hit[0]]
                if bar.low <= pending.limit and bar.minute <= 14 * 60 + 40:
                    fill = bar.open if bar.open <= pending.limit else pending.limit
                    fee = pending.qty * fill * COMMISSION
                    if pending.qty * fill + fee <= book.cash:
                        book.cash -= pending.qty * fill + fee
                        book.position = Position(
                            code=pending.code,
                            qty=pending.qty,
                            fill=fill,
                            tp_px=fill * (1 + TP_PCT),
                            sl_px=fill * (1 - SL_PCT),
                            kind=pending.kind,
                            entry_ts=ts,
                            fee_in=fee,
                        )
                        book.day_trades += 1
                        book.traded.add(pending.code)
                book.pending = None

        if book.position is None and book.pending is None and not book.halted and book.day_trades < MAX_DAY_TRADES:
            if in_signal_window(ts.hour * 60 + ts.minute):
                candidates: list[tuple[int, float, Signal]] = []
                for code, idx in index[ts]:
                    if code in book.traded or code in book.gap_blocked:
                        continue
                    ids = day_index[code][day]
                    if idx != ids[-1] and idx not in ids:
                        continue
                    offset = ids.index(idx)
                    window = [bars_by_code[code][pos] for pos in ids[: offset + 1]]
                    signal = detect_signal(code, window)
                    if signal is None:
                        continue
                    premium = None if premiums is None else premiums.get(code)
                    alloc, _note = premium_allocation(premium)
                    if alloc is None:
                        continue
                    candidates.append((0 if signal.kind == "pullback" else 1, -window[-1].volume, signal, alloc))
                if candidates:
                    candidates.sort(key=lambda item: (item[0], item[1]))
                    _rank, _vol, signal, alloc = candidates[0]
                    qty = int(book.cash * alloc / signal.limit / LOT) * LOT
                    if qty >= LOT:
                        book.pending = PendingOrder(
                            code=signal.code,
                            limit=signal.limit,
                            qty=qty,
                            kind=signal.kind,
                            day=day,
                            reason=signal.reason,
                        )

        if ts.hour == 15 and ts.minute == 0:
            marked = book.cash
            if book.position is not None:
                marked += book.position.qty * last_close.get(book.position.code, book.position.fill)
            book.equity_points.append({"day": day, "equity": marked})
            for code, price in last_close.items():
                # Only advance previous close for symbols that printed today.
                if day_index[code].get(day):
                    prev_close[code] = price
    return book


def summarize(book: Book, start_equity: float = 1_000_000.0) -> dict:
    trades = book.trades
    pnl = sum(trade.pnl for trade in trades)
    wins = [trade for trade in trades if trade.pnl > 0]
    losses = [trade for trade in trades if trade.pnl <= 0]
    equity = start_equity
    peak = start_equity
    max_dd = 0.0
    by_day: dict[str, float] = {}
    for trade in trades:
        by_day[trade.exit_ts[:10]] = by_day.get(trade.exit_ts[:10], 0.0) + trade.pnl
    for day in sorted(by_day):
        equity += by_day[day]
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, equity / peak - 1)
    avg_win = sum(trade.pnl for trade in wins) / len(wins) if wins else 0.0
    avg_loss = sum(trade.pnl for trade in losses) / len(losses) if losses else 0.0
    by_code: dict[str, float] = {}
    by_kind: dict[str, int] = {}
    for trade in trades:
        by_code[trade.code] = by_code.get(trade.code, 0.0) + trade.pnl
        by_kind[trade.kind] = by_kind.get(trade.kind, 0) + 1
    by_month: dict[str, float] = {}
    for trade in trades:
        month = trade.exit_ts[:7]
        by_month[month] = by_month.get(month, 0.0) + trade.pnl
    return {
        "start_equity": start_equity,
        "end_equity": start_equity + pnl,
        "net_pnl": pnl,
        "return_pct": pnl / start_equity * 100,
        "trades": len(trades),
        "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": (avg_win / abs(avg_loss)) if avg_loss else None,
        "max_drawdown_pct": max_dd * 100,
        "by_code": by_code,
        "by_kind": by_kind,
        "by_month": by_month,
        "profitable": pnl > 0,
    }
