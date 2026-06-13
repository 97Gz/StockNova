"""回测引擎单元测试：用手算可验证的微型行情验证交易规则与绩效口径。

所有期望值均为手工推导（见各用例注释），不依赖引擎自身输出。
"""

import numpy as np
import pandas as pd
import pytest

from app.backtest import engine
from app.backtest.engine import (
    RebalanceParams,
    SnapshotParams,
    StrategyPack,
    TradeCosts,
)

# 简化费率便于手算：买千分之0.25、卖千分之0.75（与默认一致）
COSTS = TradeCosts(buy=0.00025, sell=0.00075)

# 测试用条件：above_ma20 为真（FACTOR_META 中存在的布尔因子）
COND = {"factor": "above_ma20", "op": "is_true"}


def make_pack() -> StrategyPack:
    return StrategyPack(conditions=[("测试策略", COND)])


# ---------------------------------------------------------------- 策略时光机


def snapshot_fixture():
    """构造 4 只股票的微型面板（信号日 2025-01-10，之后 3 个交易日）。

    - 600001 命中：次日开盘 10.1 正常买入
    - 600002 命中：昨收 20，次日开盘 22（+10%≈涨停开盘）→ 应跳过
    - 600003 命中：信号日后无任何 bar（停牌）→ 应跳过
    - 600004 不命中（above_ma20=False）
    """
    days = ["2025-01-13", "2025-01-14", "2025-01-15"]  # 信号日之后的交易日

    rows = []
    # 600001：开盘 10.1，收盘 10.5 / 11.0 / 10.8
    closes = [10.5, 11.0, 10.8]
    for d, c in zip(days, closes, strict=True):
        rows.append(("600001", d, 10.1 if d == days[0] else c, c))
    # 600002：开盘即 22（昨收 20 的 +10%）
    rows.append(("600002", days[0], 22.0, 22.0))
    # 600003：无信号日后数据（停牌）→ 不添加行
    # 600004：未命中，引擎不应读它的后续行情
    rows.append(("600004", days[0], 5.0, 5.0))

    panel = pd.DataFrame(
        [
            {
                "symbol": s,
                "trade_date": d,
                "open": o,
                "high": max(o, c),
                "low": min(o, c),
                "close": c,
                "volume": 1e6,
                "amount": 1e8,
                "pct_change": 0.0,
                "turnover": 1.0,
            }
            for s, d, o, c in rows
        ]
    )

    factor_table = pd.DataFrame(
        {
            "above_ma20": [True, True, True, False],
            "close": [10.0, 20.0, 30.0, 5.0],  # 信号日收盘
            "amount_yi": [5.0, 4.0, 3.0, 2.0],
            "is_st": [False, False, False, False],
        },
        index=["600001", "600002", "600003", "600004"],
    )

    names = {"600001": "甲", "600002": "乙", "600003": "丙", "600004": "丁"}
    benchmark = pd.DataFrame(
        {
            "date": ["2025-01-10", *days],
            "close": [3000.0, 3030.0, 3060.0, 3090.0],
        }
    )
    return panel, factor_table, names, benchmark


def test_snapshot_trade_rules_and_returns():
    panel, table, names, bench = snapshot_fixture()
    result = engine.run_snapshot(
        panel,
        table,
        make_pack(),
        SnapshotParams(signal_date="2025-01-10", hold_days=[2, 5]),
        names,
        bench,
        costs=COSTS,
    )

    # 命中 3 只（600004 未命中），其中 1 只涨停开盘跳过、1 只停牌跳过
    assert result["total_hits"] == 3
    assert result["skipped_limit_up"] == 1
    assert result["skipped_suspended"] == 1
    assert result["evaluated"] == 1

    row = result["details"][0]
    assert row["symbol"] == "600001"
    assert row["buy_open"] == 10.1

    # 持有 2 日：买价 10.1×1.00025，卖价 11.0×0.99925
    buy = 10.1 * 1.00025
    sell = 11.0 * 0.99925
    expect_2d = (sell / buy - 1) * 100
    assert row["returns"]["2"]["pct"] == pytest.approx(expect_2d, abs=0.01)
    assert row["returns"]["2"]["holding"] is False

    # 持有 5 日：数据只有 3 根 → 按最后收盘 10.8 计浮盈，holding=True
    sell5 = 10.8 * 0.99925
    expect_5d = (sell5 / buy - 1) * 100
    assert row["returns"]["5"]["pct"] == pytest.approx(expect_5d, abs=0.01)
    assert row["returns"]["5"]["holding"] is True

    # 汇总：持有 2 日仅 1 笔有效交易，胜率 100%
    s2 = result["summary"]["2"]
    assert s2["trades"] == 1
    assert s2["win_rate"] == 100.0
    assert s2["avg_pct"] == pytest.approx(expect_2d, abs=0.01)
    # 基准：3000 → 第 2 个交易日收盘 3060，+2%
    assert s2["benchmark_pct"] == pytest.approx(2.0, abs=0.01)

    # 持有 5 日：无已了结交易（唯一一只仍在持有）
    s5 = result["summary"]["5"]
    assert s5["trades"] == 0
    assert s5["win_rate"] is None


