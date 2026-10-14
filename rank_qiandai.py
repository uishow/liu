#!/usr/bin/env python3
"""Rank a close-of-day weak money-bag list for the next session.

The score follows the close-list screen used on these reports:

- keep names that are still a weak bag at the close
- drop ST names and names that closed down
- treat a gain of 4.5% or more as already spent
- treat a close K10 above 0.16 as having left the weak zone
- prefer a close K10 still under 0.08, a gain around 0.8% to 3.5%,
  a signal that held from the session into the close, and a
  meaningfully negative prior-close K10
- if leading industries are passed with --hot, only those industries
  can land in the first group

Example:
    python rank_qiandai.py 钱袋_20261013_收盘.csv --hot 化学制品,化学原料,农用化工
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass


EXTENDED_PCT = 4.0
WEAK_ZONE_K10 = 0.16
PRIMARY_K10 = 0.08
PRIMARY_PCT_LOW = 0.8
PRIMARY_PCT_HIGH = 3.5


@dataclass
class Name:
    code: str
    name: str
    pct: float
    k10: float
    k10_prev: float
    caliber: str
    industry: str
    score: float = 0.0
    bucket: str = ""
    reason: str = ""


def _f(value: str) -> float:
    text = (value or "").strip().replace("%", "")
    if text == "":
        return 0.0
    return float(text)


def _pick(row: dict, *keys: str) -> str:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return str(row[key]).strip()
    return ""


def load_rows(path: str) -> list[Name]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit(f"空表: {path}")
        names: list[Name] = []
        for row in reader:
            code = _pick(row, "代码", "code").zfill(6)
            name = _pick(row, "名称", "name")
            if not code.strip("0") and not name:
                continue
            pct = _f(_pick(row, "当日涨幅%", "pct_chg", "pct"))
            k10 = _f(_pick(row, "k10收盘", "k10_close", "k10"))
            k10_prev = _f(_pick(row, "k10前收", "k10_prev_close", "k10前收盘"))
            caliber = _pick(row, "口径分类", "caliber")
            industry = _pick(row, "行业", "industry")
            names.append(
                Name(
                    code=code,
                    name=name,
                    pct=pct,
                    k10=k10,
                    k10_prev=k10_prev,
                    caliber=caliber,
                    industry=industry,
                )
            )
    if not names:
        raise SystemExit(f"没有读到标的: {path}")
    return names


def is_st(name: str) -> bool:
    folded = name.replace(" ", "").upper()
    return "ST" in folded


def both_sessions(caliber: str) -> bool:
    return "盘中+收盘" in caliber or caliber.startswith("①")


def pct_points(pct: float) -> float:
    if pct < 0.5:
        return 20 + pct * 40
    if pct <= 3.2:
        return 55 + 45 * (1 - abs(pct - 1.6) / 2.2)
    return max(10.0, 40 - (pct - 3.2) * 15)


def k10_points(k10: float) -> float:
    if k10 < 0.005:
        return 15.0
    if k10 <= 0.08:
        return 90 - (k10 - 0.03) ** 2 * 800
    if k10 <= 0.12:
        return 70 - (k10 - 0.08) * 400
    return max(20.0, 50 - (k10 - 0.12) * 300)


def prev_points(prev: float) -> float:
    if -0.70 <= prev <= -0.15:
        return 85.0
    if -1.20 <= prev < -0.70:
        return 70.0
    if -0.15 < prev <= -0.05:
        return 55.0
    if prev < -1.20:
        return 40.0
    return 35.0


def score_name(item: Name, hot: set[str]) -> None:
    if is_st(item.name):
        item.bucket = "排除"
        item.reason = "ST"
        return
    if item.pct < 0:
        item.bucket = "排除"
        item.reason = "收阴"
        return
    if item.pct >= EXTENDED_PCT:
        item.bucket = "排除"
        item.reason = "涨幅已透支"
        return
    if item.k10 > WEAK_ZONE_K10:
        item.bucket = "弱区已走完"
        item.reason = "收盘K10已过0.16"
        item.score = 0.0
        return

    caliber_bonus = 12.0 if both_sessions(item.caliber) else 0.0
    hot_bonus = 25.0 if item.industry in hot else 0.0
    item.score = (
        0.36 * pct_points(item.pct)
        + 0.32 * k10_points(item.k10)
        + 0.20 * prev_points(item.k10_prev)
        + caliber_bonus
        + hot_bonus
    )
    in_band = PRIMARY_K10 >= item.k10 >= 0.005 and PRIMARY_PCT_LOW <= item.pct <= PRIMARY_PCT_HIGH
    sector_ok = (not hot) or item.industry in hot
    session_ok = both_sessions(item.caliber) or bool(hot)
    if in_band and sector_ok and session_ok:
        item.bucket = "优先"
        item.reason = "弱区、涨幅未透支"
        if item.industry in hot:
            item.reason += "，行业在当日领涨方向"
    else:
        item.bucket = "其次"
        item.reason = "结构还在，弱区或涨幅或行业差一档"


def rank(names: list[Name], hot: set[str]) -> list[Name]:
    for item in names:
        score_name(item, hot)
    order = {"优先": 0, "其次": 1, "弱区已走完": 2, "排除": 3}
    return sorted(names, key=lambda item: (order[item.bucket], -item.score, item.code))


def display_width(text: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in text)


def pad(text: str, width: int, align: str = "left") -> str:
    gap = max(0, width - display_width(text))
    if align == "right":
        return " " * gap + text
    return text + " " * gap


def format_report(names: list[Name], hot: set[str]) -> str:
    lines = []
    if hot:
        lines.append("当日领涨行业: " + "、".join(sorted(hot)))
    else:
        lines.append("未指定领涨行业，优先组只要求盘中和收盘都成立。")
    lines.append("")
    for bucket in ("优先", "其次", "弱区已走完", "排除"):
        group = [item for item in names if item.bucket == bucket]
        lines.append(f"## {bucket} ({len(group)})")
        if not group:
            lines.append("(无)")
            lines.append("")
            continue
        if bucket == "排除":
            for item in group:
                lines.append(
                    f"{item.code} {item.name}  {item.pct:+.2f}%  K10 {item.k10:.3f}  {item.reason}"
                )
        else:
            lines.append(
                pad("代码", 8)
                + pad("名称", 12)
                + pad("涨幅", 8, "right")
                + pad("收盘K10", 10, "right")
                + pad("昨收K10", 10, "right")
                + "  "
                + pad("行业", 14)
                + pad("分数", 6, "right")
            )
            for item in group:
                lines.append(
                    pad(item.code, 8)
                    + pad(item.name, 12)
                    + pad(f"{item.pct:.2f}", 8, "right")
                    + pad(f"{item.k10:.3f}", 10, "right")
                    + pad(f"{item.k10_prev:.3f}", 10, "right")
                    + "  "
                    + pad(item.industry, 14)
                    + pad(f"{item.score:.1f}", 6, "right")
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_hot(text: str) -> set[str]:
    if not text.strip():
        return set()
    return {part.strip() for part in text.split(",") if part.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给收盘弱钱袋清单排下一交易日的相对顺序")
    parser.add_argument("csv", help="收盘清单 CSV")
    parser.add_argument(
        "--hot",
        default="",
        help="当日领涨行业，逗号分隔，例如 化学制品,化学原料,农用化工",
    )
    args = parser.parse_args(argv)
    names = rank(load_rows(args.csv), parse_hot(args.hot))
    sys.stdout.write(format_report(names, parse_hot(args.hot)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
