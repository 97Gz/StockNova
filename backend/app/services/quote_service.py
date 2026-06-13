"""实时报价服务：盘中轮询 + WebSocket 推送。

两条轮询通道（间隔均可在设置中心配置）：
- 自选股通道：默认 5 秒，只拉自选清单里的代码（M1 阶段自选股功能未上线，
  先支持"订阅任意代码列表"，M2 自选股页面接上即可）。
- 全市场通道：默认 60 秒，拉全市场快照缓存在内存，供涨跌分布/热力图等
  聚合接口使用（M2 接入），同时也是盘后增量同步前的预览数据。

非交易时段自动休眠（检查间隔放大到 60 秒），不浪费请求。
"""

import asyncio
import contextlib
import logging
from dataclasses import asdict
from typing import Any

from app.adapters.base import Quote
from app.adapters.sina_quote import SinaQuoteAdapter
from app.adapters.tencent_quote import TencentQuoteAdapter
from app.core.database import create_session
from app.core.events import event_bus
from app.services import settings_service
from app.services.calendar_service import CalendarService

logger = logging.getLogger(__name__)


class QuoteService:
    """报价轮询器：随应用启动/停止，自选订阅集合由 WebSocket/REST 动态维护。"""

    def __init__(self, calendar: CalendarService) -> None:
        self._calendar = calendar
        self._tencent = TencentQuoteAdapter()
        self._sina = SinaQuoteAdapter()
        self._watch_symbols: set[str] = set()
        self._task: asyncio.Task | None = None
        # 最新报价缓存：symbol → Quote（REST 查询与盘后预览用）
        self.latest: dict[str, Quote] = {}

    # ---------------- 生命周期 ----------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._tencent.close()
        await self._sina.close()

    # ---------------- 订阅管理 ----------------

    def set_watch_symbols(self, symbols: list[str]) -> None:
        """更新自选轮询清单（WebSocket 客户端订阅时调用）。"""
        self._watch_symbols = {s.strip() for s in symbols if s.strip()}

    # ---------------- 轮询主循环 ----------------

    async def _poll_loop(self) -> None:
        """单循环驱动两个通道：按各自间隔到点执行，非盘中休眠。"""
        last_watch = 0.0
        loop = asyncio.get_event_loop()
        while True:
            try:
                if not self._calendar.is_trading_now():
                    await asyncio.sleep(60)  # 收盘/休市：低频检查是否开盘
                    continue

                cfg = await asyncio.to_thread(self._read_settings)
                now = loop.time()
                if self._watch_symbols and now - last_watch >= cfg["watch_interval"]:
                    last_watch = now
                    await self._poll_watchlist(cfg["source"])
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 轮询循环永不退出
                logger.exception("报价轮询异常，10 秒后继续")
                await asyncio.sleep(10)

    async def _poll_watchlist(self, source: str) -> None:
        """拉一轮自选股报价并广播。主源失败自动切备用源（降级）。"""
        symbols = sorted(self._watch_symbols)
        try:
            quotes = await self._fetch(source, symbols)
        except Exception:  # noqa: BLE001
            backup = "sina" if source == "tencent" else "tencent"
            logger.warning("报价源 %s 失败，切换备用源 %s", source, backup)
            quotes = await self._fetch(backup, symbols)

        for q in quotes:
            self.latest[q.symbol] = q
        event_bus.publish({"type": "quotes", "data": [asdict(q) for q in quotes]})

    async def _fetch(self, source: str, symbols: list[str]) -> list[Quote]:
        adapter = self._tencent if source == "tencent" else self._sina
        return await adapter.fetch_quotes(symbols)

    def _read_settings(self) -> dict[str, Any]:
        with create_session() as db:
            return {
                "source": settings_service.get_value(db, "quotes.source"),
                "watch_interval": settings_service.get_value(db, "quotes.watchlist_interval_s"),
            }

    # ---------------- REST 即时查询 ----------------

    async def snapshot(self, symbols: list[str]) -> list[dict[str, Any]]:
        """按需拉一次报价（不依赖轮询循环），设置中心"测试报价源"用。"""
        with create_session() as db:
            source = settings_service.get_value(db, "quotes.source")
        quotes = await self._fetch(source, symbols)
        return [asdict(q) for q in quotes]
