"""AKShare 杂项适配器：只保留实测仍然快速稳定的接口。

AKShare 的东财类接口因翻页变慢已被直连适配器取代（见 eastmoney.py 头注），
这里保留的接口：
- 交易日历（新浪源，单请求返回全部历史交易日，含未来已排期日期）

AKShare 是同步库（内部用 requests），而我们的服务层是 asyncio 异步模型，
所以统一用 asyncio.to_thread 把同步调用挪到线程池执行，避免阻塞事件循环。
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


def _fetch_trade_dates_sync() -> list[str]:
    """同步拉取全部 A 股交易日（yyyy-MM-dd 字符串列表，升序）。"""
    import akshare as ak  # 函数内导入：akshare 首次导入约 2~3 秒，避免拖慢应用启动

    df = ak.tool_trade_date_hist_sina()
    return [d.strftime("%Y-%m-%d") for d in df["trade_date"]]


async def fetch_trade_dates() -> list[str]:
    """异步包装：在线程池里执行同步的 AKShare 调用。"""
    return await asyncio.to_thread(_fetch_trade_dates_sync)
