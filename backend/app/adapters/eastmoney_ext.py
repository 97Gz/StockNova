"""东方财富扩展数据适配器（M5）：新闻 / 资金流 / 龙虎榜 / 业绩预告 / 人气榜。

数据源实测依据见 scripts/probe_m5_sources.py 的探测结论（2026-06-12）：
- 7×24 快讯：np-weblist getFastNewsList（必须带 req_trace 参数，否则返回空表）
- 个股新闻：search-api-web 搜索接口（与 AKShare stock_news_em 同源，直连更快）
- 资金流排行：push2 clist fid=f62（主域被灰名单时自动降级 push2delay）
- 龙虎榜 / 业绩预告：datacenter-web 公开报表接口
- 人气榜：emappdata POST 接口（股吧人气，返回带交易所前缀的代码）

这些数据"从接入日起逐日积累"——东财不提供完整历史，因此相关策略
不支持机械历史回测（builtin.py 中标记 no_backtest）。
"""

import json
import logging
import re
import uuid
from typing import Any

import httpx

from app.adapters.base import NewsItem, Throttle, is_throttle_error, with_retry

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://www.eastmoney.com/",
}

FAST_NEWS_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
STOCK_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
CLIST_FALLBACK_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
POPULARITY_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"

# 沪深京 A 股全集（与 eastmoney.py 的 FS_ALL_A 一致）
FS_ALL_A = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"

# 资金流排行字段：代码/名称/今日主力净额/净占比/3日/5日/10日累计/股息率
FFLOW_FIELDS = "f12,f14,f62,f184,f267,f164,f174,f133"


def _num(v: Any) -> float:
    """东财数值字段可能给 '-'（无数据），统一转 0.0。"""
    return float(v) if isinstance(v, int | float) else 0.0


def _stock_codes(stock_list: list) -> list[str]:
    """快讯 stockList → A 股 6 位代码列表。

    实测元素为"市场前缀.代码"字符串（如 "0.000032" 深 / "1.600519" 沪 /
    "105.SPCX" 美 / "116.00700" 港）。只保留 A 股（前缀 0/1 且代码为 6 位数字），
    其余市场丢弃 —— 本应用只覆盖 A 股，且港股 5 位代码截尾会误撞 A 股代码。
    """
    out: list[str] = []
    for s in stock_list:
        code = s.get("code", "") if isinstance(s, dict) else str(s)
        market, _, symbol = code.partition(".")
        if market in ("0", "1") and len(symbol) == 6 and symbol.isdigit():
            out.append(symbol)
    return out


def parse_fast_news(data: dict) -> tuple[list[NewsItem], str]:
    """快讯接口响应 → (条目列表, 翻页游标 sortEnd)。纯函数便于单测。"""
    payload = data.get("data") or {}
    items: list[NewsItem] = []
    for r in payload.get("fastNewsList") or []:
        stocks = _stock_codes(r.get("stockList") or [])
        items.append(
            NewsItem(
                code=str(r.get("code", "")),
                title=str(r.get("title", "")),
                summary=str(r.get("summary", "")),
                publish_time=str(r.get("showTime", "")),
                media="东方财富快讯",
                url=f"https://finance.eastmoney.com/a/{r.get('code', '')}.html",
                stocks=stocks or None,
            )
        )
    return items, str(payload.get("sortEnd", ""))


def parse_stock_news(body: dict) -> list[NewsItem]:
    """个股新闻搜索响应 → 条目列表（标题/摘要去掉高亮标签）。"""
    arts = (body.get("result") or {}).get("cmsArticleWebOld") or []
    tag = re.compile(r"</?em>")
    return [
        NewsItem(
            code=str(r.get("code", "")),
            title=tag.sub("", str(r.get("title", ""))),
            summary=tag.sub("", str(r.get("content", ""))),
            publish_time=str(r.get("date", "")),
            media=str(r.get("mediaName", "")),
            url=str(r.get("url", "")),
        )
        for r in arts
    ]


