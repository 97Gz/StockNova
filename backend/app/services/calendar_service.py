"""交易日历服务：开市判定的唯一权威。

数据来源：新浪交易日历（经 AKShare），含未来已排期的交易日，
首次获取后存入 DuckDB，之后从库加载并缓存在内存（set 查询 O(1)）。
"""

import logging
from datetime import datetime, time

from app.adapters.akshare_misc import fetch_trade_dates
from app.services.market_store import MarketStore

logger = logging.getLogger(__name__)

# A 股交易时段（午休 11:30~13:00 也算"盘中"以简化轮询逻辑，收盘价不受影响）
MARKET_OPEN = time(9, 15)  # 集合竞价开始
MARKET_CLOSE = time(15, 1)  # 收盘后留 1 分钟余量


class CalendarService:
    """交易日历：内存缓存 + DuckDB 持久化 + 按需刷新。"""

    def __init__(self, store: MarketStore) -> None:
        self._store = store
        self._dates: set[str] = set()
        self._max_date = ""

    async def ensure_loaded(self) -> None:
        """启动时调用：库里有就直接加载；没有或快过期就从数据源刷新。

        "快过期"判定：日历最后日期距今不足 30 天（新浪日历按年发布，
        每年 12 月会预发布次年全年，正常情况下提前量很大）。
        """
        import asyncio

        dates = await asyncio.to_thread(self._store.load_trade_dates)
        today = datetime.now().strftime("%Y-%m-%d")
        if dates and (datetime.strptime(dates[-1], "%Y-%m-%d") - datetime.now()).days > 30:
            self._dates = set(dates)
            self._max_date = dates[-1]
            logger.info("交易日历从库加载：%d 天，至 %s", len(dates), self._max_date)
            return

        try:
            fresh = await fetch_trade_dates()
            await asyncio.to_thread(self._store.replace_trade_calendar, fresh)
            self._dates = set(fresh)
            self._max_date = fresh[-1]
            logger.info("交易日历已刷新：%d 天，至 %s", len(fresh), self._max_date)
        except Exception:
            # 数据源失败但库里有旧日历 → 继续用旧的（降级不中断）
            if dates:
                self._dates = set(dates)
                self._max_date = dates[-1]
                logger.exception("交易日历刷新失败，沿用库内旧日历（至 %s）", self._max_date)
            else:
                logger.exception("交易日历获取失败且库内无缓存，今天(%s)将按非交易日处理", today)

    def is_trading_day(self, date_str: str | None = None) -> bool:
        """指定日期（默认今天）是否为交易日。"""
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        return date_str in self._dates

    def is_trading_now(self) -> bool:
        """当前时刻是否在盘中（交易日的 9:15 ~ 15:01）。"""
        now = datetime.now()
        return self.is_trading_day() and MARKET_OPEN <= now.time() <= MARKET_CLOSE

    def latest_trade_date(self, *, before: str | None = None) -> str:
        """不晚于指定日期（默认今天）的最近一个交易日。"""
        anchor = before or datetime.now().strftime("%Y-%m-%d")
        candidates = [d for d in self._dates if d <= anchor]
        return max(candidates) if candidates else ""
