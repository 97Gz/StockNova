"""回测服务：装配行情数据 → 调引擎 → 结果存档（SQLite backtest_runs）。

职责边界：engine.py 是纯计算；这里负责取数、列裁剪、宽表构建与持久化。
"""

import json
import logging
from datetime import datetime
from typing import Any

from app.backtest import engine
from app.core.database import create_session
from app.models.orm import BacktestRun
from app.services.market_store import MarketStore
from app.strategy.builtin import get_strategy
from app.strategy.factors import EXT_FACTORS, FACTOR_META, compute_factor_table

logger = logging.getLogger(__name__)

BENCHMARK_SYMBOL = "000300"  # 沪深300（index_daily 表用裸代码，无交易所前缀）

# 指标 warmup 所需的历史长度（MA250 + EMA 收敛余量）
WARMUP_TRADE_DAYS = 370


class BacktestError(ValueError):
    """参数或数据问题导致无法回测（API 层转成业务错误码）。"""


def _collect_factors(cond: dict) -> set[str]:
    """递归收集条件树引用的因子名（用于列裁剪，省内存）。"""
    out: set[str] = set()
    if "all" in cond or "any" in cond:
        for child in cond.get("all") or cond.get("any") or []:
            out |= _collect_factors(child)
        return out
    if "factor" in cond:
        out.add(cond["factor"])
        if isinstance(cond.get("ref"), str):
            out.add(cond["ref"])
    return out


def build_pack(
    strategy_ids: list[str], custom_condition: dict | None, require_all: bool
) -> engine.StrategyPack:
    """请求参数 → StrategyPack。

    两类策略被诚实拒绝（机械回填会产生撒谎的回测结果）：
    - special：板块轮动类，依赖盘面情绪；
    - no_backtest：扩展数据类（资金流/龙虎榜等），数据从接入日才开始积累。
    自定义条件若引用扩展因子（EXT_FACTORS），同理拒绝。
    """
    conditions: list[tuple[str, dict]] = []
    for sid in strategy_ids:
        spec = get_strategy(sid)
        if spec is None:
            raise BacktestError(f"未知策略: {sid}")
        if not spec.get("available", True):
            raise BacktestError(f"策略「{spec['name']}」暂未上线，无法回测")
        if spec.get("special"):
            raise BacktestError(f"「{spec['name']}」依赖盘面情绪与板块轮动，不适合机械历史回测")
        if spec.get("no_backtest"):
            raise BacktestError(f"「{spec['name']}」{spec['no_backtest']}")
        conditions.append((spec["name"], spec["condition"]))
    if custom_condition is not None:
        ext_used = _collect_factors(custom_condition) & EXT_FACTORS
        if ext_used:
            labels = "、".join(FACTOR_META[f]["label"] for f in sorted(ext_used))
            raise BacktestError(
                f"自定义条件引用了扩展数据因子（{labels}）——这类数据从接入日起"
                "逐日积累、无完整历史，暂不支持历史回测"
            )
        conditions.append(("自定义条件", custom_condition))
    if not conditions:
        raise BacktestError("至少选择一个策略或提供自定义条件")
    return engine.StrategyPack(conditions=conditions, require_all=require_all)


