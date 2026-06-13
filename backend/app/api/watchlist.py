"""自选股接口（M2）：清单 CRUD + 带实时报价的列表。

增删后同步刷新 QuoteService 的轮询订阅集合，
盘中 WS 推送的标的范围随清单实时变化。
"""

import logging

from fastapi import APIRouter, Body, Request

from app.core.exceptions import BizError, ok
from app.services import holdings_service, watchlist_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _refresh_subscription(request: Request) -> None:
    """清单变化后更新报价轮询的订阅集合（自选 ∪ 持仓）。"""
    request.app.state.quote_service.set_watch_symbols(
        watchlist_service.list_symbols() + holdings_service.list_symbols()
    )


@router.get("")
async def list_watchlist(request: Request) -> dict:
    """自选清单 + 最新报价（页面首载一次拿全，盘中增量靠 WS）。"""
    items = watchlist_service.list_items()
    quotes: dict[str, dict] = {}
    if items:
        try:
            snap = await request.app.state.quote_service.snapshot([it["symbol"] for it in items])
            quotes = {q["symbol"]: q for q in snap}
        except Exception:  # noqa: BLE001 - 报价失败时清单照常返回
            logger.warning("自选报价快照失败", exc_info=True)
    for it in items:
        it["quote"] = quotes.get(it["symbol"])
        # 补股票名称（报价失败时从主档兜底）
        if it["quote"] is None:
            basic = request.app.state.market_store.get_stock_basic(it["symbol"])
            it["name"] = basic["name"] if basic else it["symbol"]
        else:
            it["name"] = it["quote"]["name"]
    # 内联每只自选最近一次诊断的决策摘要（评级/评分/目标价等），供列表 AI 列展示
    ai_map = request.app.state.diagnosis_service.latest_map([it["symbol"] for it in items])
    for it in items:
        it["ai"] = ai_map.get(it["symbol"])
    return ok(items)


@router.post("")
def add_watchlist(request: Request, symbol: str = Body(..., embed=True)) -> dict:
    # 先校验代码真实存在（防手输错误代码进清单，后续报价/K线全为空）
    if request.app.state.market_store.get_stock_basic(symbol.strip()) is None:
        raise BizError(40404, f"股票代码 {symbol} 不存在", http_status=404)
    result = watchlist_service.add(symbol)
    _refresh_subscription(request)
    return ok(result)


@router.delete("/{symbol}")
def remove_watchlist(request: Request, symbol: str) -> dict:
    watchlist_service.remove(symbol)
    _refresh_subscription(request)
    return ok()


@router.put("/{symbol}/note")
def update_note(request: Request, symbol: str, note: str = Body("", embed=True)) -> dict:
    watchlist_service.update_note(symbol, note)
    return ok()
