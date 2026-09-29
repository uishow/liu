from datetime import datetime, timedelta, timezone

from app.feeds import ax_quote_fields, brief_feed_error, live_price
from app.hub import bars_refresh_due
from app.strategy import (
    Bar,
    Book,
    PendingOrder,
    Position,
    build_guidance,
    day_review,
    detect_signal,
    premium_allocation,
    run_book,
    spread_block,
    summarize,
)


def _bar(ts, price, volume=100.0, high=None, low=None, open_=None):
    return Bar(
        ts=ts,
        open=price if open_ is None else open_,
        high=price if high is None else high,
        low=price if low is None else low,
        close=price,
        volume=volume,
    )


def test_premium_gate_blocks_rich_quotes_and_halves_caution():
    blocked, note = premium_allocation(0.016)
    assert blocked is None
    assert "1.5%" in note
    half, _ = premium_allocation(0.008)
    assert half == 0.05
    full, _ = premium_allocation(0.002)
    assert full == 0.10


def test_wide_spread_is_rejected():
    assert spread_block(0.540, 0.542) is not None
    assert spread_block(8.560, 8.561) is None


def test_sub_two_yuan_names_do_not_signal():
    start = datetime(2026, 9, 28, 10, 0)
    bars = [_bar(start + timedelta(minutes=5 * i), 0.54) for i in range(12)]
    assert detect_signal("513180", bars) is None


def test_breakout_limit_order_books_a_winner():
    start = datetime(2026, 9, 28, 9, 35)
    bars: list[Bar] = []
    # Quiet drift keeps VWAP under the later breakout.
    for i in range(12):
        bars.append(_bar(start + timedelta(minutes=5 * i), 10.0, volume=100))
    # Four-bar box ending just before 10:35, then the 10:35 breakout.
    # 9:35 + 12*5 = 10:35 is index 12. Box is bars[-5:-1] relative to signal,
    # so indexes 8..11 are the box if we append four more after the 12.
    box_start = bars[-1].ts + timedelta(minutes=5)
    for i in range(4):
        bars.append(
            _bar(
                box_start + timedelta(minutes=5 * i),
                10.01,
                volume=100,
                high=10.02,
                low=10.00,
                open_=10.005,
            )
        )
    signal_ts = bars[-1].ts + timedelta(minutes=5)
    assert signal_ts.hour == 10 and signal_ts.minute == 55
    bars.append(_bar(signal_ts, 10.03, volume=200, high=10.035, low=10.02, open_=10.015))
    signal = detect_signal("518880", bars)
    assert signal is not None
    assert signal.kind == "breakout"
    assert signal.limit == 10.03

    # Next bar fills the limit and a later bar prints the 0.35% target
    # without touching the 0.18% stop.
    fill_ts = signal_ts + timedelta(minutes=5)
    bars.append(_bar(fill_ts, 10.03, volume=80, high=10.04, low=10.025, open_=10.03))
    rally_ts = fill_ts + timedelta(minutes=5)
    bars.append(_bar(rally_ts, 10.07, volume=90, high=10.08, low=10.03, open_=10.04))
    flat_ts = datetime(2026, 9, 28, 14, 45)
    bars.append(_bar(flat_ts, 10.07, volume=50, high=10.08, low=10.05))
    close_ts = datetime(2026, 9, 28, 15, 0)
    bars.append(_bar(close_ts, 10.07, volume=50))

    book = run_book({"518880": bars})
    assert len(book.trades) == 1
    assert book.trades[0].reason == "take"
    assert book.trades[0].pnl > 0
    assert summarize(book)["profitable"] is True


def test_gap_open_blocks_the_session():
    day1 = []
    start = datetime(2026, 9, 25, 9, 35)
    for i in range(20):
        day1.append(_bar(start + timedelta(minutes=5 * i), 10.0))
    day1.append(_bar(datetime(2026, 9, 25, 15, 0), 10.0))

    # 2% gap up, then the same breakout shape. The gap gate must refuse it.
    start2 = datetime(2026, 9, 28, 9, 35)
    day2 = []
    for i in range(12):
        day2.append(_bar(start2 + timedelta(minutes=5 * i), 10.2, volume=100, open_=10.2))
    box_start = day2[-1].ts + timedelta(minutes=5)
    for i in range(4):
        day2.append(
            _bar(
                box_start + timedelta(minutes=5 * i),
                10.21,
                volume=100,
                high=10.22,
                low=10.20,
                open_=10.205,
            )
        )
    signal_ts = day2[-1].ts + timedelta(minutes=5)
    day2.append(_bar(signal_ts, 10.23, volume=220, high=10.24, low=10.22, open_=10.215))
    day2.append(_bar(signal_ts + timedelta(minutes=5), 10.23, volume=80, high=10.24, low=10.22, open_=10.23))
    day2.append(_bar(datetime(2026, 9, 28, 15, 0), 10.23))
    book = run_book({"518880": day1 + day2})
    assert book.trades == []


