"""扩展数据同步服务（M5）：资金流 / 龙虎榜 / 业绩预告 / 人气榜。

与核心行情同步（sync_service）的差异：
- 核心同步是"历史初始化 + 每日增量"的重任务（断点续传/状态机）；
- 扩展同步是轻任务：每类数据一两次请求即拿全，整轮 1~2 分钟，
  失败也不影响行情主流程 —— 所以做成分项容错的一把梭（partial OK）。

数据归属日：资金流/人气榜是"当下快照"，落库到最近一个交易日；
龙虎榜/业绩预告自带日期，按区间覆盖写入。
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from app.adapters.eastmoney_ext import EastMoneyExtAdapter
from app.services.calendar_service import CalendarService
from app.services.market_store import MarketStore

logger = logging.getLogger(__name__)


def current_report_dates(today: date | None = None) -> list[str]:
    """当前应同步的业绩预告报告期（最近的未来报告期 + 上一个报告期）。

    报告期固定为 3-31 / 6-30 / 9-30 / 12-31。刚跨过报告期边界时新期
    数据可能只有零星几条，同时保留上一期保证策略有数据可用。
    """
    today = today or date.today()
    quarters = [(3, 31), (6, 30), (9, 30), (12, 31)]
    # 本年 4 个报告期 + 去年年报，按时间排好后找"今天之后的第一个"
    candidates = [date(today.year - 1, 12, 31)] + [date(today.year, m, d) for m, d in quarters]
    upcoming = next(d for d in candidates if d >= today) if candidates[-1] >= today else None
    if upcoming is None:  # today 在 12-31 之后（理论不可达，防御）
        upcoming = date(today.year + 1, 3, 31)
    prev = max(d for d in candidates if d < upcoming)
    return [prev.isoformat(), upcoming.isoformat()]


class ExtSyncService:
    """扩展数据同步：手动触发 + 每日定时（增量同步完成后错峰执行）。"""

    def __init__(self, store: MarketStore, calendar: CalendarService) -> None:
        self._store = store
        self._calendar = calendar
        self._running = False
        self.last_run: dict | None = None  # 上次执行的分项结果（状态展示用）

    @property
    def running(self) -> bool:
        return self._running

    async def run(self) -> dict:
        """同步全部 4 类扩展数据，分项容错（单项失败不中断其余）。"""
        if self._running:
            return {"skipped": True, "reason": "已有扩展同步在执行"}
        self._running = True
        adapter = EastMoneyExtAdapter()
        started = datetime.now()
        result: dict = {"started_at": started.isoformat(sep=" ", timespec="seconds")}
        try:
            trade_date = self._calendar.latest_trade_date()
            result["trade_date"] = trade_date
            result["fund_flow"] = await self._sync_fund_flow(adapter, trade_date)
            result["dragon_tiger"] = await self._sync_dragon_tiger(adapter)
            result["earnings"] = await self._sync_earnings(adapter)
            result["popularity"] = await self._sync_popularity(adapter, trade_date)
        finally:
            await adapter.close()
            self._running = False
        result["cost_seconds"] = round((datetime.now() - started).total_seconds(), 1)
        self.last_run = result
        logger.info("扩展数据同步完成：%s", result)
        return result

    async def _sync_fund_flow(self, adapter: EastMoneyExtAdapter, trade_date: str) -> dict:
        try:
            rows = await adapter.fetch_fund_flow_rank()
            await asyncio.to_thread(self._store.replace_fund_flow, trade_date, rows)
            return {"ok": True, "rows": len(rows)}
        except Exception as e:  # noqa: BLE001 - 分项容错边界
            logger.warning("资金流同步失败：%s(%s)", type(e).__name__, e)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def _sync_dragon_tiger(self, adapter: EastMoneyExtAdapter) -> dict:
        """近 7 个自然日的龙虎榜（覆盖最近几个交易日，含节假日空窗）。"""
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=7)).isoformat()
        try:
            rows = await adapter.fetch_dragon_tiger(start, end)
            await asyncio.to_thread(self._store.replace_dragon_tiger, start, end, rows)
            return {"ok": True, "rows": len(rows), "range": f"{start}~{end}"}
        except Exception as e:  # noqa: BLE001
            logger.warning("龙虎榜同步失败：%s(%s)", type(e).__name__, e)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def _sync_earnings(self, adapter: EastMoneyExtAdapter) -> dict:
        out: dict = {"ok": True, "periods": {}}
        for rd in current_report_dates():
            try:
                rows = await adapter.fetch_earnings_forecast(rd)
                await asyncio.to_thread(self._store.replace_earnings_forecast, rd, rows)
                out["periods"][rd] = len(rows)
            except Exception as e:  # noqa: BLE001
                logger.warning("业绩预告 %s 同步失败：%s(%s)", rd, type(e).__name__, e)
                out["ok"] = False
                out["periods"][rd] = f"失败: {type(e).__name__}"
        return out

    async def _sync_popularity(self, adapter: EastMoneyExtAdapter, trade_date: str) -> dict:
        try:
            rows = await adapter.fetch_popularity_rank(top=100)
            await asyncio.to_thread(self._store.replace_popularity, trade_date, rows)
            return {"ok": True, "rows": len(rows)}
        except Exception as e:  # noqa: BLE001
            logger.warning("人气榜同步失败：%s(%s)", type(e).__name__, e)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
