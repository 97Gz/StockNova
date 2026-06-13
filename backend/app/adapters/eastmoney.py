"""东方财富直连适配器：历史日线 / 股票主档 / 板块 / 全市场快照。

为什么直连而不经 AKShare（2026-06 实测结论）：
- AKShare 新版把东财接口全部改为 100 条/页串行分页：全市场快照要 7 分钟、
  指数日线要 2 分钟，完全无法满足本项目的同步与轮询需求。
- 东财 K 线接口（push2his）单请求即可返回一只股票的全部历史，约 1~3 秒。
- 直连后并发数/重试/节流全部自己掌控。

接口都是东财网页端公开使用的，无需鉴权；带上浏览器 UA 与 Referer 即可。
"""

import asyncio
import logging
from datetime import datetime

import httpx

from app.adapters.base import (
    BoardInfo,
    DailyBar,
    MinuteBar,
    Quote,
    StockBasic,
    Throttle,
    is_throttle_error,
    with_retry,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"

# 备用域映射：主域被灰名单（302/502/断连）时自动降级。
# push2delay 是东财官方的延迟行情池（延迟 15 分钟），接口完全同构；
# 盘后同步场景下收盘数据已定，延迟无影响。2026-06-12 实测：
# 主域被封时 delay 域仍正常返回（K 线域 push2his 无等价备用域，
# push2dhis 只会跳转官网首页，不要加进来）。
FALLBACK_HOSTS = {
    "push2.eastmoney.com": "push2delay.eastmoney.com",
}

# 沪深京 A 股全集的市场筛选表达式（东财 fs 参数）
FS_ALL_A = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
FS_INDUSTRY_BOARD = "m:90 t:2 f:!50"  # 行业板块
FS_CONCEPT_BOARD = "m:90 t:3 f:!50"  # 概念板块

# 核心指数 secid（PRD 确认的指数范围）
INDEX_SECIDS: dict[str, tuple[str, str]] = {
    # symbol: (secid, 名称)
    "000001": ("1.000001", "上证指数"),
    "399001": ("0.399001", "深证成指"),
    "399006": ("0.399006", "创业板指"),
    "000688": ("1.000688", "科创50"),
    "000300": ("1.000300", "沪深300"),
    "000905": ("1.000905", "中证500"),
    "000852": ("1.000852", "中证1000"),
    "000016": ("1.000016", "上证50"),
    "899050": ("0.899050", "北证50"),
}


def to_secid(symbol: str) -> str:
    """6 位股票代码 → 东财 secid（市场前缀.代码）。

    规则：6 开头为沪市（前缀 1），其余（深市 0/3 开头、北交所 4/8/9 开头）前缀 0。
    """
    return f"{'1' if symbol.startswith('6') else '0'}.{symbol}"


def classify_market(symbol: str) -> str:
    """按代码段判断所属板块（用于主档展示与筛选）。"""
    if symbol.startswith(("688", "689")):
        return "科创板"
    if symbol.startswith(("300", "301", "302")):
        return "创业板"
    if symbol.startswith(("8", "4", "92")):
        return "北交所"
    return "主板"


class EastMoneyAdapter:
    """东财数据适配器。所有方法都是异步的，内部共享一个 HTTP 连接池。"""

    def __init__(self, *, delay_ms: int = 150, timeout: float = 12.0) -> None:
        # follow_redirects：东财会把部分请求 302 到 push2delay 域名（延迟行情池），
        # 不跟随会直接报错；跟随后数据一致（盘后增量场景无差别）。
        # 连接池：被封域的请求会挂到超时才释放连接，池等待（pool）必须给足，
        # 否则挂起请求占满池子时，其他健康域的请求会 PoolTimeout 误伤。
        # trust_env=False：国内数据源必须直连，忽略系统代理环境变量——
        # 用户开了梯子时代理会拦断/污染东财请求（实测 Server disconnected）。
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            timeout=httpx.Timeout(timeout, pool=60.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            trust_env=False,
        )
        self._throttle = Throttle(delay_ms)
        # 断路器：主域 → 已降级到的备用域（实例级，任务结束随实例销毁）
        self._host_override: dict[str, str] = {}
        # 连续限流计数与"死域"集合：无备用域的域连续失败 5 次后快速熔断，
        # 后续请求立即失败，不再为注定失败的请求空耗退避时间
        self._host_fail_streak: dict[str, int] = {}
        self._host_dead: set[str] = set()

    async def close(self) -> None:
        await self._client.aclose()

    # ---------------- 内部请求工具 ----------------

    async def _get_json(self, url: str, params: dict) -> dict:
        """带节流 + 重试的 GET 请求；主域被限流时降级备用域（断路器）。

        断路器：一旦确认主域被灰名单，本适配器实例的后续请求直接走
        备用域，不再反复撞主域（撞一次要付出 10s+ 的长退避代价）。
        """
        label = f"em:{params.get('secid', params.get('fs', ''))[:30]}"
        host = httpx.URL(url).host

        def make(req_url: str):
            async def do() -> dict:
                await self._throttle.wait()
                resp = await self._client.get(req_url, params=params)
                resp.raise_for_status()
                return resp.json()

            return do

        # 主域已断路 → 直接用备用域
        if host in self._host_override:
            backup_url = url.replace(host, self._host_override[host])
            return await with_retry(make(backup_url), label=f"{label}(备用域)")

        # 无备用域的"死域"（连续限流 5 次）→ 立即失败，调用方分项容错会记数；
        # 不再为注定失败的请求空耗 10s+ 的长退避（实测一轮能省 40+ 分钟）
        if host in self._host_dead:
            raise httpx.ConnectError(f"域 {host} 已熔断（连续限流），等解禁后重跑补齐")

        try:
            # 主域只试 2 次：限流时第二次失败即降级，避免长时间空耗
            result = await with_retry(make(url), retries=2, label=label)
        except Exception as e:  # noqa: BLE001 - 仅限流类错误才走备用域/熔断
            if not is_throttle_error(e):
                raise
            backup_host = FALLBACK_HOSTS.get(host)
            if backup_host is not None:
                logger.warning("[%s] 主域 %s 被限流，断路并降级到 %s", label, host, backup_host)
                self._host_override[host] = backup_host
                backup_url = url.replace(host, backup_host)
                return await with_retry(make(backup_url), label=f"{label}(备用域)")
            streak = self._host_fail_streak.get(host, 0) + 1
            self._host_fail_streak[host] = streak
            if streak >= 5:
                self._host_dead.add(host)
                logger.warning("域 %s 连续限流 %d 次，熔断该域的后续请求", host, streak)
            raise
        else:
            self._host_fail_streak[host] = 0
            return result

    async def _clist_all_pages(self, fs: str, fields: str) -> list[dict]:
        """clist 接口翻页抓全量（东财已硬限 100 条/页）。

        先拿第 1 页得知总数，再并发抓余下页面，失败页自动重试。
        """
        base = {
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f12",
            "pz": 100,
            "fs": fs,
            "fields": fields,
        }
        first = await self._get_json(CLIST_URL, {**base, "pn": 1})
        data = first.get("data") or {}
        total: int = data.get("total", 0)
        rows: list[dict] = list(data.get("diff") or [])
        if total <= 100:
            return rows

        pages = (total + 99) // 100

        async def fetch_page(page: int) -> list[dict]:
            result = await self._get_json(CLIST_URL, {**base, "pn": page})
            return list((result.get("data") or {}).get("diff") or [])

        # 并发抓余下页面；节流器已限制全局频率，这里的并发只是流水线化
        chunks = await asyncio.gather(*[fetch_page(p) for p in range(2, pages + 1)])
        for chunk in chunks:
            rows.extend(chunk)
        return rows

    # ---------------- 股票主档 ----------------

    async def fetch_stock_list(self) -> list[StockBasic]:
        """全市场股票主档：代码 + 名称（拼音由服务层入库时生成）。"""
        rows = await self._clist_all_pages(FS_ALL_A, "f12,f13,f14")
        basics: list[StockBasic] = []
        for r in rows:
            symbol = str(r.get("f12", ""))
            name = str(r.get("f14", ""))
            if not symbol or not name:
                continue
            if r.get("f13") == 1:
                exchange = "SH"
            else:
                exchange = "BJ" if classify_market(symbol) == "北交所" else "SZ"
            basics.append(
                StockBasic(
                    symbol=symbol, name=name, exchange=exchange, market=classify_market(symbol)
                )
            )
        logger.info("东财主档拉取完成：%d 只", len(basics))
        return basics

    # ---------------- 历史 K 线 ----------------

    async def _fetch_kline_raw(self, secid: str, fqt: int, beg: str, end: str) -> list[str]:
        """拉单只证券的日 K 线原始行（klt=101 日线；fqt: 0 不复权 / 2 后复权）。"""
        params = {
            "secid": secid,
            "klt": 101,
            "fqt": fqt,
            "beg": beg,
            "end": end,
            "fields1": "f1,f2,f3,f4,f5,f6",
            # f51日期 f52开 f53收 f54高 f55低 f56量 f57额 f58振幅 f59涨跌幅 f60涨跌额 f61换手
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
        data = (await self._get_json(KLINE_URL, params)).get("data") or {}
        return list(data.get("klines") or [])

    async def fetch_daily_bars(
        self, symbol: str, beg: str, end: str = "20500101"
    ) -> list[DailyBar]:
        """拉一只股票的日线（不复权）并计算后复权因子。

        做法：同时拉"不复权"与"后复权"两条序列，
        后复权因子 = 后复权收盘价 ÷ 不复权收盘价（逐日对应）。
        beg/end 格式 yyyyMMdd。
        """
        secid = to_secid(symbol)
        raw_lines, hfq_lines = await asyncio.gather(
            self._fetch_kline_raw(secid, 0, beg, end),
            self._fetch_kline_raw(secid, 2, beg, end),
        )
        # 后复权收盘价按日期索引（两条序列日期应一致，做防御性对齐）
        hfq_close: dict[str, float] = {}
        for line in hfq_lines:
            parts = line.split(",")
            hfq_close[parts[0]] = float(parts[2])

        bars: list[DailyBar] = []
        prev_factor = 1.0
        for line in raw_lines:
            p = line.split(",")
            date, close = p[0], float(p[2])
            factor = (hfq_close.get(date, close) / close) if close > 0 else 1.0
            # 数据清洗：东财对深度缩水的 ST 股会给出负/零后复权价（其加法
            # 复权模型的已知缺陷），负因子会颠倒回测涨跌方向。
            # 处理：沿用前一日因子（这些票价格不足 1 元，误差可忽略）。
            if factor <= 0:
                factor = prev_factor
            prev_factor = factor
            bars.append(
                DailyBar(
                    symbol=symbol,
                    trade_date=date,
                    open=float(p[1]),
                    close=close,
                    high=float(p[3]),
                    low=float(p[4]),
                    volume=float(p[5]),
                    amount=float(p[6]),
                    pct_change=float(p[8]),
                    turnover=float(p[10]),
                    adj_factor=round(factor, 6),
                )
            )
        return bars

    async def fetch_minute_bars(self, symbol: str, day: str) -> list[MinuteBar]:
        """拉一只股票指定交易日的 5 分钟 K 线（一天 48 根，一次请求拿全）。

        与日线同一个 kline 接口，只是 klt=5；fqt=0 不复权（盘中因子只看
        当日相对走势）。day 格式 yyyyMMdd，beg=end 限定只取当天。
        """
        params = {
            "secid": to_secid(symbol),
            "klt": 5,  # 5 分钟粒度
            "fqt": 0,
            "beg": day,
            "end": day,
            "fields1": "f1,f2,f3,f4,f5,f6",
            # 分钟线的 f51 是 "yyyy-MM-dd HH:mm" 时间戳，其余字段与日线一致
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
        }
        data = (await self._get_json(KLINE_URL, params)).get("data") or {}
        bars: list[MinuteBar] = []
        for line in data.get("klines") or []:
            p = line.split(",")
            bars.append(
                MinuteBar(
                    symbol=symbol,
                    dt=p[0],
                    trade_date=p[0][:10],
                    open=float(p[1]),
                    close=float(p[2]),
                    high=float(p[3]),
                    low=float(p[4]),
                    volume=float(p[5]),
                    amount=float(p[6]),
                )
            )
        return bars

    async def fetch_index_daily(
        self, index_symbol: str, beg: str, end: str = "20500101"
    ) -> list[DailyBar]:
        """拉指数日线（指数无复权概念，factor 恒为 1）。"""
        secid, _name = INDEX_SECIDS[index_symbol]
        lines = await self._fetch_kline_raw(secid, 0, beg, end)
        bars: list[DailyBar] = []
        for line in lines:
            p = line.split(",")
            bars.append(
                DailyBar(
                    symbol=index_symbol,
                    trade_date=p[0],
                    open=float(p[1]),
                    close=float(p[2]),
                    high=float(p[3]),
                    low=float(p[4]),
                    volume=float(p[5]),
                    amount=float(p[6]),
                    pct_change=float(p[8]),
                    turnover=float(p[10]),
                )
            )
        return bars

    # ---------------- 板块 ----------------

    async def fetch_board_list(self) -> list[BoardInfo]:
        """行业板块 + 概念板块列表。"""
        industry, concept = await asyncio.gather(
            self._clist_all_pages(FS_INDUSTRY_BOARD, "f12,f14"),
            self._clist_all_pages(FS_CONCEPT_BOARD, "f12,f14"),
        )
        boards = [
            BoardInfo(code=str(r["f12"]), name=str(r["f14"]), type="industry") for r in industry
        ] + [BoardInfo(code=str(r["f12"]), name=str(r["f14"]), type="concept") for r in concept]
        logger.info("板块列表：行业 %d 个 + 概念 %d 个", len(industry), len(concept))
        return boards

    async def fetch_board_members(self, board_code: str) -> list[str]:
        """某板块的成分股代码列表。"""
        rows = await self._clist_all_pages(f"b:{board_code}", "f12")
        return [str(r["f12"]) for r in rows if r.get("f12")]

    async def fetch_board_daily(
        self, board_code: str, beg: str, end: str = "20500101"
    ) -> list[DailyBar]:
        """板块指数日线（secid 前缀 90）。"""
        lines = await self._fetch_kline_raw(f"90.{board_code}", 0, beg, end)
        bars: list[DailyBar] = []
        for line in lines:
            p = line.split(",")
            bars.append(
                DailyBar(
                    symbol=board_code,
                    trade_date=p[0],
                    open=float(p[1]),
                    close=float(p[2]),
                    high=float(p[3]),
                    low=float(p[4]),
                    volume=float(p[5]),
                    amount=float(p[6]),
                    pct_change=float(p[8]),
                    turnover=float(p[10]),
                )
            )
        return bars

    # ---------------- 全市场快照 ----------------

    async def fetch_spot_snapshot(self) -> list[Quote]:
        """全市场实时/收盘快照：每日增量同步的主数据源。

        一次翻页抓全市场（约 59 页），含当日 OHLCV + 估值（PE/PB/市值）。
        盘后调用拿到的即当日收盘数据。
        """
        fields = "f12,f14,f2,f3,f4,f5,f6,f8,f9,f23,f20,f21,f15,f16,f17,f18,f10"
        rows = await self._clist_all_pages(FS_ALL_A, fields)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        quotes: list[Quote] = []
        for r in rows:
            # 东财停牌/未上市股票数值字段会给 "-"，统一转 0
            def num(key: str, row: dict = r) -> float:
                v = row.get(key)
                return float(v) if isinstance(v, int | float) else 0.0

            symbol = str(r.get("f12", ""))
            if not symbol:
                continue
            quotes.append(
                Quote(
                    symbol=symbol,
                    name=str(r.get("f14", "")),
                    price=num("f2"),
                    pct_change=num("f3"),
                    change=num("f4"),
                    volume=num("f5"),
                    amount=num("f6"),
                    turnover=num("f8"),
                    pe_ttm=num("f9"),
                    pb=num("f23"),
                    total_mv=num("f20"),
                    circ_mv=num("f21"),
                    high=num("f15"),
                    low=num("f16"),
                    open=num("f17"),
                    prev_close=num("f18"),
                    ts=now,
                )
            )
        return quotes
