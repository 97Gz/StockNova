"""QuoteService 轮询链路单测：订阅 → 轮询 → EventBus 推送。

盘中真实行情只能在交易时段人工验证，这里用 mock 把整条管道
（set_watch_symbols → _poll_loop → publish quotes 事件）固化成回归测试：
任何人改坏轮询逻辑，测试立刻发现，不用等开盘。
"""

import asyncio
from unittest.mock import patch

from app.adapters.base import Quote
from app.core.events import event_bus
from app.services.quote_service import QuoteService


def make_quote(symbol: str) -> Quote:
    """构造一条最小可用的伪报价。"""
    return Quote(
        symbol=symbol,
        name="测试股",
        price=10.0,
        prev_close=9.5,
        open=9.6,
        high=10.2,
        low=9.4,
        volume=1000.0,
        amount=1_000_000.0,
        pct_change=5.26,
        change=0.5,
        turnover=1.2,
        pe_ttm=15.0,
        pb=2.0,
        total_mv=1e10,
        circ_mv=8e9,
        ts="2026-06-12 10:00:00",
    )


class FakeCalendar:
    """始终盘中：让轮询循环走主路径。"""

    def is_trading_now(self) -> bool:
        return True


async def test_watchlist_poll_publishes_quotes() -> None:
    service = QuoteService(FakeCalendar())  # type: ignore[arg-type]
    service.set_watch_symbols(["600519", "300750"])

    async def fake_fetch(source: str, symbols: list[str]) -> list[Quote]:
        return [make_quote(s) for s in symbols]

    def fake_settings(self: QuoteService) -> dict:
        return {"source": "tencent", "watch_interval": 0.1}  # 加速测试

    queue = event_bus.subscribe()
    try:
        with (
            patch.object(QuoteService, "_fetch", side_effect=fake_fetch),
            patch.object(QuoteService, "_read_settings", fake_settings),
        ):
            service.start()
            # 等第一批推送（轮询周期 0.1s + sleep(1) 节拍，给足 3 秒余量）
            event = await asyncio.wait_for(queue.get(), timeout=3)
    finally:
        event_bus.unsubscribe(queue)
        await service.stop()

    assert event["type"] == "quotes"
    symbols = {q["symbol"] for q in event["data"]}
    assert symbols == {"600519", "300750"}
    # 最新报价缓存同步更新（REST 即时查询路径依赖它）
    assert service.latest["600519"].price == 10.0


async def test_poll_falls_back_to_backup_source() -> None:
    """主源失败时自动切换备用源，推送不中断。"""
    service = QuoteService(FakeCalendar())  # type: ignore[arg-type]
    service.set_watch_symbols(["600519"])
    calls: list[str] = []

    async def flaky_fetch(source: str, symbols: list[str]) -> list[Quote]:
        calls.append(source)
        if source == "tencent":
            raise ConnectionError("主源挂了")
        return [make_quote(s) for s in symbols]

    def fake_settings(self: QuoteService) -> dict:
        return {"source": "tencent", "watch_interval": 0.1}

    queue = event_bus.subscribe()
    try:
        with (
            patch.object(QuoteService, "_fetch", side_effect=flaky_fetch),
            patch.object(QuoteService, "_read_settings", fake_settings),
        ):
            service.start()
            event = await asyncio.wait_for(queue.get(), timeout=3)
    finally:
        event_bus.unsubscribe(queue)
        await service.stop()

    assert calls[:2] == ["tencent", "sina"]  # 先主后备
    assert event["data"][0]["symbol"] == "600519"
