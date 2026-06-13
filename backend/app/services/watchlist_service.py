"""自选股服务：清单 CRUD（SQLite）。

报价不在这里处理——自选页的实时价由 QuoteService 的 WS 推送
（盘中）或 REST snapshot（页面首载）提供，本服务只管"哪些股票在册"。
"""

from sqlalchemy import select

from app.core.database import create_session
from app.core.exceptions import BizError
from app.models.orm import WatchlistItem


def list_symbols() -> list[str]:
    """自选代码列表（按 sort_order）——QuoteService 订阅与页面共用。"""
    with create_session() as db:
        rows = db.execute(
            select(WatchlistItem.symbol).order_by(WatchlistItem.sort_order, WatchlistItem.id)
        ).all()
    return [r[0] for r in rows]


def list_items() -> list[dict]:
    """完整清单（含备注），自选页表格用。"""
    with create_session() as db:
        items = (
            db.execute(select(WatchlistItem).order_by(WatchlistItem.sort_order, WatchlistItem.id))
            .scalars()
            .all()
        )
        return [
            {
                "id": it.id,
                "symbol": it.symbol,
                "note": it.note,
                "created_at": it.created_at.isoformat(sep=" ", timespec="seconds"),
            }
            for it in items
        ]


def add(symbol: str, note: str = "") -> dict:
    """加自选。重复添加报业务错误（前端给出"已在自选中"提示）。"""
    symbol = symbol.strip()
    if not symbol:
        raise BizError(40010, "股票代码不能为空")
    with create_session() as db:
        exists = db.execute(
            select(WatchlistItem).where(WatchlistItem.symbol == symbol)
        ).scalar_one_or_none()
        if exists is not None:
            raise BizError(40011, f"{symbol} 已在自选中")
        max_order = db.query(WatchlistItem).count()
        item = WatchlistItem(symbol=symbol, note=note, sort_order=max_order)
        db.add(item)
        db.commit()
        return {"id": item.id, "symbol": item.symbol}


def remove(symbol: str) -> None:
    with create_session() as db:
        item = db.execute(
            select(WatchlistItem).where(WatchlistItem.symbol == symbol)
        ).scalar_one_or_none()
        if item is None:
            raise BizError(40012, f"{symbol} 不在自选中")
        db.delete(item)
        db.commit()


def update_note(symbol: str, note: str) -> None:
    with create_session() as db:
        item = db.execute(
            select(WatchlistItem).where(WatchlistItem.symbol == symbol)
        ).scalar_one_or_none()
        if item is None:
            raise BizError(40012, f"{symbol} 不在自选中")
        item.note = note
        db.commit()
