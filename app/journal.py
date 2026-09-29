"""Append-only archive of the suggestions shown on the desk and the paper fills."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

JOURNAL_PATH = Path(__file__).resolve().parent.parent / "data" / "journal.json"


class Journal:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or JOURNAL_PATH
        self._lock = threading.Lock()
        self._data = self._load()

    def record(self, guidance: dict | None, trades: list[dict], now: datetime) -> dict:
        with self._lock:
            changed = False
            if guidance:
                entry = _suggestion(guidance, now)
                previous = self._data["suggestions"][-1] if self._data["suggestions"] else None
                if previous is None or entry["key"] != previous["key"]:
                    self._data["suggestions"].append(entry)
                    changed = True
                elif entry["instruction"] != previous["instruction"]:
                    previous["instruction"] = entry["instruction"]
                    previous["why"] = entry["why"]
                    changed = True
            known = {item["id"] for item in self._data["operations"]}
            for trade in trades:
                operation = _operation(trade)
                if operation["id"] in known:
                    continue
                self._data["operations"].append(operation)
                known.add(operation["id"])
                changed = True
            if changed:
                self._save()
            return self._view(now.strftime("%Y-%m-%d"))

    def view(self, day: str) -> dict:
        with self._lock:
            return self._view(day)

    def _view(self, day: str) -> dict:
        suggestions = [_public_suggestion(item) for item in self._data["suggestions"]]
        operations = list(self._data["operations"])
        return {
            "today": day,
            "suggestions": suggestions,
            "operations": operations,
        }

    def _load(self) -> dict:
        if not self.path.exists():
            return {"suggestions": [], "operations": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"suggestions": [], "operations": []}
        payload.setdefault("suggestions", [])
        payload.setdefault("operations", [])
        return payload

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def _suggestion(guidance: dict, now: datetime) -> dict:
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    day = stamp[:10]
    key = "|".join(
        [
            str(guidance.get("action") or ""),
            str(guidance.get("title") or ""),
            str(guidance.get("code") or ""),
            _num(guidance.get("limit")),
            _num(guidance.get("qty")),
            str(guidance.get("valid_until") or ""),
            str(guidance.get("why") or ""),
        ]
    )
    return {
        "key": key,
        "ts": stamp,
        "day": day,
        "action": guidance.get("action") or "wait",
        "title": guidance.get("title") or "",
        "code": guidance.get("code") or "",
        "name": guidance.get("name") or "",
        "kind": guidance.get("kind") or "",
        "limit": guidance.get("limit"),
        "take_profit": guidance.get("take_profit"),
        "stop_loss": guidance.get("stop_loss"),
        "qty": guidance.get("qty") or 0,
        "valid_until": guidance.get("valid_until") or "",
        "why": guidance.get("why") or "",
        "instruction": _instruction(guidance),
    }


def _instruction(guidance: dict) -> str:
    action = guidance.get("action")
    code = guidance.get("code") or ""
    name = guidance.get("name") or ""
    limit = guidance.get("limit")
    take = guidance.get("take_profit")
    stop = guidance.get("stop_loss")
    qty = guidance.get("qty") or 0
    if action == "buy" and limit is not None:
        return (
            f"限价买入 {name} {code}，价格 {limit:.3f}，数量 {qty}，"
            f"有效到 {guidance.get('valid_until') or '下一根 5 分钟'}。"
            f"止盈 {take:.3f}，止损 {stop:.3f}。"
        )
    if action == "sell" and limit is not None:
        return f"卖出 {name} {code} {qty} 份。止盈 {take:.3f}，止损 {stop:.3f}。{guidance.get('why') or ''}"
    if action == "hold" and limit is not None:
        return f"持有 {name} {code}，成本 {limit:.3f}，止盈 {take:.3f}，止损 {stop:.3f}。不要加仓。"
    lines = []
    why = guidance.get("why") or "不下单"
    lines.append(why)
    for step in guidance.get("steps") or []:
        if step and step not in lines:
            lines.append(step)
    return " ".join(lines)


def _operation(trade: dict) -> dict:
    entry = str(trade.get("entry_ts") or "")
    exit_ts = str(trade.get("exit_ts") or "")
    code = str(trade.get("code") or "")
    reason = str(trade.get("reason") or "")
    return {
        "id": f"{code}|{entry}|{exit_ts}|{reason}",
        "day": entry[:10],
        "code": code,
        "kind": trade.get("kind") or "",
        "qty": trade.get("qty") or 0,
        "entry_ts": entry,
        "exit_ts": exit_ts,
        "fill": trade.get("fill"),
        "exit": trade.get("exit"),
        "pnl": None if trade.get("pnl") is None else round(float(trade["pnl"]), 2),
        "reason": reason,
        "source": "纸面",
    }


def _public_suggestion(item: dict) -> dict:
    shown = dict(item)
    shown.pop("key", None)
    return shown


def _num(value) -> str:
    if value is None or value == "":
        return ""
    return str(value)
