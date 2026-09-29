from datetime import datetime

from app.journal import Journal


def _wait(why="午休"):
    return {
        "action": "wait",
        "title": "现在不交易",
        "code": "",
        "name": "",
        "kind": "",
        "limit": None,
        "take_profit": None,
        "stop_loss": None,
        "qty": 0,
        "valid_until": "",
        "why": why,
        "steps": [why],
    }


def test_journal_archives_each_new_suggestion_and_every_fill(tmp_path):
    path = tmp_path / "journal.json"
    journal = Journal(path)
    now = datetime(2026, 9, 29, 10, 36, 2)
    journal.record(_wait(), [], now)
    richer = _wait("午休")
    richer["steps"] = ["午休", "价格不低于 2 元的标的今天还没有买点。"]
    once = journal.record(richer, [], now.replace(second=20))
    assert len(once["suggestions"]) == 1
    assert "还没有买点" in once["suggestions"][0]["instruction"]
    buy = _wait("突破")
    buy.update(
        {
            "action": "buy",
            "title": "现在买入",
            "code": "518880",
            "name": "华安黄金ETF",
            "kind": "breakout",
            "limit": 8.56,
            "take_profit": 8.59,
            "stop_loss": 8.545,
            "qty": 11600,
            "valid_until": "10:40",
        }
    )
    trade = {
        "code": "518880",
        "kind": "breakout",
        "qty": 11600,
        "entry_ts": "2026-09-29 10:40",
        "exit_ts": "2026-09-29 11:00",
        "fill": 8.56,
        "exit": 8.59,
        "pnl": 12.34,
        "reason": "take",
        "note": "",
    }
    view = journal.record(buy, [trade], now)
    again = journal.record(buy, [trade], now)
    assert len(view["suggestions"]) == 2
    assert view["suggestions"][1]["instruction"].startswith("限价买入 华安黄金ETF 518880")
    assert len(view["operations"]) == 1
    assert again["operations"][0]["pnl"] == 12.34
    reloaded = Journal(path).view("2026-09-29")
    assert len(reloaded["suggestions"]) == 2
    assert reloaded["operations"][0]["id"] == "518880|2026-09-29 10:40|2026-09-29 11:00|take"
