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

The same ranking is written to an Excel file beside the input. The default
name adds ``_次日排序`` before ``.xlsx``.

Example:
    python rank_qiandai.py 钱袋_20261013_收盘.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape


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


def default_xlsx_path(csv_path: str) -> Path:
    source = Path(csv_path)
    return source.with_name(source.stem + "_次日排序.xlsx")


_HEADERS = ("分组", "代码", "名称", "涨幅%", "收盘K10", "昨收K10", "口径", "行业", "分数", "说明")


def _col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _inline(ref: str, text: str, style: int) -> str:
    return (
        f'<c r="{ref}" t="inlineStr" s="{style}">'
        f'<is><t xml:space="preserve">{escape(text)}</t></is></c>'
    )


def _number(ref: str, value: float, style: int) -> str:
    return f'<c r="{ref}" s="{style}"><v>{value:.10g}</v></c>'


def _styles_xml() -> str:
    fonts = """
    <fonts count="2">
      <font><sz val="11"/><name val="Calibri"/></font>
      <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
    </fonts>"""
    fills = """
    <fills count="7">
      <fill><patternFill patternType="none"/></fill>
      <fill><patternFill patternType="gray125"/></fill>
      <fill><patternFill patternType="solid"><fgColor rgb="FF1F4E3D"/></patternFill></fill>
      <fill><patternFill patternType="solid"><fgColor rgb="FFE5F2EA"/></patternFill></fill>
      <fill><patternFill patternType="solid"><fgColor rgb="FFFFF6D8"/></patternFill></fill>
      <fill><patternFill patternType="solid"><fgColor rgb="FFF3F4F6"/></patternFill></fill>
      <fill><patternFill patternType="solid"><fgColor rgb="FFF8E8E8"/></patternFill></fill>
    </fills>"""
    xfs = [
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>',
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>',
    ]
    # 2 + bucket * 4 + kind, kind: 0 text, 1 percent, 2 K10, 3 score.
    number_formats = (49, 2, 164, 165)
    for fill_id in (3, 4, 5, 6):
        for num_fmt in number_formats:
            xfs.append(
                f'<xf numFmtId="{num_fmt}" fontId="0" fillId="{fill_id}" borderId="0" '
                f'xfId="0" applyNumberFormat="1" applyFill="1"/>'
            )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="2">
    <numFmt numFmtId="164" formatCode="0.000"/>
    <numFmt numFmtId="165" formatCode="0.0"/>
  </numFmts>
  {fonts}
  {fills}
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="{len(xfs)}">{''.join(xfs)}</cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>
"""


def _style_for(bucket: str, kind: int) -> int:
    order = ("优先", "其次", "弱区已走完", "排除")
    return 2 + order.index(bucket) * 4 + kind


def _ranking_sheet(names: list[Name]) -> str:
    rows = [
        "<row r=\"1\">"
        + "".join(_inline(f"{_col_name(i)}1", title, 1) for i, title in enumerate(_HEADERS, start=1))
        + "</row>"
    ]
    for offset, item in enumerate(names, start=2):
        text_style = _style_for(item.bucket, 0)
        cells = [
            _inline(f"A{offset}", item.bucket, text_style),
            _inline(f"B{offset}", item.code, text_style),
            _inline(f"C{offset}", item.name, text_style),
            _number(f"D{offset}", item.pct, _style_for(item.bucket, 1)),
            _number(f"E{offset}", item.k10, _style_for(item.bucket, 2)),
            _number(f"F{offset}", item.k10_prev, _style_for(item.bucket, 2)),
            _inline(f"G{offset}", item.caliber, text_style),
            _inline(f"H{offset}", item.industry, text_style),
            _number(f"I{offset}", item.score, _style_for(item.bucket, 3)),
            _inline(f"J{offset}", item.reason, text_style),
        ]
        rows.append(f'<row r="{offset}">{"".join(cells)}</row>')
    last = max(1, len(names) + 1)
    widths = (10, 10, 12, 10, 12, 12, 16, 14, 8, 28)
    cols = "".join(
        f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
        for i, width in enumerate(widths, start=1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:J{last}"/>
  <sheetViews><sheetView workbookViewId="0">
    <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
    <selection pane="bottomLeft" activeCell="A2" sqref="A2"/>
  </sheetView></sheetViews>
  <cols>{cols}</cols>
  <sheetData>{''.join(rows)}</sheetData>
  <autoFilter ref="A1:J{last}"/>
</worksheet>
"""


def _notes_sheet(header: list[str]) -> str:
    rows = []
    for offset, line in enumerate(header or ["（无说明）"], start=1):
        rows.append(f'<row r="{offset}">{_inline(f"A{offset}", line, 0)}</row>')
    last = max(1, len(header))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:A{last}"/>
  <cols><col min="1" max="1" width="88" customWidth="1"/></cols>
  <sheetData>{''.join(rows)}</sheetData>
</worksheet>
"""


def write_xlsx(path: str | Path, names: list[Name], header: list[str]) -> None:
    target = Path(path)
    package = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>
""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="排序" sheetId="1" r:id="rId1"/>
    <sheet name="说明" sheetId="2" r:id="rId2"/>
  </sheets>
</workbook>
""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
""",
        "xl/styles.xml": _styles_xml(),
        "xl/worksheets/sheet1.xml": _ranking_sheet(names),
        "xl/worksheets/sheet2.xml": _notes_sheet(header),
    }
    temporary = target.with_name(target.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in package.items():
                archive.writestr(name, payload)
        temporary.replace(target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SystemExit(f"写不了 Excel: {target} ({exc})") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给收盘弱钱袋清单排下一交易日的相对顺序")
    parser.add_argument("csv", help="收盘清单 CSV")
    parser.add_argument(
        "--hot",
        default="",
        help="覆盖自动算出的领涨行业，逗号分隔。默认从清单里的行业涨幅计算",
    )
    parser.add_argument(
        "--xlsx",
        default="",
        help="结果 Excel 路径。默认写到清单旁边，文件名加 _次日排序",
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
    xlsx_path = Path(args.xlsx) if args.xlsx.strip() else default_xlsx_path(args.csv)
    write_xlsx(xlsx_path, names, header)
    sys.stdout.write(format_report(names, header))
    sys.stderr.write(f"已写入 {xlsx_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
