"""策略扫描服务：因子表缓存 → 条件树求值 → 多策略共振 → 跑批存档。

性能设计（验收：3 策略共振 < 5s）：
- 因子表按"最新交易日"缓存：首次全市场计算约 2~4s（170 万行面板向量化），
  之后所有扫描直接命中缓存（<100ms）；每日增量同步落库后日期变化自动失效。
- 求值本身是布尔向量运算，5800 行宽表上微秒级。
"""

import json
import logging
import threading
from datetime import date as date_cls
from datetime import datetime
from typing import Any

import pandas as pd

from app.core.database import create_session
from app.models.orm import NewsSentiment, StrategySignal
from app.services.market_store import MarketStore
from app.strategy import engine
from app.strategy.builtin import STRATEGIES, available_strategies, get_strategy
from app.strategy.factors import attach_ext_factors, compute_factor_table
from app.strategy.intraday import attach_intraday_factors, compute_intraday_factors

logger = logging.getLogger(__name__)


class StrategyService:
    def __init__(self, store: MarketStore) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._cache_date: str | None = None
        self._table: pd.DataFrame | None = None
        self._names: dict[str, str] = {}

    # ---------------- 因子表缓存 ----------------

    def factor_table(self, *, refresh: bool = False) -> pd.DataFrame:
        """取（或重建）最新交易日的因子宽表。线程安全、按日缓存。"""
        latest = self._store.stats()["bar_date_max"]
        with self._lock:
            if not refresh and self._table is not None and self._cache_date == latest:
                return self._table
            t0 = datetime.now()
            panel = self._store.query_market_panel(days=300)
            basics = self._store.basics_df()
            funds = self._store.fundamentals_df()
            table = compute_factor_table(panel, basics, funds)
            attach_ext_factors(
                table,
                fund_flow=self._store.fund_flow_df(),
                dragon_tiger=self._store.dragon_tiger_agg_df(days=3),
                earnings=self._store.earnings_df(),
                popularity=self._store.popularity_df(),
                senti_scores=_today_senti_scores(),
            )
            # 盘中因子：最新交易日有分钟线就计算，没有则置空（策略不命中）
            minute_df = self._store.minute_day_df(latest) if latest else pd.DataFrame()
            intraday = compute_intraday_factors(minute_df) if len(minute_df) else None
            attach_intraday_factors(table, intraday)
            self._names = dict(zip(basics["symbol"], basics["name"], strict=True))
            self._table = table
            self._cache_date = latest
            cost = (datetime.now() - t0).total_seconds()
            logger.info("因子表已重建: %s 只股票, 耗时 %.1fs (数据日 %s)", len(table), cost, latest)
            return table

    def warmup(self) -> None:
        """启动预热（后台线程调用），让用户第一次扫描就秒回。"""
        try:
            self.factor_table()
        except Exception:  # noqa: BLE001 - 预热失败不影响启动，首次扫描会重试
            logger.warning("因子表预热失败（可能行情库为空）", exc_info=True)

    def name_of(self, symbol: str) -> str:
        return self._names.get(symbol, symbol)

    # ---------------- 扫描与共振 ----------------

    def run(
        self,
        strategy_ids: list[str],
        *,
        custom_condition: dict | None = None,
        require_all: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        """执行一批策略并合并结果。

        require_all=True 表示"全部命中才入选"（AND 共振），
        默认 False 表示"命中任意一个即入选"，按命中数量降序（共振优先）。
        """
        table = self.factor_table()
        date = self._cache_date or ""

        # 每个策略求值 → {strategy_id: (mask, leaves)}
        evaluated: dict[str, dict] = {}
        for sid in strategy_ids:
            spec = get_strategy(sid)
            if spec is None or not spec.get("available", True):
                continue
            if spec.get("special"):
                mask, reason_map = self._run_special(spec, table)
                evaluated[sid] = {"spec": spec, "mask": mask, "reason_map": reason_map}
            else:
                mask, leaves = engine.evaluate(spec["condition"], table)
                evaluated[sid] = {"spec": spec, "mask": mask, "leaves": leaves}
        if custom_condition is not None:
            mask, leaves = engine.evaluate(custom_condition, table)
            evaluated["__custom__"] = {
                "spec": {"id": "__custom__", "name": "自定义条件"},
                "mask": mask,
                "leaves": leaves,
            }

        if not evaluated:
            return {"trade_date": date, "total": 0, "items": []}

        # 命中计数与合并
        hit_count = None
        for item in evaluated.values():
            m = item["mask"].astype(int)
            hit_count = m if hit_count is None else hit_count + m
        selected = hit_count >= (len(evaluated) if require_all else 1)
        symbols = hit_count[selected].sort_values(ascending=False).index.tolist()

        items = []
        for sym in symbols[:limit]:
            hits = []
            for item in evaluated.values():
                if not bool(item["mask"].get(sym, False)):
                    continue
                spec = item["spec"]
                if "reason_map" in item:
                    reasons = item["reason_map"].get(sym, [])
                else:
                    reasons = engine.describe_hit(sym, item["leaves"], table)
                hits.append({"strategy_id": spec["id"], "name": spec["name"], "reasons": reasons})
            score = round(100 * len(hits) / len(evaluated))
            items.append(
                {
                    "symbol": sym,
                    "name": self.name_of(sym),
                    "close": _safe(table.at[sym, "close"]),
                    "pct_change": _safe(table.at[sym, "pct_change"]),
                    "turnover": _safe(table.at[sym, "turnover"]),
                    "vol_ratio": _safe(table.at[sym, "vol_ratio"]),
                    "score": score,
                    "hit_count": len(hits),
                    "hits": hits,
                }
            )
        return {"trade_date": date, "total": int(selected.sum()), "items": items}

    # ---------------- 板块类特殊策略 ----------------

    def _run_special(
        self, spec: dict, table: pd.DataFrame
    ) -> tuple[pd.Series, dict[str, list[str]]]:
        kind = spec["special"]
        if kind == "board_leader":
            return self._board_leader(table)
        if kind == "board_rotation":
            return self._board_rotation(table)
        raise ValueError(f"未知特殊策略: {kind}")

    def _board_leader(self, table: pd.DataFrame) -> tuple[pd.Series, dict[str, list[str]]]:
        """热门板块龙头：今日涨幅前3行业板块 × 板块内今日涨幅前3成分股。"""
        perf = self._store.board_recent_perf("industry", days=2)
        mask = pd.Series(False, index=table.index)
        reasons: dict[str, list[str]] = {}
        if perf.empty:
            return mask, reasons
        latest_day = perf["trade_date"].max()
        today = perf[perf["trade_date"] == latest_day]
        top_boards = today.nlargest(3, "pct_change")
        members = self._store.board_members_map(top_boards["code"].tolist())
        for _, row in top_boards.iterrows():
            syms = [s for s in members.get(row["code"], []) if s in table.index]
            if not syms:
                continue
            ranked = table.loc[syms, "pct_change"].dropna().nlargest(3)
            for rank, (sym, pct) in enumerate(ranked.items(), start=1):
                if pct <= 0:
                    continue  # 板块强但个股不涨的不算龙头
                mask[sym] = True
                reasons.setdefault(sym, []).append(
                    f"✓ 所属「{row['name']}」板块今日领涨（{row['pct_change']:+.2f}%），"
                    f"板块内涨幅第 {rank}（{pct:+.2f}%）"
                )
        return mask, reasons

    def _board_rotation(self, table: pd.DataFrame) -> tuple[pd.Series, dict[str, list[str]]]:
        """板块轮动接力：5日涨幅排名跃升最快的3个板块 × 板块内今日放量上涨的前5成分股。"""
        perf = self._store.board_recent_perf("industry", days=12)
        mask = pd.Series(False, index=table.index)
        reasons: dict[str, list[str]] = {}
        if perf.empty:
            return mask, reasons
        # 每板块：近5日累计涨幅（现在） vs 5日前的近5日累计涨幅 → 排名变化
        snap = []
        for code, g in perf.groupby("code", sort=False):
            pct = g.sort_values("trade_date")["pct_change"].to_numpy()
            if len(pct) < 10:
                continue
            now5 = float(pct[-5:].sum())
            prev5 = float(pct[-10:-5].sum())
            snap.append({"code": code, "name": g["name"].iloc[0], "now": now5, "prev": prev5})
        if not snap:
            return mask, reasons
        df = pd.DataFrame(snap)
        df["rank_now"] = df["now"].rank(ascending=False, method="min")
        df["rank_prev"] = df["prev"].rank(ascending=False, method="min")
        df["jump"] = df["rank_prev"] - df["rank_now"]
        top = df[df["jump"] > 0].nlargest(3, "jump")
        members = self._store.board_members_map(top["code"].tolist())
        for _, row in top.iterrows():
            syms = [s for s in members.get(row["code"], []) if s in table.index]
            if not syms:
                continue
            sub = table.loc[syms]
            cand = sub[(sub["pct_change"] > 0) & (sub["vol_ratio"] > 1)]
            for sym, pct in cand["pct_change"].nlargest(5).items():
                mask[sym] = True
                vr = _safe(table.at[sym, "vol_ratio"]) or 0
                reasons.setdefault(sym, []).append(
                    f"✓ 「{row['name']}」板块5日涨幅排名从第 {int(row['rank_prev'])} 升至"
                    f"第 {int(row['rank_now'])}（资金切入），个股今日 {pct:+.2f}% 量比 {vr:.2f}"
                )
        return mask, reasons

    # ---------------- 每日跑批存档 ----------------

    def run_daily_batch(self) -> dict:
        """对全部可用策略跑批并落 strategy_signals（同日重跑先清旧档）。"""
        ids = [s["id"] for s in available_strategies()]
        table = self.factor_table(refresh=True)
        date = self._cache_date or ""
        total_signals = 0
        with create_session() as db:
            db.query(StrategySignal).filter(StrategySignal.trade_date == date).delete()
            for sid in ids:
                spec = get_strategy(sid)
                if spec.get("special"):
                    mask, reason_map = self._run_special(spec, table)
                else:
                    mask, leaves = engine.evaluate(spec["condition"], table)
                    reason_map = None
                for sym in mask[mask].index:
                    if reason_map is not None:
                        reasons = reason_map.get(sym, [])
                    else:
                        reasons = engine.describe_hit(sym, leaves, table)
                    db.add(
                        StrategySignal(
                            trade_date=date,
                            strategy_id=sid,
                            symbol=sym,
                            close=_safe(table.at[sym, "close"]) or 0.0,
                            reasons_json=json.dumps(reasons, ensure_ascii=False),
                        )
                    )
                    total_signals += 1
            db.commit()
        logger.info("策略跑批完成: %s, %d 个策略, %d 条信号", date, len(ids), total_signals)
        return {"trade_date": date, "strategies": len(ids), "signals": total_signals}

    def today_signals_summary(
        self, top: int = 8, strategy_ids: list[str] | None = None
    ) -> dict:
        """最新跑批日的共振排行。

        strategy_ids 非空时只统计选中的策略（用户自选组合视角）；
        同时返回 by_strategy（每个策略当日命中只数），供前端做筛选器。
        """
        with create_session() as db:
            latest = (
                db.query(StrategySignal.trade_date)
                .order_by(StrategySignal.trade_date.desc())
                .first()
            )
            if latest is None:
                return {"trade_date": None, "items": [], "by_strategy": []}
            date = latest[0]
            rows = (
                db.query(StrategySignal.symbol, StrategySignal.strategy_id, StrategySignal.close)
                .filter(StrategySignal.trade_date == date)
                .all()
            )
        name_by_id = {s["id"]: s["name"] for s in STRATEGIES}

        # 每个策略当日命中数（基于全部信号，不随筛选变化，给筛选器展示用）
        hit_by_strategy: dict[str, int] = {}
        for _, sid, _ in rows:
            hit_by_strategy[sid] = hit_by_strategy.get(sid, 0) + 1
        by_strategy = [
            {"id": sid, "name": name_by_id.get(sid, sid), "count": cnt}
            for sid, cnt in sorted(hit_by_strategy.items(), key=lambda kv: kv[1], reverse=True)
        ]

        # 自选策略组合：只保留选中策略的信号
        picked = set(strategy_ids) if strategy_ids else None
        by_symbol: dict[str, list[str]] = {}
        close_map: dict[str, float] = {}
        for symbol, sid, close in rows:
            if picked is not None and sid not in picked:
                continue
            by_symbol.setdefault(symbol, []).append(sid)
            close_map[symbol] = close
        ranked = sorted(by_symbol.items(), key=lambda kv: len(kv[1]), reverse=True)[:top]
        items = [
            {
                "symbol": sym,
                "name": self.name_of(sym),
                "close": close_map.get(sym),
                "hit_count": len(sids),
                "strategies": [name_by_id.get(s, s) for s in sids[:4]],
            }
            for sym, sids in ranked
        ]
        return {"trade_date": date, "items": items, "by_strategy": by_strategy}


def _safe(v: Any) -> float | None:
    """numpy 标量/NaN → JSON 可序列化的 float/None。"""
    if v is None or pd.isna(v):
        return None
    return round(float(v), 4)


def _today_senti_scores() -> dict[str, int]:
    """今日已分析股票的 AI 情绪分（senti_score 因子的数据源）。

    直接查 SQLite（与 news_service.all_today_scores 同口径），
    避免 strategy_service 反向依赖消息服务。
    """
    today = date_cls.today().isoformat()
    with create_session() as db:
        rows = db.query(NewsSentiment.symbol, NewsSentiment.score).filter(
            NewsSentiment.trade_date == today
        )
        return {sym: score for sym, score in rows}