class EastMoneyExtAdapter:
    """扩展数据适配器。与 EastMoneyAdapter 相互独立（生命周期不同：
    本适配器由 ext_sync / news 服务常驻持有，而非按同步任务创建销毁）。"""

    def __init__(self, *, delay_ms: int = 200, timeout: float = 15.0) -> None:
        # trust_env=False：国内数据源直连，忽略系统代理（代理会拦断东财请求）
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            timeout=httpx.Timeout(timeout, pool=60.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            trust_env=False,
        )
        self._throttle = Throttle(delay_ms)
        self._clist_use_fallback = False  # push2 主域被灰名单后切 delay 域

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_json(self, url: str, params: dict, *, label: str) -> dict:
        async def do() -> dict:
            await self._throttle.wait()
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

        return await with_retry(do, label=label)

    # ---------------- 新闻 ----------------

    async def fetch_fast_news(
        self, *, cursor: str = "", page_size: int = 50, column: str = "102"
    ) -> tuple[list[NewsItem], str]:
        """7×24 快讯流。cursor 为上一页返回的 sortEnd（翻更早的内容）。

        column：102=全部 101=重点。req_trace 必须带（实测缺失时返回空表）。
        """
        params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": column,
            "sortEnd": cursor,
            "pageSize": str(min(page_size, 200)),
            "req_trace": uuid.uuid4().hex,
        }
        data = await self._get_json(FAST_NEWS_URL, params, label="news:fast")
        return parse_fast_news(data)

    async def fetch_stock_news(self, symbol: str, *, page_size: int = 20) -> list[NewsItem]:
        """个股新闻：按 6 位代码搜索东财资讯库，按时间倒序。"""
        param = {
            "uid": "",
            "keyword": symbol,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "time",  # 按时间倒序（情绪分析要看最新消息）
                    "pageIndex": 1,
                    "pageSize": min(page_size, 50),
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }

        async def do() -> list[NewsItem]:
            await self._throttle.wait()
            resp = await self._client.get(
                STOCK_NEWS_URL,
                params={"cb": "jq", "param": json.dumps(param, ensure_ascii=False)},
            )
            resp.raise_for_status()
            text = resp.text  # JSONP：jq({...}) → 剥外壳
            body = json.loads(text[text.index("(") + 1 : text.rindex(")")])
            return parse_stock_news(body)

        return await with_retry(do, label=f"news:{symbol}")

    # ---------------- 资金流排行（全市场，含股息率） ----------------

    async def fetch_fund_flow_rank(self) -> list[dict]:
        """全市场当日资金流快照（翻页抓全量，约 56 页）。

        返回行字段：symbol/main_net/main_pct/net_3d/net_5d/net_10d/dv_ttm。
        push2 主域 502 时自动切换 push2delay（延迟 15 分钟池，盘后无差别）。
        """
        base = {
            "fid": "f62",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "pz": "100",
            "fs": FS_ALL_A,
            "fields": FFLOW_FIELDS,
        }

        async def page(pn: int) -> dict:
            url = CLIST_FALLBACK_URL if self._clist_use_fallback else CLIST_URL
            try:
                return await self._get_json(url, {**base, "pn": str(pn)}, label=f"fflow:p{pn}")
            except Exception as e:  # noqa: BLE001 - 限流时降级备用域重试一次
                if self._clist_use_fallback or not is_throttle_error(e):
                    raise
                logger.warning("push2 主域被限流，资金流排行切换 push2delay 域")
                self._clist_use_fallback = True
                return await self._get_json(
                    CLIST_FALLBACK_URL, {**base, "pn": str(pn)}, label=f"fflow:p{pn}(备用)"
                )

        first = await page(1)
        data = first.get("data") or {}
        total: int = data.get("total", 0)
        rows: list[dict] = list(data.get("diff") or [])
        pages = (total + 99) // 100
        for pn in range(2, pages + 1):
            rows.extend(((await page(pn)).get("data") or {}).get("diff") or [])

        return [
            {
                "symbol": str(r.get("f12", "")),
                "main_net": _num(r.get("f62")),
                "main_pct": _num(r.get("f184")),
                "net_3d": _num(r.get("f267")),
                "net_5d": _num(r.get("f164")),
                "net_10d": _num(r.get("f174")),
                "dv_ttm": _num(r.get("f133")),
            }
            for r in rows
            if r.get("f12")
        ]

    # ---------------- 龙虎榜 ----------------

    async def fetch_dragon_tiger(self, start_date: str, end_date: str) -> list[dict]:
        """龙虎榜明细（东财数据中心，日期为 yyyy-MM-dd）。"""
        rows: list[dict] = []
        pn = 1
        while True:
            params = {
                "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
                "columns": (
                    "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLAIN,CLOSE_PRICE,"
                    "CHANGE_RATE,BILLBOARD_NET_AMT,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,"
                    "TURNOVERRATE"
                ),
                "filter": f"(TRADE_DATE>='{start_date}')(TRADE_DATE<='{end_date}')",
                "pageNumber": str(pn),
                "pageSize": "500",
                "sortColumns": "TRADE_DATE,SECURITY_CODE",
                "sortTypes": "-1,1",
                "source": "WEB",
                "client": "WEB",
            }
            body = await self._get_json(DATACENTER_URL, params, label=f"dt:p{pn}")
            result = body.get("result") or {}
            page_rows = result.get("data") or []
            rows.extend(page_rows)
            if pn >= int(result.get("pages") or 1):
                break
            pn += 1

        out: list[dict] = []
        for r in rows:
            reason = str(r.get("EXPLAIN") or "")
            out.append(
                {
                    "trade_date": str(r.get("TRADE_DATE", ""))[:10],
                    "symbol": str(r.get("SECURITY_CODE", "")),
                    "name": str(r.get("SECURITY_NAME_ABBR", "")),
                    "close": _num(r.get("CLOSE_PRICE")),
                    "pct_change": _num(r.get("CHANGE_RATE")),
                    "net_amt": _num(r.get("BILLBOARD_NET_AMT")),
                    "buy_amt": _num(r.get("BILLBOARD_BUY_AMT")),
                    "sell_amt": _num(r.get("BILLBOARD_SELL_AMT")),
                    "turnover": _num(r.get("TURNOVERRATE")),
                    "reason": reason,
                    "has_inst": "机构买入" in reason,
                }
            )
        return out

    # ---------------- 业绩预告 ----------------

    async def fetch_earnings_forecast(self, report_date: str) -> list[dict]:
        """某报告期的全部业绩预告（report_date 如 2026-06-30）。"""
        rows: list[dict] = []
        pn = 1
        while True:
            params = {
                "reportName": "RPT_PUBLIC_OP_NEWPREDICT",
                "columns": (
                    "SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,NOTICE_DATE,PREDICT_TYPE,"
                    "ADD_AMP_LOWER,ADD_AMP_UPPER,PREDICT_CONTENT"
                ),
                "filter": f"(REPORT_DATE='{report_date}')",
                "pageNumber": str(pn),
                "pageSize": "500",
                "sortColumns": "NOTICE_DATE,SECURITY_CODE",
                "sortTypes": "-1,1",
                "source": "WEB",
                "client": "WEB",
            }
            body = await self._get_json(DATACENTER_URL, params, label=f"earn:p{pn}")
            result = body.get("result") or {}
            page_rows = result.get("data") or []
            rows.extend(page_rows)
            if pn >= int(result.get("pages") or 1):
                break
            pn += 1

        return [
            {
                "symbol": str(r.get("SECURITY_CODE", "")),
                "name": str(r.get("SECURITY_NAME_ABBR", "")),
                "report_date": str(r.get("REPORT_DATE", ""))[:10],
                "notice_date": str(r.get("NOTICE_DATE", ""))[:10],
                "predict_type": str(r.get("PREDICT_TYPE") or ""),
                "amp_lower": _num(r.get("ADD_AMP_LOWER")),
                "amp_upper": _num(r.get("ADD_AMP_UPPER")),
                "content": str(r.get("PREDICT_CONTENT") or "")[:500],
            }
            for r in rows
        ]

    # ---------------- 人气榜 ----------------

    async def fetch_popularity_rank(self, *, top: int = 100) -> list[dict]:
        """股吧人气榜（POST 接口）。

        实测（2026-06-13）：接口只提供前 100 名，pageNo>=2 返回空 —— top 上限 100。
        rc 字段实测盘后恒为 0（盘中实时变化），用 hisRc（较昨日变化）。
        """
        rows: list[dict] = []
        for pn in range(1, (min(top, 100) + 99) // 100 + 1):
            payload = {
                "appId": "appId01",
                "globalId": uuid.uuid4().hex[:32],
                "marketType": "",
                "pageNo": pn,
                "pageSize": 100,
            }

            async def do(body: dict = payload, page: int = pn) -> list[dict]:
                await self._throttle.wait()
                resp = await self._client.post(POPULARITY_URL, json=body)
                resp.raise_for_status()
                return resp.json().get("data") or []

            rows.extend(await with_retry(do, label=f"pop:p{pn}"))

        out: list[dict] = []
        for r in rows:
            sc = str(r.get("sc", ""))  # 形如 SH603993 / SZ002407
            if len(sc) < 8:
                continue
            his = r.get("hisRc")
            out.append(
                {
                    "symbol": sc[2:],
                    "rank": int(r.get("rk", 0)),
                    # hisRc 为 -1 时代表新上榜（东财口径），按大幅跃升处理
                    "rank_chg": 999 if his == -1 else int(his or 0),
                }
            )
        return out