def test_guidance_gives_a_limit_buy_and_blocks_rich_premium():
    order = PendingOrder(
        "518880",
        8.560,
        11600,
        "breakout",
        "2026-09-29",
        "VWAP 上方窄幅震荡后放量突破上沿",
        signal_ts=datetime(2026, 9, 29, 10, 35),
    )
    book = Book(pending=order, day="2026-09-29")
    quote = {"last": 8.562, "bid": 8.561, "ask": 8.562, "premium_rate": 0.002, "name": "华安黄金ETF"}
    guide = build_guidance(book, {"518880": quote}, datetime(2026, 9, 29, 10, 36))
    assert guide["action"] == "buy"
    assert guide["limit"] == 8.560
    assert guide["take_profit"] == 8.590
    assert guide["stop_loss"] == 8.545
    assert guide["qty"] == 11600
    assert "8.560" in guide["steps"][1]

    quote["premium_rate"] = 0.02
    blocked = build_guidance(book, {"518880": quote}, datetime(2026, 9, 29, 10, 36))
    assert blocked["action"] == "wait"
    assert "1.5%" in blocked["why"]


def test_guidance_says_sell_when_the_target_is_hit():
    pos = Position("511380", 7600, 13.000, 13.000 * 1.0035, 13.000 * 0.9982, "pullback", datetime(2026, 9, 29, 10, 40), 1.0)
    book = Book(position=pos, day="2026-09-29", day_trades=1)
    guide = build_guidance(book, {"511380": {"last": 13.05, "name": "可转债ETF"}}, datetime(2026, 9, 29, 10, 50))
    assert guide["action"] == "sell"
    assert "止盈" in guide["why"]


def test_zero_last_before_the_open_uses_the_previous_close():
    assert live_price(0, 8.568) == 8.568
    assert live_price(8.57, 8.568) == 8.57
    assert live_price(None, None) is None


def test_feed_timeout_is_named_in_chinese():
    assert brief_feed_error(TimeoutError("timed out")) == "通达信连接超时"


def test_preopen_snapshot_does_not_print_a_total_loss():
    row = ax_quote_fields(
        {
            "last_price": 0,
            "pre_close": 8.568,
            "open": 0,
            "high": 0,
            "low": 0,
            "change_pct": -100,
            "volume": 0,
            "amount": 0,
            "amplitude_pct": 0,
        }
    )
    assert row["last"] == 8.568
    assert row["change_pct"] is None


def test_a_quiet_morning_is_reported_instead_of_a_fake_trade():
    start = datetime(2026, 9, 29, 9, 35)
    quiet = [_bar(start + timedelta(minutes=5 * i), 8.50) for i in range(20)]
    cheap = [_bar(start + timedelta(minutes=5 * i), 0.54) for i in range(20)]
    review = day_review({"518880": quiet, "513180": cheap}, "2026-09-29")
    assert review["signals"] == []
    assert review["quiet"][0]["code"] == "518880"
    assert review["cheap"] == ["513180"]


def test_five_minute_bars_refresh_themselves_during_the_session():
    cn = timezone(timedelta(hours=8))
    opening = datetime(2026, 9, 29, 10, 12, tzinfo=cn)
    assert bars_refresh_due(0, 100, opening) is True
    assert bars_refresh_due(80, 100, opening) is False
    assert bars_refresh_due(80, 130, opening) is True
    assert bars_refresh_due(80, 100, opening, force=True) is True
    closed = datetime(2026, 9, 29, 16, 0, tzinfo=cn)
    assert bars_refresh_due(0, 100, closed) is False
    weekend = datetime(2026, 10, 3, 10, 0, tzinfo=cn)
    assert bars_refresh_due(0, 100, weekend, force=True) is False
