"""5 分钟K线同步服务：每个交易日盘后拉当天全市场 48 根/股，逐日积累。

数据源：腾讯 mkline 接口（东财 push2his 在大量同步后会被灰名单，腾讯无此问题）。
节奏：5800+ 只 × 每只 1 次请求，8 并发 + 80ms 节流 ≈ 2~4 分钟跑完。
落库：分批攒满 BATCH_FLUSH 行就写一次 DuckDB（流式入库，常驻内存几十 MB）。

用途：盘中形态因子（尾盘拉升/跳水、早盘强势、分时重心等）。
回测解锁：这类因子无完整历史，从接入日积累满 60 个交易日后，
回测服务才允许对盘中策略做历史推演（诚实原则，与 M5 扩展数据一致）。
"""

import asyncio
import logging
from datetime import datetime

import pandas as pd

from app.adapters.base import MinuteBar
from app.adapters.tencent_quote import TencentQuoteAdapter
from app.services.calendar_service import CalendarService
from app.services.market_store import MarketStore

logger = logging.getLogger(__name__)

CONCURRENCY = 8  # 并发请求数（腾讯接口宽容，8 并发实测安全）
DELAY_MS = 80  # 单请求间隔（全局节流）
BATCH_FLUSH = 20_000  # 攒多少行分钟线就批量写一次库


class MinuteSyncService:
    """分钟线同步：手动触发 + 每日定时（日线增量之后、策略跑批之前）。"""

    def __init__(self, store: MarketStore, calendar: CalendarService) -> None:
        self._store = store
        self._calendar = calendar
        self._running = False
        self.last_run: dict | None = None  # 上次执行结果（状态展示用）
        self.progress: dict | None = None  # 进行中的进度（done/total）

    @property
    def running(self) -> bool:
        return self._running

    async def run(self, trade_date: str | None = None) -> dict:
        """同步一个交易日的全市场 5 分钟线（默认最近交易日）。

        分项容错：单票失败只计数不中断；最后汇报成功/失败数。
        幂等：入库前先删当日旧数据，重跑安全。
        """
        if self._running:
            return {"skipped": True, "reason": "已有分钟线同步在执行"}
        self._running = True
        started = datetime.now()
        adapter = TencentQuoteAdapter()
        try:
            day = trade_date or self._calendar.latest_trade_date()
            symbols = await asyncio.to_thread(self._store.get_symbols)
            total = len(symbols)
            self.progress = {"trade_date": day, "done": 0, "total": total}
            logger.info("分钟线同步开始：%s，共 %d 只", day, total)

            # 幂等：先清当日旧数据，之后分批追加
            await asyncio.to_thread(self._store.delete_minute_bars_of, day)

            sem = asyncio.Semaphore(CONCURRENCY)
            buffer: list[MinuteBar] = []
            ok = 0
            fail = 0
            flush_lock = asyncio.Lock()

            async def flush() -> None:
                """把缓冲区的分钟线写入 DuckDB（攒批写，减少小事务）。"""
                nonlocal buffer
                if not buffer:
                    return
                batch, buffer = buffer, []
                df = pd.DataFrame(
                    {
                        "symbol": [b.symbol for b in batch],
                        "dt": pd.to_datetime([b.dt for b in batch]),
                        "trade_date": pd.to_datetime([b.trade_date for b in batch]),
                        "open": [b.open for b in batch],
                        "high": [b.high for b in batch],
                        "low": [b.low for b in batch],
                        "close": [b.close for b in batch],
                        "volume": [b.volume for b in batch],
                        "amount": [b.amount for b in batch],
                    }
                )
                await asyncio.to_thread(self._store.append_minute_bars, df)

            async def fetch_one(symbol: str) -> None:
                nonlocal ok, fail
                async with sem:
                    await asyncio.sleep(DELAY_MS / 1000)
                    try:
                        bars = await adapter.fetch_minute_bars(symbol, day)
                    except Exception as e:  # noqa: BLE001 - 单票容错边界
                        fail += 1
                        if fail <= 5:  # 只记前几条，避免日志刷屏
                            logger.warning("分钟线 %s 失败：%s(%s)", symbol, type(e).__name__, e)
                        return
                    if bars:
                        ok += 1
                        async with flush_lock:
                            buffer.extend(bars)
                            if len(buffer) >= BATCH_FLUSH:
                                await flush()
                    self.progress = {"trade_date": day, "done": ok + fail, "total": total}

            await asyncio.gather(*[fetch_one(s) for s in symbols])
            async with flush_lock:
                await flush()

            coverage = await asyncio.to_thread(self._store.minute_coverage_days)
            result = {
                "trade_date": day,
                "ok": ok,
                "fail": fail,
                "coverage_days": coverage,
                "cost_seconds": round((datetime.now() - started).total_seconds(), 1),
            }
            self.last_run = result
            logger.info("分钟线同步完成：%s", result)
            return result
        finally:
            await adapter.close()
            self._running = False
            self.progress = None
