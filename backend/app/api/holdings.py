"""持仓诊断接口（M6）。

- GET    /holdings           持仓清单（含实时报价、盈亏明细与组合总览）
- POST   /holdings           录入持仓 {symbol, shares, cost_price, note?}
- PUT    /holdings/{id}      修改持仓（股数/成本/备注）
- DELETE /holdings/{id}      删除持仓
- POST   /holdings/{symbol}/diagnose  发起带持仓上下文的 AI 诊断（割/守/补）
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.database import create_session
from app.core.exceptions import BizError, ok
from app.services import holdings_service, settings_service, watchlist_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/holdings", tags=["holdings"])


def _total_capital() -> float:
    """读取用户配置的账户总资金（元）；未设置为 0。"""
    with create_session() as db:
        return float(settings_service.get_value(db, "portfolio.total_capital") or 0.0)


def _refresh_subscription(request: Request) -> None:
    """持仓变化后刷新报价轮询订阅集合（自选 ∪ 持仓），使新持仓盘中即时跳动。"""
    request.app.state.quote_service.set_watch_symbols(
        watchlist_service.list_symbols() + holdings_service.list_symbols()
    )


class HoldingBody(BaseModel):
    symbol: str = ""
    shares: int
    cost_price: float
    note: str = ""


class ImportRow(BaseModel):
    symbol: str
    shares: float = 0
    cost_price: float = 0
    note: str = ""


class ImportBody(BaseModel):
    items: list[ImportRow]


async def _quote_map(request: Request, symbols: list[str]) -> dict[str, dict]:
    """拉一轮持仓股的报价快照；失败返回空 map（页面降级为成本价展示）。"""
    if not symbols:
        return {}
    try:
        snap = await request.app.state.quote_service.snapshot(symbols)
        return {q["symbol"]: q for q in snap}
    except Exception:  # noqa: BLE001 - 报价失败时清单照常返回
        logger.warning("持仓报价快照失败", exc_info=True)
        return {}


@router.get("")
async def list_holdings(request: Request) -> dict:
    """持仓清单 + 组合总览（市值/盈亏按实时报价计算，并内联最近一次 AI 诊断摘要）。"""
    items = holdings_service.list_items()
    symbols = [it["symbol"] for it in items]
    quotes = await _quote_map(request, symbols)
    # 报价缺失时用主档兜底股票名称
    for it in items:
        if it["symbol"] not in quotes:
            basic = request.app.state.market_store.get_stock_basic(it["symbol"])
            it["name"] = basic["name"] if basic else it["symbol"]
    data = holdings_service.enrich(items, quotes, total_capital=_total_capital())
    # 内联每只持仓最近一次诊断的决策摘要（操作建议/目标价/止损价/评分等）
    ai_map = request.app.state.diagnosis_service.latest_map(symbols)
    for it in data["items"]:
        it["ai"] = ai_map.get(it["symbol"])
    return ok(data)


class CapitalBody(BaseModel):
    total_capital: float = 0.0


@router.put("/capital")
def set_total_capital(body: CapitalBody) -> dict:
    """设置/更新账户总资金（元）。0 表示清除（恢复未设置态）。"""
    value = max(0.0, float(body.total_capital))
    with create_session() as db:
        settings_service.update_values(db, {"portfolio.total_capital": value})
    return ok({"total_capital": value})


@router.post("")
def add_holding(request: Request, body: HoldingBody) -> dict:
    """录入持仓。代码必须真实存在（与自选股同样的防呆校验）。"""
    symbol = body.symbol.strip()
    if request.app.state.market_store.get_stock_basic(symbol) is None:
        raise BizError(40404, f"股票代码 {symbol} 不存在", http_status=404)
    result = holdings_service.add(symbol, body.shares, body.cost_price, body.note)
    _refresh_subscription(request)
    return ok(result)


@router.post("/import")
def import_holdings(request: Request, body: ImportBody) -> dict:
    """批量导入持仓（CSV）：按代码 upsert，校验代码真实存在；逐行容错。"""
    store = request.app.state.market_store
    rows: list[dict] = []
    invalid: list[dict] = []
    for r in body.items:
        symbol = r.symbol.strip()
        # 代码不存在的行直接计入失败，不污染批量写入
        if not symbol or store.get_stock_basic(symbol) is None:
            invalid.append({"symbol": symbol or "(空)", "error": "代码不存在"})
            continue
        rows.append(
            {"symbol": symbol, "shares": r.shares, "cost_price": r.cost_price, "note": r.note}
        )
    result = holdings_service.upsert_many(rows)
    result["failed"] = invalid + result["failed"]
    _refresh_subscription(request)
    return ok(result)


@router.put("/{holding_id}")
def update_holding(holding_id: int, body: HoldingBody, request: Request) -> dict:
    """修改持仓（加减仓后自行更新摊薄成本）。"""
    holdings_service.update(holding_id, body.shares, body.cost_price, body.note)
    _refresh_subscription(request)
    return ok({"updated": True})


@router.delete("/{holding_id}")
def remove_holding(holding_id: int, request: Request) -> dict:
    """删除持仓（清仓移除）。"""
    holdings_service.remove(holding_id)
    _refresh_subscription(request)
    return ok()


@router.post("/{symbol}/diagnose")
async def diagnose_holding(symbol: str, request: Request, mode: str = "deep") -> dict:
    """发起带持仓上下文的多角色 AI 诊断。

    与普通诊股共用同一条工作流，差异只在：组合经理的输入末尾
    附加了该股的持仓成本/浮亏情况，要求评级落到「割/守/补」。
    mode：deep=完整工作流；quick=快速模式。
    """
    quotes = await _quote_map(request, [symbol])
    ctx = holdings_service.position_context(
        symbol, quotes.get(symbol), total_capital=_total_capital()
    )
    if not ctx:
        raise BizError(40012, f"{symbol} 没有持仓记录，请先录入")
    return ok(request.app.state.diagnosis_service.start(symbol, user_context=ctx, mode=mode))