class BacktestService:
    def __init__(self, store: MarketStore) -> None:
        self._store = store

    # ---------------- 策略时光机 ----------------

    def snapshot(
        self,
        pack: engine.StrategyPack,
        signal_date: str,
        hold_days: list[int],
        params_for_archive: dict,
    ) -> dict[str, Any]:
        latest = self._store.stats()["bar_date_max"]
        if not latest:
            raise BacktestError("行情库为空，请先完成数据初始化")
        if signal_date >= latest:
            raise BacktestError(f"信号日需早于最新数据日 {latest}（要留出持有期）")

        max_hold = max(hold_days)
        # 取数窗口：向前 warmup（算指标）+ 向后 max_hold+5 个交易日（算持有收益）。
        # 不直接取到 latest——信号日很久远时那会把面板撑大好几倍。
        warmup_start = self._store.warmup_start_date(signal_date, WARMUP_TRADE_DAYS)
        forward = self._store.trade_dates_between(signal_date, latest)
        if not forward or forward[0] != signal_date:
            raise BacktestError(f"{signal_date} 不是交易日（或库内缺该日数据）")
        forward_end = forward[min(max_hold + 5, len(forward) - 1)]

        panel = self._store.query_market_panel_between(warmup_start, forward_end)
        if panel.empty:
            raise BacktestError("行情库为空，请先完成数据初始化")
        panel["trade_date"] = panel["trade_date"].astype(str).str[:10]

        basics = self._store.basics_df()
        funds = self._store.fundamentals_df()
        hist = panel[panel["trade_date"] <= signal_date]
        table = compute_factor_table(hist, basics, funds)
        # 信号日停牌的股票（最后一行是更早日期的旧数据）不参与——停牌买不到
        active = set(hist.loc[hist["trade_date"] == signal_date, "symbol"])
        table = table[table.index.isin(active)]

        names = dict(zip(basics["symbol"], basics["name"], strict=True))
        # 基准窗口与面板同宽（信号日 → 持有期末）
        benchmark = self._store.index_close_series(BENCHMARK_SYMBOL, signal_date, forward_end)

        result = engine.run_snapshot(
            panel,
            table,
            pack,
            engine.SnapshotParams(signal_date=signal_date, hold_days=hold_days),
            names,
            benchmark,
        )
        result["latest_data_date"] = latest
        note = (
            "信号日次日开盘买入（一字板跳过）、持有期满收盘卖出；"
            "已扣双边手续费；基本面因子按当前快照近似。"
        )
        # 距信号日不足 max_hold 个交易日的部分按最新收盘计浮盈
        if any(r["returns"][str(max_hold)]["holding"] for r in result["details"]):
            note += f" 部分持有期距信号日不足 {max_hold} 个交易日，按最新收盘价计算浮动盈亏。"
        result["note"] = note
        result["run_id"] = self._archive("snapshot", params_for_archive, result)
        return result

    # ---------------- 定期调仓 ----------------

    def rebalance(
        self,
        pack: engine.StrategyPack,
        params: engine.RebalanceParams,
        params_for_archive: dict,
    ) -> dict[str, Any]:
        latest = self._store.stats()["bar_date_max"]
        if not latest:
            raise BacktestError("行情库为空，请先完成数据初始化")
        end = min(params.end, latest)
        if params.start >= end:
            raise BacktestError("起始日期必须早于结束日期（且不晚于最新数据日）")
        # 区间上限保护：全市场全日期因子面板很吃内存，先限制两年（约 490 交易日）
        n_days = len(self._store.trade_dates_between(params.start, end))
        if n_days > 500:
            raise BacktestError("回测区间过长，请控制在两年以内（约 490 个交易日）")
        if n_days < 10:
            raise BacktestError("回测区间过短（不足 10 个交易日），请扩大日期范围")

        warmup_start = self._store.warmup_start_date(params.start, WARMUP_TRADE_DAYS)
        t0 = datetime.now()
        panel = self._store.query_market_panel_between(warmup_start, end)
        if panel.empty:
            raise BacktestError("行情库为空，请先完成数据初始化")
        panel["trade_date"] = panel["trade_date"].astype(str).str[:10]

        basics = self._store.basics_df()
        funds = self._store.fundamentals_df()
        factor_long = compute_factor_table(panel, basics, funds, all_dates=True)

        # 列裁剪：条件树引用的因子 + 交易/排序必备列
        needed = set()
        for _, cond in pack.conditions:
            needed |= _collect_factors(cond)
        unknown = needed - set(FACTOR_META)
        if unknown:
            raise BacktestError(f"条件引用了未知因子: {', '.join(sorted(unknown))}")
        keep = list(needed | {"close", "amount_yi", "is_st"}) + ["symbol", "trade_date"]
        factor_long = factor_long[keep]
        # 回测窗口外的 warmup 行丢掉（指标已算完，不再需要）
        factor_long = factor_long[factor_long["trade_date"] >= params.start]

        # date × symbol 宽表：open 用于成交价，close 用于每日估值（ffill 补停牌价）
        window = panel[panel["trade_date"] >= params.start]
        open_wide = window.pivot(index="trade_date", columns="symbol", values="open")
        close_wide = window.pivot(index="trade_date", columns="symbol", values="close").ffill()

        names = dict(zip(basics["symbol"], basics["name"], strict=True))
        st_map = dict(zip(basics["symbol"], basics["is_st"].astype(bool), strict=True))
        benchmark = self._store.index_close_series(BENCHMARK_SYMBOL, params.start, end)

        prep_cost = (datetime.now() - t0).total_seconds()
        result = engine.run_rebalance(
            factor_long,
            open_wide,
            close_wide,
            pack,
            engine.RebalanceParams(
                start=params.start,
                end=end,
                freq_days=params.freq_days,
                top_n=params.top_n,
                init_cash=params.init_cash,
            ),
            names,
            benchmark,
            st_map,
        )
        total_cost = (datetime.now() - t0).total_seconds()
        logger.info(
            "调仓回测完成: %s~%s freq=%d top=%d, 数据准备 %.1fs / 总耗时 %.1fs",
            params.start,
            end,
            params.freq_days,
            params.top_n,
            prep_cost,
            total_cost,
        )
        result["note"] = (
            "调仓日收盘选股、次日开盘换仓（开盘涨停不追、跌停不卖顺延、停牌冻结）；"
            "整百股成交并扣双边手续费；基本面因子按当前快照近似。"
        )
        run_id = self._archive("rebalance", params_for_archive, result)
        result["run_id"] = run_id
        return result

    # ---------------- 存档 ----------------

    def _archive(self, kind: str, params: dict, result: dict) -> int:
        """落一条回测记录（结果含曲线明细，JSON 文本存 SQLite）。"""
        with create_session() as db:
            row = BacktestRun(
                kind=kind,
                params_json=json.dumps(params, ensure_ascii=False),
                result_json=json.dumps(result, ensure_ascii=False),
            )
            db.add(row)
            db.commit()
            return row.id

    def list_runs(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        """历史回测列表（摘要，不含大字段）。"""
        with create_session() as db:
            q = db.query(BacktestRun).order_by(BacktestRun.id.desc())
            if kind:
                q = q.filter(BacktestRun.kind == kind)
            rows = q.limit(limit).all()
            out = []
            for r in rows:
                params = json.loads(r.params_json)
                result = json.loads(r.result_json)
                brief: dict[str, Any] = {"strategy_ids": params.get("strategy_ids", [])}
                if r.kind == "snapshot":
                    brief["signal_date"] = result.get("signal_date")
                    brief["evaluated"] = result.get("evaluated")
                else:
                    brief["range"] = f"{result.get('start')} ~ {result.get('end')}"
                    brief["total_return_pct"] = result.get("metrics", {}).get("total_return_pct")
                out.append(
                    {
                        "id": r.id,
                        "kind": r.kind,
                        "created_at": r.created_at.isoformat(sep=" ", timespec="seconds"),
                        **brief,
                    }
                )
        return out

    def get_run(self, run_id: int) -> dict | None:
        with create_session() as db:
            row = db.get(BacktestRun, run_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "kind": row.kind,
                "params": json.loads(row.params_json),
                "result": json.loads(row.result_json),
                "created_at": row.created_at.isoformat(sep=" ", timespec="seconds"),
            }

    def delete_run(self, run_id: int) -> bool:
        with create_session() as db:
            row = db.get(BacktestRun, run_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True
