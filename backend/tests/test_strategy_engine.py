"""M3 策略引擎单测：指标正确性（手工对照）+ 条件树求值 + 内置策略回归。"""

import numpy as np
import pandas as pd
import pytest

from app.strategy import engine
from app.strategy import indicators as ind
from app.strategy.builtin import available_strategies
from app.strategy.factors import FACTOR_META, compute_factor_table, limit_threshold


def make_panel(closes_by_symbol: dict[str, list[float]]) -> tuple[pd.DataFrame, np.ndarray]:
    """用收盘价序列构造最小面板（开高低=收，量=100）。"""
    frames = []
    for sym, closes in closes_by_symbol.items():
        n = len(closes)
        frames.append(
            pd.DataFrame(
                {
                    "symbol": sym,
                    "trade_date": pd.date_range("2026-01-01", periods=n),
                    "open": closes,
                    "high": closes,
                    "low": closes,
                    "close": closes,
                    "volume": [100.0] * n,
                    "amount": [1000.0] * n,
                    "pct_change": pd.Series(closes).pct_change().fillna(0) * 100,
                    "turnover": [1.0] * n,
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    return df, ind.group_pos(df)


# ---------------- 指标正确性 ----------------


def test_sma_basic_and_group_isolation():
    """MA 计算正确，且第二只股票开头不被第一只污染。"""
    df, pos = make_panel({"A": [1, 2, 3, 4, 5], "B": [10, 20, 30]})
    ma3 = ind.sma(df["close"], pos, 3)
    # A 的 MA3：第3天 (1+2+3)/3=2，第5天 (3+4+5)/3=4
    assert ma3.iloc[2] == pytest.approx(2.0)
    assert ma3.iloc[4] == pytest.approx(4.0)
    # B 的前两行必须是 NaN（窗口不足；若跨组污染会算出错误值）
    assert pd.isna(ma3.iloc[5]) and pd.isna(ma3.iloc[6])
    assert ma3.iloc[7] == pytest.approx(20.0)


def test_shift_group_isolation():
    df, pos = make_panel({"A": [1, 2, 3], "B": [7, 8, 9]})
    prev = ind.shift(df["close"], pos, 1)
    assert pd.isna(prev.iloc[0]) and prev.iloc[1] == 1
    # B 第一行的 shift 必须是 NaN（不能拿到 A 的最后一价 3）
    assert pd.isna(prev.iloc[3]) and prev.iloc[4] == 7


def test_cross_above():
    """金叉：a 从下方上穿 b 当日为 True，持续在上方不再触发。"""
    df, pos = make_panel({"A": [1, 1, 1, 1, 1, 1]})
    a = pd.Series([1.0, 2.0, 3.0, 5.0, 6.0, 7.0])
    b = pd.Series([4.0, 4.0, 4.0, 4.0, 4.0, 4.0])
    cross = ind.cross_above(a, b, pos)
    assert not cross.iloc[2]  # 3 < 4 未穿
    assert cross.iloc[3]  # 3→5 上穿 4
    assert not cross.iloc[4]  # 已在上方，不重复触发


def test_rsi_manual_reference():
    """RSI 与手工 Wilder 平滑对照（容差 1e-6）。"""
    closes = [
        10,
        10.5,
        10.2,
        10.8,
        11.0,
        10.7,
        11.2,
        11.5,
        11.3,
        11.8,
        12.0,
        11.6,
        12.2,
        12.5,
        12.3,
        12.8,
        13.0,
    ]
    df, pos = make_panel({"A": [float(c) for c in closes] * 5})  # 拉长到 85 行越过 warmup
    rsi = ind.rsi(df["close"], df["symbol"], pos, 14)
    # 手工逐步计算最后一行的 Wilder RSI
    s = df["close"]
    delta = s.diff().fillna(0)
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    expect = 100 - 100 / (1 + gain.iloc[-1] / loss.iloc[-1])
    assert rsi.iloc[-1] == pytest.approx(expect, abs=1e-6)
    assert 0 <= rsi.iloc[-1] <= 100


def test_macd_matches_per_group_calc():
    """组感知 MACD 必须与逐组独立计算严格一致（无跨组污染）。"""
    rng = np.random.default_rng(7)
    a = (100 + np.cumsum(rng.normal(0, 1, 120))).tolist()
    b = (50 + np.cumsum(rng.normal(0, 1, 100))).tolist()
    df, pos = make_panel({"A": a, "B": b})
    dif, dea, hist = ind.macd(df["close"], df["symbol"], pos)

    for sym, series in (("A", a), ("B", b)):
        s = pd.Series(series)
        exp_dif = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
        exp_dea = exp_dif.ewm(span=9, adjust=False).mean()
        got = dif[df["symbol"] == sym].reset_index(drop=True)
        assert got.iloc[-1] == pytest.approx(exp_dif.iloc[-1], abs=1e-10)
        got_dea = dea[df["symbol"] == sym].reset_index(drop=True)
        assert got_dea.iloc[-1] == pytest.approx(exp_dea.iloc[-1], abs=1e-10)


def test_limit_threshold_rules():
    """涨停阈值：主板10/创业科创20/北交30/主板ST 5。"""
    syms = pd.Series(["600519", "300750", "688981", "430047", "600999"])
    is_st = pd.Series([False, False, False, False, True])
    th = limit_threshold(syms, is_st)
    assert th.tolist() == [9.8, 19.8, 19.8, 29.8, 4.8]


def test_within_and_streak():
    df, pos = make_panel({"A": [1.0] * 6})
    cond = pd.Series([False, True, False, False, False, False])
    within3 = ind.within_last(cond, pos, 3)
    assert within3.iloc[1] and within3.iloc[3]
    assert not within3.iloc[4]  # 第5天距发生日已超3日窗口
    streak2 = ind.streak_true(pd.Series([True, True, True, False, True, True]), pos, 2)
    assert streak2.iloc[1] and streak2.iloc[2]
    assert not streak2.iloc[3] and not streak2.iloc[4]
    assert streak2.iloc[5]


# ---------------- 因子表 ----------------


def _rich_panel() -> pd.DataFrame:
    """70 天稳步上涨的面板（满足多头排列），加一只下跌股做对照。"""
    up = [10 * (1.01**i) for i in range(80)]
    down = [50 * (0.99**i) for i in range(80)]
    df, _ = make_panel({"AAA": up, "BBB": down})
    return df


def test_factor_table_bull_arrange():
    panel = _rich_panel()
    basics = pd.DataFrame(
        {"symbol": ["AAA", "BBB"], "name": ["上涨股", "下跌股"], "is_st": [False, False]}
    )
    funds = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "pe_ttm": [12.0, -5.0],
            "pb": [0.8, 3.0],
            "total_mv": [200e8, 30e8],
        }
    )
    table = compute_factor_table(panel, basics, funds)
    assert table.at["AAA", "ma_bull_arrange"]  # 持续上涨 → 多头排列
    assert not table.at["BBB", "ma_bull_arrange"]
    assert bool(table.at["AAA", "above_ma20"])
    assert table.at["AAA", "pe_ttm"] == 12.0
    assert bool(table.at["AAA", "pb_recover"])  # PB 0.8<1 且站上20日线
    assert not bool(table.at["BBB", "pb_recover"])


# ---------------- 条件树引擎 ----------------


@pytest.fixture
def toy_table() -> pd.DataFrame:
    cols = {name: [0.0, 0.0] for name in FACTOR_META}
    t = pd.DataFrame(cols, index=["S1", "S2"])
    t["rsi14"] = [20.0, 60.0]
    t["pe_ttm"] = [10.0, 30.0]
    t["ma5"] = [11.0, 9.0]
    t["ma20"] = [10.0, 10.0]
    t["ma_bull_arrange"] = [True, False]
    return t


def test_engine_leaf_ops(toy_table):
    mask, leaves = engine.evaluate({"factor": "rsi14", "op": "<", "value": 25}, toy_table)
    assert mask.tolist() == [True, False]
    assert "RSI(14)" in leaves[0]["text"]

    mask, _ = engine.evaluate({"factor": "pe_ttm", "op": "between", "value": [0, 15]}, toy_table)
    assert mask.tolist() == [True, False]

    mask, _ = engine.evaluate({"factor": "ma5", "op": ">", "ref": "ma20"}, toy_table)
    assert mask.tolist() == [True, False]

    mask, _ = engine.evaluate({"factor": "ma_bull_arrange", "op": "is_true"}, toy_table)
    assert mask.tolist() == [True, False]


def test_engine_nested(toy_table):
    cond = {
        "any": [
            {
                "all": [
                    {"factor": "rsi14", "op": "<", "value": 25},
                    {"factor": "pe_ttm", "op": "<", "value": 15},
                ]
            },
            {"factor": "rsi14", "op": ">", "value": 55},
        ]
    }
    mask, leaves = engine.evaluate(cond, toy_table)
    assert mask.tolist() == [True, True]
    assert len(leaves) == 3


def test_engine_rejects_bad_tree(toy_table):
    with pytest.raises(engine.ConditionError):
        engine.evaluate({"factor": "not_exist", "op": ">", "value": 1}, toy_table)
    with pytest.raises(engine.ConditionError):
        engine.evaluate({"factor": "rsi14", "op": "~~", "value": 1}, toy_table)
    with pytest.raises(engine.ConditionError):
        engine.evaluate({"all": []}, toy_table)


def test_describe_hit(toy_table):
    cond = {"all": [{"factor": "rsi14", "op": "<", "value": 25}]}
    _, leaves = engine.evaluate(cond, toy_table)
    reasons = engine.describe_hit("S1", leaves, toy_table)
    assert reasons[0].startswith("✓") and "20" in reasons[0]
    reasons = engine.describe_hit("S2", leaves, toy_table)
    assert reasons[0].startswith("✗")


# ---------------- 内置策略回归 ----------------


def test_all_builtin_strategies_evaluate(toy_table):
    """每个可用策略的条件树都必须能在因子表上求值（防因子名打错）。"""
    for spec in available_strategies():
        if spec.get("special"):
            continue  # 板块类特殊策略不走条件树
        mask, leaves = engine.evaluate(spec["condition"], toy_table)
        assert len(mask) == 2, spec["id"]
        assert leaves, spec["id"]
