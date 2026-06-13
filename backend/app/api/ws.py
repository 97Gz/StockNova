"""WebSocket 通道：/ws

单连接多用途——服务端把 EventBus 上的事件全部推给客户端：
- {"type": "sync_progress", ...}  同步任务进度（数据管理页进度条）
- {"type": "quotes", "data": [...]}  自选股实时报价（M2 自选页接入）

客户端可发送控制消息：
- {"action": "subscribe_quotes", "symbols": ["600519", ...]}  更新报价订阅清单
"""

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.events import event_bus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    queue = event_bus.subscribe()

    async def forward_events() -> None:
        """事件总线 → 客户端。"""
        while True:
            event = await queue.get()
            await ws.send_json(event)

    async def receive_commands() -> None:
        """客户端 → 控制命令（订阅报价等）。"""
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("action") == "subscribe_quotes":
                symbols = [str(s) for s in msg.get("symbols", [])]
                ws.app.state.quote_service.set_watch_symbols(symbols)

    # 两个方向任一结束（断开/异常）就整体收尾
    forward = asyncio.create_task(forward_events())
    receive = asyncio.create_task(receive_commands())
    try:
        await asyncio.wait({forward, receive}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in (forward, receive):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        event_bus.unsubscribe(queue)
