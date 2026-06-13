"""腾讯行情适配器：实时报价（自选 5 秒轮询/全市场快照）+ 盘中 5 分钟K线。

报价接口：https://qt.gtimg.cn/q=sh600519,sz000001,...
实测单请求可带 900 个代码、约 2.6 秒返回（见 scripts/probe_sources.py），
全市场 5800+ 只分 7 批即可，远快于东财翻页。

返回格式：每行 `v_sh600519="1~贵州茅台~600519~价格~昨收~今开~...";`
字段以 ~ 分隔，按固定下标取值（下方 _parse_line 标注了各下标含义）。

分钟K线接口：https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=sh600519,m5,,50
单根格式 [yyyyMMddHHmm, 开, 收, 高, 低, 量(手), {}, 换手]——
选腾讯而非东财做分钟线主源：东财 push2his 域在大量同步后会被灰名单，
而腾讯行情接口经本项目实时报价长期验证、无此问题。
"""

import logging

import httpx

from app.adapters.base import MinuteBar, Quote, with_retry

logger = logging.getLogger(__name__)

QUOTE_URL = "https://qt.gtimg.cn/q="
MKLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
BATCH_SIZE = 800  # 单请求代码数上限（实测 900 可用，留余量）


def to_tencent_code(symbol: str) -> str:
    """6 位代码 → 腾讯代码格式（sh600519 / sz000001 / bj832000）。

    已带市场前缀的代码原样放行——指数必须显式传前缀
    （上证指数 sh000001 与平安银行 sz000001 的 6 位代码相同，无法推断）。
    """
    if symbol.startswith(("sh", "sz", "bj")):
        return symbol
    if symbol.startswith("6"):
        return f"sh{symbol}"
    if symbol.startswith(("8", "4", "92")):
        return f"bj{symbol}"
    return f"sz{symbol}"


def _to_float(value: str) -> float:
    """容错转 float：空串/横杠等脏值一律按 0 处理。"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _parse_line(line: str) -> Quote | None:
    """解析腾讯行情单行。字段下标（gtimg 协议，实测 88 个字段）：

    [1]名称 [2]代码 [3]最新价 [4]昨收 [5]今开 [6]成交量(手)
    [30]时间戳(yyyyMMddHHmmss) [31]涨跌额 [32]涨跌幅% [33]最高 [34]最低
    [37]成交额(万元) [38]换手率% [39]市盈率TTM [44]流通市值(亿) [45]总市值(亿) [46]市净率
    """
    if "=" not in line:
        return None
    _, _, payload = line.partition("=")
    fields = payload.strip().strip('";').split("~")
    if len(fields) < 47:
        return None

    ts_raw = fields[30]  # 形如 20260612150500
    ts = (
        f"{ts_raw[0:4]}-{ts_raw[4:6]}-{ts_raw[6:8]} {ts_raw[8:10]}:{ts_raw[10:12]}:{ts_raw[12:14]}"
        if len(ts_raw) >= 14
        else ts_raw
    )
    return Quote(
        symbol=fields[2],
        name=fields[1],
        price=_to_float(fields[3]),
        prev_close=_to_float(fields[4]),
        open=_to_float(fields[5]),
        volume=_to_float(fields[6]),
        ts=ts,
        change=_to_float(fields[31]),
        pct_change=_to_float(fields[32]),
        high=_to_float(fields[33]),
        low=_to_float(fields[34]),
        amount=_to_float(fields[37]) * 1e4,  # 万元 → 元
        turnover=_to_float(fields[38]),
        pe_ttm=_to_float(fields[39]),
        circ_mv=_to_float(fields[44]) * 1e8,  # 亿 → 元
        total_mv=_to_float(fields[45]) * 1e8,
        pb=_to_float(fields[46]),
    )


class TencentQuoteAdapter:
    """腾讯批量报价。无需鉴权，注意控制频率（自选 5s / 全市场 60s 足够安全）。"""

    def __init__(self, *, timeout: float = 10.0) -> None:
        # trust_env=False：国内行情源直连，忽略系统代理
        self._client = httpx.AsyncClient(timeout=timeout, trust_env=False)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_quotes(self, symbols: list[str]) -> list[Quote]:
        """批量拉报价：内部自动按 BATCH_SIZE 分批请求。"""
        quotes: list[Quote] = []
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i : i + BATCH_SIZE]
            codes = ",".join(to_tencent_code(s) for s in batch)

            async def do(url: str = QUOTE_URL + codes) -> str:
                resp = await self._client.get(url)
                resp.raise_for_status()
                return resp.text

            text = await with_retry(do, retries=2, label="tencent_quote")
            for line in text.split(";"):
                quote = _parse_line(line)
                if quote is not None:
                    quotes.append(quote)
        return quotes

    async def fetch_minute_bars(self, symbol: str, day: str) -> list[MinuteBar]:
        """拉一只股票指定交易日的 5 分钟K线（当天 48 根）。

        接口一次最多返回最近 N 根（跨日连续），请求 60 根再按 day 过滤，
        保证拿全当天 48 根（9:35~11:30 + 13:05~15:00）。
        day 格式 yyyy-MM-dd。

        成交额近似：接口不返回额，按 量(手)×100×(开+收)/2 估算——
        盘中因子只用相对量价关系，对额的精度不敏感。
        """
        code = to_tencent_code(symbol)
        params = {"param": f"{code},m5,,60"}

        async def do() -> dict:
            resp = await self._client.get(MKLINE_URL, params=params)
            resp.raise_for_status()
            return resp.json()

        data = await with_retry(do, retries=2, label=f"tencent_m5:{symbol}")
        rows = ((data.get("data") or {}).get(code) or {}).get("m5") or []
        day_compact = day.replace("-", "")
        bars: list[MinuteBar] = []
        for row in rows:
            # 单根: [时间yyyyMMddHHmm, 开, 收, 高, 低, 量(手), {}, 换手]
            ts = str(row[0])
            if not ts.startswith(day_compact):
                continue
            o, c = float(row[1]), float(row[2])
            vol = float(row[5])
            bars.append(
                MinuteBar(
                    symbol=symbol,
                    dt=f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}",
                    trade_date=f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}",
                    open=o,
                    close=c,
                    high=float(row[3]),
                    low=float(row[4]),
                    volume=vol,
                    amount=vol * 100 * (o + c) / 2,
                )
            )
        return bars
