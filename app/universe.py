"""Tradable universe from the T+0 ETF playbook."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    code: str
    name: str
    exchange: str  # SH / SZ
    class_id: str  # qdii / domestic
    style: str
    note: str

    @property
    def instrument_id(self) -> str:
        return f"{self.code}.{self.exchange}"

    @property
    def tdx_code(self) -> str:
        prefix = "sh" if self.exchange == "SH" else "sz"
        return f"{prefix}{self.code}"

    @property
    def tencent_code(self) -> str:
        return self.tdx_code


UNIVERSE: tuple[Instrument, ...] = (
    Instrument("513180", "恒生科技ETF", "SH", "qdii", "激进", "高弹性跨境，价位低时最小变动价位会吞掉利润"),
    Instrument("513330", "恒生互联网ETF", "SH", "qdii", "激进", "中概主线，流动性高，低价位同样受跳点约束"),
    Instrument("518880", "华安黄金ETF", "SH", "domestic", "稳健", "无外盘隔夜跳空，折溢价稳定"),
    Instrument("513500", "博时标普500ETF", "SH", "qdii", "稳健", "美股大盘，波动偏低"),
    Instrument("159941", "纳指ETF", "SZ", "qdii", "激进", "纳指弹性备选"),
    Instrument("513660", "恒生ETF", "SH", "qdii", "波段", "港股大盘，波动温和"),
    Instrument("511380", "可转债ETF", "SH", "domestic", "稳健", "境内品种，无外盘时差"),
    Instrument("159726", "恒生红利ETF", "SZ", "qdii", "波段", "高股息，弹性低"),
    Instrument("159557", "恒生医疗ETF", "SZ", "qdii", "激进", "题材弹性，阶段性行情"),
    Instrument("513100", "标普500ETF", "SH", "qdii", "稳健", "美股大盘备选"),
)

BY_CODE = {item.code: item for item in UNIVERSE}


def classify(code: str) -> str:
    item = BY_CODE.get(code)
    return item.class_id if item else "qdii"
