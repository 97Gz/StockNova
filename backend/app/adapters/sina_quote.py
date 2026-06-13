"""新浪实时报价适配器：腾讯源故障时的备用。

接口：https://hq.sinajs.cn/list=sh600519,sz000001
注意：新浪要求带 Referer 头（防盗链），单请求代码数控制在 100 以内较稳。

返回格式：`var hq_str_sh600519="贵州茅台,今开,昨收,最新价,最高,最低,...";`
字段以英文逗号分隔。新浪不提供 PE/PB/市值，相应字段置 0
（备用源只保底价格类数据，估值类以东财每日快照为准）。
"""

import logging

import httpx

from app.adapters.base import Quote, with_retry

logger = logging.getLogger(__name__)

QUOTE_URL = "https://hq.sinajs.cn/list="
BATCH_SIZE = 100
HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}


def to_sina_code(symbol: str) -> str:
    """6 位代码 → 新浪代码格式（sh600519 / sz000001 / bj832000）。"""
    if symbol.startswith("6"):
        return f"sh{symbol}"
    if symbol.startswith(("8", "4", "92")):
        return f"bj{symbol}"
    return f"sz{symbol}"


def _to_float(value: str) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _parse_line(line: str) -> Quote | None:
    """解析新浪行情单行。字段下标：

    [0]名称 [1]今开 [2]昨收 [3]最新价 [4]最高 [5]最低
    [8]成交量(股) [9]成交额(元) [30]日期 [31]时间
    """
    if "=" not in line or "hq_str_" not in line:
        return None
    head, _, payload = line.partition("=")
    code = head.split("hq_str_")[-1].strip()  # 形如 sh600519
    fields = payload.strip().strip('";').split(",")
    if len(fields) < 32:
        return None

    symbol = code[2:]  # 去掉 sh/sz/bj 前缀
    price = _to_float(fields[3])
    prev_close = _to_float(fields[2])
    change = price - prev_close if price and prev_close else 0.0
    pct = (change / prev_close * 100) if prev_close else 0.0
    return Quote(
        symbol=symbol,
        name=fields[0],
        open=_to_float(fields[1]),
        prev_close=prev_close,
        price=price,
        high=_to_float(fields[4]),
        low=_to_float(fields[5]),
        volume=_to_float(fields[8]) / 100,  # 股 → 手（与腾讯/东财口径统一）
        amount=_to_float(fields[9]),
        change=round(change, 3),
        pct_change=round(pct, 2),
        ts=f"{fields[30]} {fields[31]}",
        # 新浪不提供以下估值字段
        turnover=0.0,
        pe_ttm=0.0,
        pb=0.0,
        total_mv=0.0,
        circ_mv=0.0,
    )


class SinaQuoteAdapter:
    """新浪批量报价（备用源）。"""

    def __init__(self, *, timeout: float = 10.0) -> None:
        # trust_env=False：国内行情源直连，忽略系统代理
        self._client = httpx.AsyncClient(timeout=timeout, headers=HEADERS, trust_env=False)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_quotes(self, symbols: list[str]) -> list[Quote]:
        quotes: list[Quote] = []
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i : i + BATCH_SIZE]
            codes = ",".join(to_sina_code(s) for s in batch)

            async def do(url: str = QUOTE_URL + codes) -> str:
                resp = await self._client.get(url)
                resp.raise_for_status()
                return resp.text

            text = await with_retry(do, retries=2, label="sina_quote")
            for line in text.splitlines():
                quote = _parse_line(line)
                if quote is not None:
                    quotes.append(quote)
        return quotes
