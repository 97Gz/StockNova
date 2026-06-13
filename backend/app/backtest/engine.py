"""回测引擎核心（纯计算，不碰数据库）。

两种模式：
1. 策略时光机 run_snapshot —— "假如在历史某天按策略买入，持有 N 天结果如何"
2. 定期调仓 run_rebalance —— "每隔 N 个交易日按策略换仓，资金曲线长什么样"

A 股现实约束（回测可信度的关键）：
- 信号在收盘后产生 → 一律次日开盘成交（杜绝未来函数）
- 次日开盘≈涨停价 → 买不进，跳过（一字板追不上是散户之痛）
- 开盘≈跌停价 → 卖不出，顺延到下个能卖的交易日
- 手续费：买入佣金 0.025%；卖出佣金 0.025% + 印花税 0.05%
- A 股一手 = 100 股，买入按整百股取整，钱不够一手就放弃

已知近似（结果页需向用户标注）：
- 基本面因子（PE/PB/市值）用最新快照回填历史 —— 估值快照从 M1 上线才开始积累
- 时光机持有期满按当日收盘价卖出，不模拟收盘跌停卖不出的极端情况
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.strategy import engine as cond_engine
from app.strategy.factors import limit_threshold

logger = logging.getLogger(__name__)

# 双边费率（可被请求参数覆盖）
BUY_COST = 0.00025  # 买入佣金 0.025%
SELL_COST = 0.00075  # 卖出佣金 0.025% + 印花税 0.05%

# 开盘涨/跌停判定的容差（百分点）：开盘涨幅 >= 阈值-0.5 视为封板开盘
LIMIT_TOLERANCE = 0.5


@dataclass
class StrategyPack:
    """一组待回测的策略条件（内置多选 + 可选自定义树，与策略广场同语义）。"""

    conditions: list[tuple[str, dict]]  # [(策略名, 条件树), ...]
    require_all: bool = False

    def evaluate(self, table: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """对因子宽表求值 → (入选掩码, 命中数)。"""
        hit_count = pd.Series(0, index=table.index)
        for _, cond in self.conditions:
            mask, _ = cond_engine.evaluate(cond, table)
            hit_count = hit_count + mask.astype(int)
        need = len(self.conditions) if self.require_all else 1
        return hit_count >= need, hit_count


@dataclass
class TradeCosts:
    buy: float = BUY_COST
    sell: float = SELL_COST


# ---------------------------------------------------------------- 策略时光机


@dataclass
class SnapshotParams:
    signal_date: str
    hold_days: list[int] = field(default_factory=lambda: [5, 10, 20])
    max_stocks: int = 200  # 防止命中过多导致响应过大


def run_snapshot(
    panel: pd.DataFrame,
    factor_table: pd.DataFrame,
    pack: StrategyPack,
    params: SnapshotParams,
    names: dict[str, str],
    benchmark: pd.DataFrame,
    costs: TradeCosts | None = None,
) -> dict[str, Any]:
    """策略时光机。

    panel：覆盖 [signal_date, signal_date + max(hold_days)+缓冲] 的 QFQ 面板
    factor_table：signal_date 当日的因子宽表（选股模式，index=symbol）
    benchmark：基准指数收盘序列（date/close），用于对照同期收益
    """
    costs = costs or TradeCosts()
    mask, hit_count = pack.evaluate(factor_table)
    picked = factor_table[mask].copy()
    picked["hit_count"] = hit_count[mask]
    # 命中多的优先；平手按成交额（流动性）排，结果确定可复现
    picked = picked.sort_values(["hit_count", "amount_yi"], ascending=False)
    symbols = picked.index.tolist()[: params.max_stocks]

    # 每只股票 signal_date 之后的 bars（按日期升序）
    after = panel[panel["trade_date"] > params.signal_date]
    grouped = dict(tuple(after.groupby("symbol", sort=False)))

    st_flags = (
        picked["is_st"].astype(bool) if "is_st" in picked else pd.Series(False, index=picked.index)
    )
    thresholds = limit_threshold(
        pd.Series(symbols, index=symbols), st_flags.reindex(symbols).fillna(False)
    )

    details: list[dict] = []
    skipped_suspend = 0
    skipped_limit = 0
    for sym in symbols:
        bars = grouped.get(sym)
        signal_close = float(picked.at[sym, "close"])
        if bars is None or len(bars) == 0:
            skipped_suspend += 1
            continue
        bars = bars.reset_index(drop=True)
        buy_open = float(bars.at[0, "open"])
        open_gain = (buy_open / signal_close - 1) * 100
        if open_gain >= float(thresholds[sym]) - LIMIT_TOLERANCE:
            skipped_limit += 1
            continue
        buy_price = buy_open * (1 + costs.buy)

        row: dict[str, Any] = {
            "symbol": sym,
            "name": names.get(sym, sym),
            "hit_count": int(picked.at[sym, "hit_count"]),
            "signal_close": round(signal_close, 3),
            "buy_open": round(buy_open, 3),
            "returns": {},
        }
        for n in params.hold_days:
            if len(bars) >= n:
                sell_close = float(bars.at[n - 1, "close"])
                holding = False
            else:
                sell_close = float(bars["close"].iloc[-1])  # 数据不足：按最后收盘浮动盈亏
                holding = True
            sell_price = sell_close * (1 - costs.sell)
            ret = (sell_price / buy_price - 1) * 100
            row["returns"][str(n)] = {"pct": round(ret, 2), "holding": holding}
        details.append(row)

    # 每个持有期的汇总 + 基准对照
    bench_close = benchmark.set_index("date")["close"] if len(benchmark) else pd.Series(dtype=float)
    bench_dates = bench_close.index.tolist()
    summary: dict[str, dict] = {}
    for n in params.hold_days:
        rets = [d["returns"][str(n)]["pct"] for d in details if not d["returns"][str(n)]["holding"]]
        arr = np.array(rets) if rets else np.array([0.0])
        has = len(rets) > 0
        # 基准同期收益：signal_date 收盘 → 之后第 n 个交易日收盘
        bench_ret = None
        if params.signal_date in bench_dates:
            i = bench_dates.index(params.signal_date)
            if i + n < len(bench_dates):
                bench_ret = round((bench_close.iloc[i + n] / bench_close.iloc[i] - 1) * 100, 2)
        summary[str(n)] = {
            "trades": len(rets),
            "win_rate": round(float((arr > 0).mean()) * 100, 1) if has else None,
            "avg_pct": round(float(arr.mean()), 2) if has else None,
            "median_pct": round(float(np.median(arr)), 2) if has else None,
            "best_pct": round(float(arr.max()), 2) if has else None,
            "worst_pct": round(float(arr.min()), 2) if has else None,
            "benchmark_pct": bench_ret,
        }

    return {
        "signal_date": params.signal_date,
        "hold_days": params.hold_days,
        "total_hits": int(mask.sum()),
        "evaluated": len(details),
        "skipped_limit_up": skipped_limit,
        "skipped_suspended": skipped_suspend,
        "summary": summary,
        "details": details,
    }


# ---------------------------------------------------------------- 定期调仓


@dataclass
class RebalanceParams:
    start: str
    end: str
    freq_days: int = 5  # 每 N 个交易日调一次仓
    top_n: int = 10
    init_cash: float = 100_000.0


def run_rebalance(
    factor_long: pd.DataFrame,
    open_wide: pd.DataFrame,
    close_wide: pd.DataFrame,
    pack: StrategyPack,
    params: RebalanceParams,
    names: dict[str, str],
    benchmark: pd.DataFrame,
    st_map: dict[str, bool],
    costs: TradeCosts | None = None,
) -> dict[str, Any]:
    """定期调仓回测。

    factor_long：全日期因子长表（含 symbol/trade_date 列，仅保留所需因子）
    open_wide / close_wide：date × symbol 的开盘/收盘宽表（close 已向前填充补停牌）
    流程：调仓日收盘选股 → 次日开盘换仓 → 每日收盘 mark-to-market。
    """
    costs = costs or TradeCosts()
    dates: list[str] = [d for d in close_wide.index if params.start <= d <= params.end]
    if len(dates) < 2:
        raise ValueError("回测区间内交易日不足")
    rebalance_set = set(dates[:: params.freq_days])

    # 调仓日 → 因子切片（只在调仓日做，避免无谓分组）
    by_date = {
        d: g.set_index("symbol")
        for d, g in factor_long.groupby("trade_date", sort=False)
        if d in rebalance_set
    }

    cash = params.init_cash
    holdings: dict[str, float] = {}  # symbol -> shares
    last_price: dict[str, float] = {}  # 最近已知收盘价（停牌估值 + 跌停判定参照）
    curve: list[dict] = []
    trades_log: list[dict] = []
    period_marks: list[float] = []  # 每个调仓周期起点净值（算周期胜率）

    # 待执行状态：昨日收盘做的决策，今日开盘成交（杜绝未来函数的关键）
    pending: dict | None = None  # {"signal_date", "target": list, "signal_close": dict}
    defer_sell: set[str] = set()  # 跌停/停牌没卖掉的，此后每日开盘重试

    def threshold_of(sym: str) -> float:
        return float(
            limit_threshold(
                pd.Series([sym], index=[sym]),
                pd.Series([st_map.get(sym, False)], index=[sym]),
            ).iloc[0]
        )

    def try_sell(sym: str, d: str, sells: list[dict]) -> bool:
        """开盘卖出；停牌（无开盘价）或开盘跌停则失败。"""
        nonlocal cash
        op = open_wide.at[d, sym] if sym in open_wide.columns else np.nan
        prev = last_price.get(sym)
        if pd.isna(op) or prev is None:
            return False
        if (float(op) / prev - 1) * 100 <= -(threshold_of(sym) - LIMIT_TOLERANCE):
            return False
        cash += holdings[sym] * float(op) * (1 - costs.sell)
        sells.append({"symbol": sym, "name": names.get(sym, sym), "price": round(float(op), 3)})
        del holdings[sym]
        return True

    def mark_to_market(d: str) -> float:
        total = cash
        for sym, shares in holdings.items():
            px = close_wide.at[d, sym] if sym in close_wide.columns else np.nan
            if pd.isna(px):
                px = last_price.get(sym, 0.0)
            else:
                last_price[sym] = float(px)
            total += shares * float(px)
        return total

    for i, d in enumerate(dates):
        # ---- 1) 开盘：执行昨日决策的换仓 + 重试顺延卖出 ----
        if pending is not None or defer_sell:
            buys: list[dict] = []
            sells: list[dict] = []
            target = set(pending["target"]) if pending else set()

            # 卖：不在新目标里的持仓 + 之前没卖掉的顺延单
            for sym in list(holdings.keys()):
                if sym in target:
                    defer_sell.discard(sym)  # 新决策又选回它，取消顺延卖出
                    continue
                if pending is None and sym not in defer_sell:
                    continue  # 今天没有新决策，也不是顺延单 → 继续持有
                if try_sell(sym, d, sells):
                    defer_sell.discard(sym)
                else:
                    defer_sell.add(sym)  # 跌停/停牌：明天开盘再试

            # 买：新目标里还没持有的，等权分配当前现金
            if pending is not None:
                to_buy = [s for s in pending["target"] if s not in holdings]
                if to_buy:
                    budget = cash / len(to_buy)
                    for sym in to_buy:
                        op = open_wide.at[d, sym] if sym in open_wide.columns else np.nan
                        if pd.isna(op) or float(op) <= 0:
                            continue  # 停牌买不进
                        signal_close = pending["signal_close"].get(sym)
                        if signal_close:
                            open_pct = (float(op) / signal_close - 1) * 100
                            if open_pct >= threshold_of(sym) - LIMIT_TOLERANCE:
                                continue  # 开盘≈涨停价，一字板追不进
                        unit_cost = float(op) * (1 + costs.buy)
                        shares = int(budget / unit_cost / 100) * 100  # A股整百股
                        if shares <= 0:
                            continue  # 钱不够一手
                        cash -= shares * unit_cost
                        holdings[sym] = holdings.get(sym, 0) + shares
                        last_price[sym] = float(op)
                        buys.append(
                            {
                                "symbol": sym,
                                "name": names.get(sym, sym),
                                "price": round(float(op), 3),
                                "shares": shares,
                            }
                        )

            if buys or sells:
                trades_log.append(
                    {
                        "signal_date": pending["signal_date"] if pending else "",
                        "exec_date": d,
                        "buys": buys,
                        "sells": sells,
                        "holdings_count": len(holdings),
                    }
                )
            pending = None

        # ---- 2) 收盘：调仓日做选股决策（次日开盘执行）----
        if d in rebalance_set and i + 1 < len(dates):
            table_d = by_date.get(d)
            if table_d is not None and len(table_d):
                mask, hit_count = pack.evaluate(table_d)
                cand = table_d[mask].copy()
                cand["hit_count"] = hit_count[mask]
                cand = cand.sort_values(["hit_count", "amount_yi"], ascending=False)
                picked = list(cand.index[: params.top_n])
                pending = {
                    "signal_date": d,
                    "target": picked,
                    "signal_close": {s: float(cand.at[s, "close"]) for s in picked},
                }
            else:
                # 当日无任何候选 → 清仓决策（空目标）
                pending = {"signal_date": d, "target": [], "signal_close": {}}
            period_marks.append(mark_to_market(d))

        # ---- 3) 收盘：净值结算 ----
        curve.append({"date": d, "value": round(mark_to_market(d), 2)})

    # ---- 绩效指标 ----
    if period_marks:
        period_marks.append(curve[-1]["value"])  # 收尾：最后一个周期到区间末
    values = pd.Series([c["value"] for c in curve], index=[c["date"] for c in curve])
    total_return = values.iloc[-1] / params.init_cash - 1
    n_days = len(values)
    annual = (1 + total_return) ** (244 / n_days) - 1 if n_days > 0 else 0.0
    cummax = values.cummax()
    max_drawdown = float((1 - values / cummax).max())
    daily_ret = values.pct_change().dropna()
    sharpe = (
        float(daily_ret.mean() / daily_ret.std() * np.sqrt(244))
        if len(daily_ret) > 1 and daily_ret.std() > 0
        else 0.0
    )
    # 周期胜率：相邻调仓周期净值环比为正的比例
    wins = sum(1 for a, b in zip(period_marks, period_marks[1:], strict=False) if b > a)
    period_win_rate = (
        round(wins / (len(period_marks) - 1) * 100, 1) if len(period_marks) > 1 else None
    )

    # 基准曲线（同窗归一化到初始资金）
    bench_curve: list[dict] = []
    if len(benchmark):
        b = benchmark[(benchmark["date"] >= params.start) & (benchmark["date"] <= params.end)]
        if len(b):
            base = float(b["close"].iloc[0])
            bench_curve = [
                {
                    "date": str(r["date"]),
                    "value": round(params.init_cash * float(r["close"]) / base, 2),
                }
                for _, r in b.iterrows()
            ]
    bench_total = (bench_curve[-1]["value"] / params.init_cash - 1) if bench_curve else None

    return {
        "start": params.start,
        "end": params.end,
        "freq_days": params.freq_days,
        "top_n": params.top_n,
        "init_cash": params.init_cash,
        "final_value": round(float(values.iloc[-1]), 2),
        "metrics": {
            "total_return_pct": round(total_return * 100, 2),
            "annual_return_pct": round(annual * 100, 2),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "sharpe": round(sharpe, 2),
            "period_win_rate": period_win_rate,
            "benchmark_return_pct": (
                round(bench_total * 100, 2) if bench_total is not None else None
            ),
            "rebalance_count": len(trades_log),
        },
        "curve": curve,
        "benchmark_curve": bench_curve,
        "trades": trades_log,
    }