def test_snapshot_require_all_resonance():
    """共振 AND 模式：两个条件都满足才入选。"""
    panel, table, names, bench = snapshot_fixture()
    table["vol_dry"] = [True, False, False, False]  # 只有 600001 同时满足
    pack = StrategyPack(
        conditions=[
            ("条件A", COND),
            ("条件B", {"factor": "vol_dry", "op": "is_true"}),
        ],
        require_all=True,
    )
    result = engine.run_snapshot(
        panel, table, pack,
        SnapshotParams(signal_date="2025-01-10", hold_days=[2]),
        names, bench, costs=COSTS,
    )
    assert result["total_hits"] == 1
    assert result["details"][0]["symbol"] == "600001"


# ---------------------------------------------------------------- 定期调仓


def rebalance_fixture():
    """两只股票、6 个交易日、第 0/3 日调仓（freq=3, top=1）的手算场景。

    甲(600001)：全程 10 元横盘。
    乙(600002)：d4 开盘 20、收盘 20；d5 收盘 22（+10%）。
    决策：d0 选甲（d1 开盘买入）；d3 换乙（d4 开盘卖甲买乙）。

    手算账本（佣金买 0.025%、卖 0.075%）：
      d1 买甲：10×1.00025=10.0025/股 → 100000/10.0025=9997.5 → 整百 9900 股，
               花费 99024.75，余现金 975.25 → d1 收盘净值 99975.25
      d4 卖甲：9900×10×0.99925=98925.75 → 现金 99901.00
         买乙：20×1.00025=20.005 → 99901/20.005=4994.05 → 4900 股，
               花费 98024.50，余现金 1876.50 → d4 收盘净值 99876.50
      d5 乙收盘 22 → 净值 1876.50+4900×22=109676.50
    """
    dates = [f"2025-02-{10 + i:02d}" for i in range(6)]  # d0..d5

    rows = []
    for i, d in enumerate(dates):
        rows.append({"symbol": "600001", "trade_date": d, "open": 10.0, "close": 10.0})
        b_open = 20.0
        b_close = 22.0 if i == 5 else 20.0
        rows.append({"symbol": "600002", "trade_date": d, "open": b_open, "close": b_close})
    px = pd.DataFrame(rows)
    open_wide = px.pivot(index="trade_date", columns="symbol", values="open")
    close_wide = px.pivot(index="trade_date", columns="symbol", values="close").ffill()

    # 因子长表：d0 甲命中；d3 乙命中（其余日期不调仓，值无关紧要）
    frows = []
    for d in dates:
        frows.append(
            {
                "symbol": "600001",
                "trade_date": d,
                "above_ma20": d == dates[0],
                "close": 10.0,
                "amount_yi": 1.0,
                "is_st": False,
            }
        )
        frows.append(
            {
                "symbol": "600002",
                "trade_date": d,
                "above_ma20": d == dates[3],
                "close": 20.0,
                "amount_yi": 1.0,
                "is_st": False,
            }
        )
    factor_long = pd.DataFrame(frows)

    names = {"600001": "甲", "600002": "乙"}
    benchmark = pd.DataFrame({"date": dates, "close": np.linspace(3000, 3050, 6)})
    return factor_long, open_wide, close_wide, names, benchmark, dates


def test_rebalance_ledger_matches_hand_calc():
    factor_long, open_wide, close_wide, names, bench, dates = rebalance_fixture()
    result = engine.run_rebalance(
        factor_long, open_wide, close_wide,
        make_pack(),
        RebalanceParams(start=dates[0], end=dates[-1], freq_days=3, top_n=1, init_cash=100_000),
        names, bench, st_map={}, costs=COSTS,
    )

    curve = {c["date"]: c["value"] for c in result["curve"]}
    assert curve[dates[0]] == 100_000.0  # d0 决策日收盘：仍全现金
    assert curve[dates[1]] == pytest.approx(99_975.25, abs=0.01)
    assert curve[dates[3]] == pytest.approx(99_975.25, abs=0.01)
    assert curve[dates[4]] == pytest.approx(99_876.50, abs=0.01)
    assert curve[dates[5]] == pytest.approx(109_676.50, abs=0.01)

    assert result["final_value"] == pytest.approx(109_676.50, abs=0.01)
    assert result["metrics"]["total_return_pct"] == pytest.approx(9.68, abs=0.01)
    assert result["metrics"]["rebalance_count"] == 2

    # 交易明细：d1 买甲 9900 股；d4 卖甲、买乙 4900 股
    t1, t2 = result["trades"]
    assert t1["exec_date"] == dates[1]
    assert t1["buys"][0] == {"symbol": "600001", "name": "甲", "price": 10.0, "shares": 9900}
    assert t2["exec_date"] == dates[4]
    assert t2["sells"][0]["symbol"] == "600001"
    assert t2["buys"][0]["shares"] == 4900

    # 周期胜率：周期1（d0→d3）亏损、周期2（d3→末）盈利 → 50%
    assert result["metrics"]["period_win_rate"] == 50.0


