"""行情中心接口（M2）：指数行情 / 盘面统计 / 板块热力 / K 线 / 搜索 / 个股信息。

数据来源分两类：
- 实时类（指数行情、个股最新价）：腾讯报价接口现拉（免费、无频率压力）；
- 盘后类（涨跌分布、板块热力、K 线、成交额趋势）：直接查 DuckDB 行情库。
"""

import logging

from fastapi import APIRouter, Query, Request

from app.core.exceptions import BizError, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["market"])

# 指数的腾讯报价代码：必须带市场前缀（与同 6 位代码的个股区分）
INDEX_TENCENT: list[tuple[str, str, str]] = [
    # (6位代码, 腾讯代码, 名称)
    ("000001", "sh000001", "上证指数"),
    ("399001", "sz399001", "深证成指"),
    ("399006", "sz399006", "创业板指"),
    ("000688", "sh000688", "科创50"),
    ("000300", "sh000300", "沪深300"),
    ("000905", "sh000905", "中证500"),
]


@router.get("/indices")
async def indices(request: Request) -> dict:
    """核心指数实时行情（盘后返回收盘数据，同一接口通吃）。

    腾讯接口失败时降级读库（index_daily 最后一根），保证卡片永远有数。
    """
    quote_service = request.app.state.quote_service
    store = request.app.state.market_store
    try:
        raw = await quote_service.snapshot([code for _, code, _ in INDEX_TENCENT])
        # 指数代码在腾讯返回里也是 6 位（去掉前缀），按顺序对齐名称
        result = []
        for (symbol, _, name), q in zip(INDEX_TENCENT, raw, strict=False):
            result.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "price": q["price"],
                    "change": q["change"],
                    "pct_change": q["pct_change"],
                    "amount": q["amount"],
                    "ts": q["ts"],
                }
            )
        if result:
            return ok(result)
    except Exception:  # noqa: BLE001 - 实时源故障降级读库
        logger.warning("指数实时报价失败，降级读取库内日线", exc_info=True)

    fallback = []
    for symbol, _, name in INDEX_TENCENT:
        bars = store.query_index_daily(symbol, limit=1)
        if bars:
            b = bars[-1]
            fallback.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "price": b["close"],
                    "change": round(b["close"] * b["pct_change"] / 100, 2),
                    "pct_change": b["pct_change"],
                    "amount": b["amount"],
                    "ts": b["date"],
                }
            )
    return ok(fallback)


@router.get("/overview")
def overview(request: Request) -> dict:
    """最新交易日盘面统计：涨跌分布桶 + 涨跌家数 + 总成交额 + 30 日成交趋势。"""
    store = request.app.state.market_store
    data = store.eod_market_overview()
    data["amount_trend"] = store.amount_trend(30)
    return ok(data)


@router.get("/board-heat")
def board_heat(
    request: Request,
    type: str = Query("industry", pattern="^(industry|concept)$"),  # noqa: A002
    limit: int = Query(40, ge=5, le=100),
) -> dict:
    """板块热力图数据（面积=成交额，颜色=涨跌幅）。"""
    return ok(request.app.state.market_store.eod_board_heat(type, limit))


@router.get("/board/{code}")
def board_detail(request: Request, code: str) -> dict:
    """板块详情：板块概览 + 近10日走势 + 成分股最新行情（按涨幅排序，含主力资金）。"""
    detail = request.app.state.market_store.board_detail(code)
    if detail is None:
        raise BizError(40404, f"未找到板块 {code}", http_status=404)
    return ok(detail)


@router.get("/search")
def search(request: Request, q: str = Query(..., min_length=1, max_length=20)) -> dict:
    """全局搜索：代码 / 名称 / 拼音首字母。"""
    return ok(request.app.state.market_store.search_stocks(q, limit=20))


@router.get("/stock/{symbol}")
async def stock_info(request: Request, symbol: str) -> dict:
    """个股详情页头部聚合：主档 + 所属板块 + 最新估值 + 实时报价。"""
    store = request.app.state.market_store
    basic = store.get_stock_basic(symbol)
    if basic is None:
        raise BizError(40404, f"未找到股票 {symbol}", http_status=404)

    quote = None
    try:
        snap = await request.app.state.quote_service.snapshot([symbol])
        quote = snap[0] if snap else None
    except Exception:  # noqa: BLE001 - 报价失败不影响主档展示
        logger.warning("个股 %s 实时报价失败", symbol, exc_info=True)

    return ok(
        {
            "basic": basic,
            "boards": store.stock_board_names(symbol),
            "fundamentals": store.latest_fundamentals(symbol),
            "quote": quote,
        }
    )


@router.get("/kline/{symbol}")
def kline(request: Request, symbol: str, limit: int = Query(500, ge=30, le=3000)) -> dict:
    """个股日 K 线（前复权），KLineCharts 直接可用。"""
    bars = request.app.state.market_store.query_daily_bars(symbol, limit)
    if not bars:
        raise BizError(40405, f"暂无 {symbol} 的K线数据", http_status=404)
    return ok(bars)


@router.get("/index-kline/{symbol}")
def index_kline(request: Request, symbol: str, limit: int = Query(500, ge=30, le=3000)) -> dict:
    """指数日 K 线（仪表盘指数走势卡）。"""
    bars = request.app.state.market_store.query_index_daily(symbol, limit)
    if not bars:
        raise BizError(40405, f"暂无指数 {symbol} 的K线数据", http_status=404)
    return ok(bars)


@router.get("/fundflow/{symbol}")
def stock_fund_flow(request: Request, symbol: str, days: int = Query(30, ge=1, le=120)) -> dict:
    """个股近 N 日主力资金流（M5 扩展数据，从接入日起逐日积累）。"""
    return ok(request.app.state.market_store.stock_fund_flow(symbol, days))
