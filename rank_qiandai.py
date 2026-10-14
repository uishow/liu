#!/usr/bin/env python3
"""Rank a close-of-day weak money-bag list for the next session.

The score follows the close-list screen used on these reports:

- keep names that are still a weak bag at the close
- drop ST names and names that closed down
- treat a gain of 4% or more as already spent
- treat a close K10 above 0.16 as having left the weak zone
- prefer a close K10 still under 0.08, a gain around 0.8% to 3.5%,
  a signal that held from the session into the close, and a
  meaningfully negative prior-close K10
- leading industries are inferred from the file. A family needs at
  least three non-ST names still under a 4% gain, a median gain of
  at least 1% in the top quarter of such families, a majority of
  those names green, and one name still inside the primary band.
  Pass --hot only to override that

Example:
    python rank_qiandai.py 钱袋_20261013_收盘.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass


EXTENDED_PCT = 4.0
WEAK_ZONE_K10 = 0.16
PRIMARY_K10 = 0.08
PRIMARY_PCT_LOW = 0.8
PRIMARY_PCT_HIGH = 3.5
PRIMARY_PREV_MAX = -0.05

# Longer keys must be tested by the caller via this order: specific families
# first, so 化学制药 stays in 医药 instead of 化工.
FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("医药", ("药", "医疗", "生物制品")),
    ("化工", ("化学", "化工", "塑料", "橡胶")),
    ("银行", ("银行",)),
    ("煤炭", ("煤炭", "焦炭")),
    ("电力公用", ("电力", "燃气", "水务")),
    ("食饮", ("食品", "酿酒", "乳品", "调味")),
    ("军工", ("军工", "航海装备", "航空装备", "地面兵装")),
    ("计算机", ("软件", "IT设备", "云服务")),
    ("传媒", ("广告", "游戏", "出版", "数字媒体", "影视")),
    ("交运", ("航运", "公路铁路", "物流", "航空机场")),
    ("钢铁", ("普钢", "工业金属")),
    ("零售", ("零售", "商业物业", "专业连锁")),
)


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


def family_of(industry: str) -> str:
    for label, keys in FAMILY_RULES:
        if any(key in industry for key in keys):
            return label
    return industry


@dataclass
class HotResult:
    industries: set[str]
    lines: list[str]


def in_primary_band(item: Name) -> bool:
    return (
        PRIMARY_K10 >= item.k10 >= 0.005
        and PRIMARY_PCT_LOW <= item.pct <= PRIMARY_PCT_HIGH
        and item.k10_prev <= PRIMARY_PREV_MAX
    )


def infer_hot(names: list[Name]) -> HotResult:
    """Mark industry families that led inside this close list.

    A family needs at least three non-ST names whose gain is still under 4%.
    Two names, or one name, are not an industry trend. The median gain has to
    be at least 1% and sit in the top quarter of those families, with at least
    half of the names closing green, and one name still inside the primary
    weak-zone band. Otherwise the turn has already left the list.
    """
    pool = [item for item in names if not is_st(item.name)]
    if not pool:
        return HotResult(set(), ["清单里没有可统计的行业。"])
    groups: dict[str, list[Name]] = defaultdict(list)
    for item in pool:
        groups[family_of(item.industry)].append(item)

    counted: list[tuple[str, float, float, list[Name]]] = []
    for family, items in groups.items():
        core = [item for item in items if item.pct < EXTENDED_PCT]
        if not core:
            continue
        median = statistics.median(item.pct for item in core)
        green = sum(item.pct > 0 for item in core) / len(core)
        counted.append((family, median, green, core))

    sized = [row for row in counted if len(row[3]) >= 3]
    hot_families: set[str] = set()
    spent: list[tuple[float, str, int]] = []
    cutoff = 1.0
    if sized:
        keep = max(1, (len(sized) + 3) // 4)
        ranked_medians = sorted((row[1] for row in sized), reverse=True)
        cutoff = max(ranked_medians[keep - 1], 1.0)
        for family, median, green, core in sized:
            if median < cutoff or green < 0.5:
                continue
            if any(in_primary_band(item) for item in core):
                hot_families.add(family)
            else:
                spent.append((median, family, len(core)))

    industries = {
        item.industry
        for item in pool
        if family_of(item.industry) in hot_families and item.industry
    }
    lines = [
        "领涨行业由清单算出（至少 3 只未透支，中位数处在前四分之一且不低于 1%，"
        "过半收阳，弱区里仍有标的）："
    ]
    if not hot_families:
        lines = ["清单里没有算出领涨行业，优先组只保留盘中和收盘都成立的标的。"]
    else:
        shown = []
        for family in hot_families:
            members = sorted({item.industry for item in groups[family] if item.industry})
            core = [item for item in groups[family] if item.pct < EXTENDED_PCT]
            median = statistics.median(item.pct for item in core)
            shown.append((median, len(core), family, members))
        for median, count, family, members in sorted(shown, reverse=True):
            label = family if members == [family] else f"{family}（{'、'.join(members)}）"
            lines.append(f"- {label} 中位数 {median:.2f}%（{count} 只）")

    thin = [
        (median, family, len(core))
        for family, median, green, core in counted
        if len(core) == 2 and median >= cutoff and green >= 0.5
    ]
    if thin:
        bits = [f"{family} {median:.2f}%（{count} 只）" for median, family, count in sorted(thin, reverse=True)]
        lines.append("样本不足 3 只，不单独成领涨：" + "、".join(bits))
    if spent:
        bits = [f"{family} {median:.2f}%（{count} 只）" for median, family, count in sorted(spent, reverse=True)]
        lines.append("中位数够，但弱区里已经没有优先标的：" + "、".join(bits))
    return HotResult(industries, lines)


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
    in_band = in_primary_band(item)
    sector_ok = (not hot) or item.industry in hot
    session_ok = both_sessions(item.caliber) or item.industry in hot
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


def format_report(names: list[Name], header: list[str]) -> str:
    lines = list(header)
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
        help="覆盖自动算出的领涨行业，逗号分隔。默认从清单里的行业涨幅计算",
    )
    args = parser.parse_args(argv)
    loaded = load_rows(args.csv)
    if args.hot.strip():
        hot = parse_hot(args.hot)
        header = ["领涨行业由参数指定: " + "、".join(sorted(hot))]
    else:
        inferred = infer_hot(loaded)
        hot = inferred.industries
        header = inferred.lines
    names = rank(loaded, hot)
    sys.stdout.write(format_report(names, header))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
