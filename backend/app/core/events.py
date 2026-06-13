"""应用内事件总线：服务层发事件 → WebSocket 层转发给前端。

为什么需要它：
同步任务的进度、实时报价、异动通知都要推给前端，但服务层不应该
直接认识 WebSocket 连接（耦合）。服务层只管 publish，谁订阅谁收。

实现：每个订阅者一个 asyncio.Queue。队列满了直接丢弃最旧消息——
行情/进度类消息只关心最新值，丢旧不丢新。
"""

import asyncio
import contextlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """进程内发布-订阅。事件是 dict，约定带 "type" 字段（如 quotes / sync_progress）。"""

    def __init__(self, *, queue_size: int = 100) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._queue_size = queue_size

    def subscribe(self) -> asyncio.Queue:
        """订阅事件流，返回专属队列（WebSocket 连接建立时调用）。"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """退订（WebSocket 断开时调用）。"""
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        """发布事件给所有订阅者。队列满时挤掉最旧的一条再放入。"""
        for queue in self._subscribers:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)


# 全局单例：服务层 import 它来发事件，WS 路由 import 它来订阅
event_bus = EventBus()
