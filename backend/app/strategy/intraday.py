"""盘中因子：从当日 5 分钟K线计算"日内走势"类因子。

数据从接入日起逐日积累（无完整历史），所以这些因子全部归入扩展因子
（ext=True），引用它们的策略暂不支持历史回测——与 M5 扩展数据同一诚实原则。

计算原则（学自 TradingAgents-AShare）：所有数值在这里用 pandas 向量化算好，
策略引擎与 AI 只消费结果，不做算术。
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 盘中因子元数据：factors.FACTOR_META 在模块加载时合并这份注册表
INTRADAY_META: dict[str, dict] = {
    "late30_pct": {
        "label": "尾盘30分钟涨幅",
        "unit": "%",
        "kind": "number",
        "ext": True,
        "desc": "14:30 至收盘的涨跌幅（5分钟线精确计算），>1.5% 即尾盘拉升、<-1.5% 即尾盘跳水",
    },
    "late_vol_pct": {
        "label": "尾盘量能占比",
        "unit": "%",
        "kind": "number",
        "ext": True,
        "desc": "最后30分钟成交量占全天的百分比，均匀分布约 12.5%，>20% 说明尾盘有资金集中行动",
    },
    "am60_pct": {
        "label": "早盘1小时涨幅",
        "unit": "%",
        "kind": "number",
        "ext": True,
        "desc": "开盘到 10:30 的涨跌幅，>2% 即早盘强势抢筹",
    },
    "above_vwap": {
        "label": "收盘站上分时均价",
        "kind": "bool",
        "ext": True,
        "desc": "收盘价高于全天成交均价（VWAP），全天买方占优的标志",
    },
    "vwap_climb": {
        "label": "分时重心上移",
        "kind": "bool",
        "ext": True,
        "desc": "下午成交均价高于上午成交均价，资金全天持续抬轿而非冲高回落",
    },
}


def compute_intraday_factors(minute_df: pd.DataFrame) -> pd.DataFrame:
    """当日全市场分钟线面板 → 盘中因子宽表（index=symbol）。

    minute_df 列：symbol, dt(Timestamp), open, high, low, close, volume, amount，
    已按 (symbol, dt) 排序（market_store.minute_day_df 保证）。

    停牌/数据不全的股票：相关因子为 NaN/False，条件树对 NaN 比较恒 False，
    自然实现"没有数据就不命中"。
    """
    if minute_df.empty:
        return pd.DataFrame(
            columns=list(INTRADAY_META.keys()), index=pd.Index([], name="symbol")
        )

    df = minute_df
    hhmm = df["dt"].dt.strftime("%H:%M")

    g = df.groupby("symbol", sort=False)
    last_close = g["close"].last()
    first_open = g["open"].first()
    vol_total = g["volume"].sum()
    amt_total = g["amount"].sum()

    # 尾盘 30 分钟 = 14:30 之后的 6 根；基准价 = 14:30(含)前最后一根收盘
    before_1430 = df[hhmm <= "14:30"]
    c1430 = before_1430.groupby("symbol", sort=False)["close"].last()
    late = df[hhmm > "14:30"]
    late_vol = late.groupby("symbol", sort=False)["volume"].sum()

    # 早盘 1 小时 = 10:30(含)前的最后收盘 vs 开盘价
    am_end = df[hhmm <= "10:30"]
    c1030 = am_end.groupby("symbol", sort=False)["close"].last()

    # 上/下午成交均价（VWAP，元/股；volume 单位是手 → ×100 股）
    am = df[hhmm <= "11:30"]
    pm = df[hhmm > "11:30"]
    am_g = am.groupby("symbol", sort=False)
    pm_g = pm.groupby("symbol", sort=False)
    am_vwap = am_g["amount"].sum() / (am_g["volume"].sum() * 100).replace(0, np.nan)
    pm_vwap = pm_g["amount"].sum() / (pm_g["volume"].sum() * 100).replace(0, np.nan)
    day_vwap = amt_total / (vol_total * 100).replace(0, np.nan)

    out = pd.DataFrame(index=last_close.index)
    out["late30_pct"] = (last_close / c1430.reindex(out.index) - 1) * 100
    out["late_vol_pct"] = late_vol.reindex(out.index) / vol_total.replace(0, np.nan) * 100
    out["am60_pct"] = (c1030.reindex(out.index) / first_open.replace(0, np.nan) - 1) * 100
    out["above_vwap"] = (last_close > day_vwap).fillna(False)
    out["vwap_climb"] = (
        (pm_vwap.reindex(out.index) > am_vwap.reindex(out.index)).fillna(False)
    )
    out.index.name = "symbol"
    return out


def attach_intraday_factors(wide: pd.DataFrame, intraday: pd.DataFrame | None) -> pd.DataFrame:
    """把盘中因子挂到选股宽表上（原地修改并返回）。

    没有分钟数据（未同步/非交易日）时全部置 NaN/False，策略自然不命中。
    """
    if intraday is not None and len(intraday):
        for col in INTRADAY_META:
            if col in intraday.columns:
                wide[col] = intraday[col].reindex(wide.index)
        # 布尔因子 reindex 产生的 NaN 统一回 False（条件树 is_true 需要纯布尔）
        for col, meta in INTRADAY_META.items():
            if meta.get("kind") == "bool" and col in wide.columns:
                wide[col] = wide[col].fillna(False).astype(bool)
    else:
        for col, meta in INTRADAY_META.items():
            wide[col] = False if meta.get("kind") == "bool" else np.nan
    return wide