def test_rebalance_limit_down_sell_deferred():
    """开盘跌停卖不出：当日顺延，次日恢复后成交。"""
    factor_long, open_wide, close_wide, names, bench, dates = rebalance_fixture()
    # d4 甲开盘跌停（-9.7% < -9.3 阈值），d5 恢复 10 元开盘
    open_wide.loc[dates[4], "600001"] = 9.03
    close_wide.loc[dates[4], "600001"] = 9.03

    result = engine.run_rebalance(
        factor_long, open_wide, close_wide,
        make_pack(),
        RebalanceParams(start=dates[0], end=dates[-1], freq_days=3, top_n=1, init_cash=100_000),
        names, bench, st_map={}, costs=COSTS,
    )

    # d4 卖出失败 → 当日交易记录里没有甲的卖单
    d4_trades = [t for t in result["trades"] if t["exec_date"] == dates[4]]
    assert all(not t["sells"] for t in d4_trades)
    # d5 顺延卖出成功（开盘 10 元，相对 d4 收盘 9.03 上涨，非跌停）
    d5_trades = [t for t in result["trades"] if t["exec_date"] == dates[5]]
    assert len(d5_trades) == 1 and d5_trades[0]["sells"][0]["symbol"] == "600001"


def test_rebalance_no_candidates_clears_position():
    """调仓日无任何候选 → 清仓决策，次日开盘卖出全部。"""
    factor_long, open_wide, close_wide, names, bench, dates = rebalance_fixture()
    factor_long.loc[
        (factor_long["trade_date"] == dates[3]), "above_ma20"
    ] = False  # d3 没有任何命中

    result = engine.run_rebalance(
        factor_long, open_wide, close_wide,
        make_pack(),
        RebalanceParams(start=dates[0], end=dates[-1], freq_days=3, top_n=1, init_cash=100_000),
        names, bench, st_map={}, costs=COSTS,
    )
    # d4 应卖出甲且不买任何股票，之后净值=纯现金不再波动
    t_d4 = next(t for t in result["trades"] if t["exec_date"] == dates[4])
    assert t_d4["sells"][0]["symbol"] == "600001" and not t_d4["buys"]
    curve = {c["date"]: c["value"] for c in result["curve"]}
    assert curve[dates[4]] == curve[dates[5]] == pytest.approx(99_901.0, abs=0.01)


def test_rebalance_max_drawdown():
    """最大回撤口径：从峰值回落的最大比例。"""
    factor_long, open_wide, close_wide, names, bench, dates = rebalance_fixture()
    # 乙 d5 暴跌至 16（持仓市值从 d4 的 98000 跌到 78400）
    close_wide.loc[dates[5], "600002"] = 16.0

    result = engine.run_rebalance(
        factor_long, open_wide, close_wide,
        make_pack(),
        RebalanceParams(start=dates[0], end=dates[-1], freq_days=3, top_n=1, init_cash=100_000),
        names, bench, st_map={}, costs=COSTS,
    )
    # 峰值 100000（d0），d5 净值 1876.5+4900×16=80276.5 → 回撤 19.72%
    assert result["metrics"]["max_drawdown_pct"] == pytest.approx(19.72, abs=0.05)


def test_strategy_pack_or_and_semantics():
    """OR 模式命中任一即入选；AND 模式必须全中。"""
    table = pd.DataFrame(
        {
            "above_ma20": [True, False],
            "vol_dry": [False, True],
            "close": [10, 20],
            "amount_yi": [1, 1],
            "is_st": [False, False],
        },
        index=["600001", "600002"],
    )
    conds = [
        ("A", {"factor": "above_ma20", "op": "is_true"}),
        ("B", {"factor": "vol_dry", "op": "is_true"}),
    ]
    or_mask, or_hits = StrategyPack(conditions=conds).evaluate(table)
    assert or_mask.tolist() == [True, True]
    assert or_hits.tolist() == [1, 1]
    and_mask, _ = StrategyPack(conditions=conds, require_all=True).evaluate(table)
    assert and_mask.tolist() == [False, False]
