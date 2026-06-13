"""适配器层公共契约：数据结构（DTO）+ 重试工具。

为什么不直接在服务层调用各家接口：
数据源随时可能改版/失效（本项目最大外部风险）。把"取数"收敛到适配器层，
服务层只认这里定义的数据结构，换数据源时业务代码零改动。
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# 这些状态码代表"被数据源限流/灰名单"，重试要用分钟级长退避而不是秒级
THROTTLE_STATUS = {302, 403, 429, 502, 503}


def is_throttle_error(e: Exception) -> bool:
    """判断异常是否属于"被数据源限流/封禁"。

    两种表现（2026-06-12 东财实测）：
    - 返回 302/502 等状态码（HTTPStatusError）
    - 直接断开连接不给响应（RemoteProtocolError）
    """
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in THROTTLE_STATUS:
        return True
    return isinstance(e, httpx.RemoteProtocolError)


# ---------------- 统一数据结构 ----------------


@dataclass
class StockBasic:
    """股票主档：一只股票的静态信息。"""

    symbol: str  # 6 位代码，如 600519
    name: str  # 名称，如 贵州茅台
    exchange: str  # SH / SZ / BJ
    market: str  # 主板 / 创业板 / 科创板 / 北交所
    pinyin: str = ""  # 名称拼音首字母，如 gzmt（入库时计算）


@dataclass
class DailyBar:
    """单日 K 线（不复权），adj_factor 为后复权因子。

    复权说明（重要，回测正确性的根基）：
    - 存"不复权价 + 后复权因子"，前复权/后复权价都能随时算出来：
      后复权价 = 不复权价 × adj_factor
      前复权价 = 后复权价 ÷ 最新一天的 adj_factor
    - 不存前复权价的原因：每次分红除权后历史前复权价会整体变化，
      存了就要全量重算，而后复权因子只增不改。
    """

    symbol: str
    trade_date: str  # yyyy-MM-dd
    open: float
    high: float
    low: float
    close: float
    volume: float  # 手
    amount: float  # 元
    pct_change: float  # 涨跌幅 %
    turnover: float  # 换手率 %
    adj_factor: float = 1.0


@dataclass
class MinuteBar:
    """盘中 5 分钟 K 线（不复权）。

    只用于"当日盘中走势"类因子（尾盘拉升/早盘强势等），
    看的是日内相对变化，不涉及跨除权日比较，所以无需复权因子。
    """

    symbol: str
    dt: str  # K线结束时间 yyyy-MM-dd HH:mm
    trade_date: str  # 所属交易日 yyyy-MM-dd
    open: float
    high: float
    low: float
    close: float
    volume: float  # 手
    amount: float  # 元


@dataclass
class Quote:
    """实时报价快照（盘中轮询用）。"""

    symbol: str
    name: str
    price: float  # 最新价
    prev_close: float  # 昨收（除权日为除权参考价，用于检测除权事件）
    open: float
    high: float
    low: float
    volume: float  # 手
    amount: float  # 元
    pct_change: float  # 涨跌幅 %
    change: float  # 涨跌额
    turnover: float  # 换手率 %
    pe_ttm: float  # 市盈率 TTM
    pb: float  # 市净率
    total_mv: float  # 总市值（元）
    circ_mv: float  # 流通市值（元）
    ts: str  # 行情时间戳 yyyy-MM-dd HH:mm:ss


@dataclass
class BoardInfo:
    """板块（行业/概念）基本信息。"""

    code: str  # 东财板块代码，如 BK0475
    name: str  # 板块名，如 银行
    type: str  # industry / concept


@dataclass
class NewsItem:
    """一条新闻/快讯（消息中心与 AI 情绪分析的输入）。"""

    code: str  # 新闻唯一编号（东财文章 code）
    title: str
    summary: str  # 摘要/正文片段
    publish_time: str  # yyyy-MM-dd HH:mm:ss
    media: str = ""  # 来源媒体
    url: str = ""  # 原文链接
    stocks: list[str] | None = None  # 关联股票代码（快讯流带 stockList）


# ---------------- 重试与节流工具 ----------------


async def with_retry(coro_factory, *, retries: int = 3, base_delay: float = 1.0, label: str = ""):
    """异步重试：失败后指数退避（1s → 2s → 4s）再试。

    coro_factory 是"每次调用产生一个新协程"的函数——
    协程对象只能 await 一次，所以这里传工厂而不是协程本身。
    """
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return await coro_factory()
        except Exception as e:  # noqa: BLE001 - 适配器边界处兜底所有网络/解析错误
            last_error = e
            # 限流类错误（302/502 等）：东财灰名单一般持续几分钟，
            # 秒级退避扛不过去，改用 10s 起步的长退避（10/20/40s）
            wait = (10.0 if is_throttle_error(e) else base_delay) * (2**attempt)
            # 注意带上异常类型名：TimeoutError/ReadTimeout 等异常 str() 为空，
            # 只打 %s 会出现"失败原因是空白"的不可排查日志
            logger.warning(
                "[%s] 第 %d 次失败：%s(%s)，%.0fs 后重试",
                label,
                attempt + 1,
                type(e).__name__,
                e,
                wait,
            )
            await asyncio.sleep(wait)
    raise last_error  # type: ignore[misc]


class Throttle:
    """请求节流器：保证相邻请求之间至少间隔 delay_ms 毫秒（防封禁）。

    并发场景下配合 asyncio.Lock 使用：多个协程共享同一个节流器时，
    依然能保证全局请求频率不超标。
    """

    def __init__(self, delay_ms: int) -> None:
        self.delay = delay_ms / 1000
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            gap = self.delay - (now - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = asyncio.get_event_loop().time()
