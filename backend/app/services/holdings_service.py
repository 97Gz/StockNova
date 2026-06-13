"""持仓服务（M6）：持仓 CRUD + 盈亏总览计算。

持仓数据是用户手工录入的（股数 + 摊薄成本价），本服务负责：
- 清单的增删改查（SQLite holdings 表）
- 把实时报价叠到每条持仓上，算出市值/浮动盈亏/当日盈亏/仓位占比
- 汇总成组合总览（总市值/总成本/总盈亏/今日盈亏）

报价由调用方（API 层）传入 —— 服务本身不依赖 QuoteService，
这样单元验证可以直接喂假报价。
"""

from sqlalchemy import select

from app.core.database import create_session
from app.core.exceptions import BizError
from app.models.orm import Holding


def list_symbols() -> list[str]:
    """持仓代码列表（拉报价快照用）。"""
    with create_session() as db:
        rows = db.execute(select(Holding.symbol).order_by(Holding.id)).all()
    return [r[0] for r in rows]


def list_items() -> list[dict]:
    """持仓原始清单（不含报价与盈亏，由 enrich 补全）。"""
    with create_session() as db:
        items = db.execute(select(Holding).order_by(Holding.id)).scalars().all()
        return [
            {
                "id": it.id,
                "symbol": it.symbol,
                "shares": it.shares,
                "cost_price": it.cost_price,
                "note": it.note,
                "created_at": it.created_at.isoformat(sep=" ", timespec="seconds"),
            }
            for it in items
        ]


def add(symbol: str, shares: int, cost_price: float, note: str = "") -> dict:
    """录入一笔持仓。同一只股票只允许一条记录（多笔买入自行摊薄成本）。"""
    symbol = symbol.strip()
    if not symbol:
        raise BizError(40010, "股票代码不能为空")
    if shares <= 0:
        raise BizError(40013, "持股数量必须大于 0")
    if cost_price <= 0:
        raise BizError(40013, "成本价必须大于 0")
    with create_session() as db:
        exists = db.execute(select(Holding).where(Holding.symbol == symbol)).scalar_one_or_none()
        if exists is not None:
            raise BizError(40011, f"{symbol} 已有持仓记录，可直接编辑股数与成本")
        item = Holding(symbol=symbol, shares=shares, cost_price=cost_price, note=note)
        db.add(item)
        db.commit()
        return {"id": item.id, "symbol": item.symbol}


def update(holding_id: int, shares: int, cost_price: float, note: str = "") -> None:
    """修改一笔持仓（加减仓后用户自行更新摊薄成本）。"""
    if shares <= 0:
        raise BizError(40013, "持股数量必须大于 0")
    if cost_price <= 0:
        raise BizError(40013, "成本价必须大于 0")
    with create_session() as db:
        item = db.get(Holding, holding_id)
        if item is None:
            raise BizError(40012, "持仓记录不存在")
        item.shares = shares
        item.cost_price = cost_price
        item.note = note
        db.commit()


def remove(holding_id: int) -> None:
    """删除一笔持仓（清仓后移除）。"""
    with create_session() as db:
        item = db.get(Holding, holding_id)
        if item is None:
            raise BizError(40012, "持仓记录不存在")
        db.delete(item)
        db.commit()


def upsert_many(rows: list[dict]) -> dict:
    """批量导入持仓（CSV 导入用）：按代码 upsert，已存在则覆盖股数/成本/备注。

    rows 每项形如 {symbol, shares, cost_price, note}。
    返回 {added, updated, failed:[{symbol, error}]}，逐行容错不中断整批。
    """
    added = 0
    updated = 0
    failed: list[dict] = []
    with create_session() as db:
        for r in rows:
            symbol = str(r.get("symbol", "")).strip()
            try:
                shares = int(float(r.get("shares", 0)))
                cost_price = float(r.get("cost_price", 0))
                note = str(r.get("note", "")).strip()
                if not symbol:
                    raise ValueError("代码为空")
                if shares <= 0 or cost_price <= 0:
                    raise ValueError("股数/成本价必须大于 0")
                exists = db.execute(
                    select(Holding).where(Holding.symbol == symbol)
                ).scalar_one_or_none()
                if exists is None:
                    db.add(
                        Holding(symbol=symbol, shares=shares, cost_price=cost_price, note=note)
                    )
                    added += 1
                else:
                    exists.shares = shares
                    exists.cost_price = cost_price
                    if note:
                        exists.note = note
                    updated += 1
            except Exception as e:  # noqa: BLE001 - 单行容错
                failed.append({"symbol": symbol or "(空)", "error": str(e)})
        db.commit()
    return {"added": added, "updated": updated, "failed": failed}


def enrich(items: list[dict], quotes: dict[str, dict], total_capital: float = 0.0) -> dict:
    """把实时报价叠到持仓清单上，输出明细 + 组合总览。

    每条明细补充：name / price / pct_change / market_value /
    pnl（浮动盈亏额）/ pnl_pct（浮动盈亏比例）/ day_pnl（今日盈亏额）/ weight（仓位占比）。
    报价缺失（停牌/数据源故障）时该条市值按成本计，盈亏字段置 None。

    total_capital>0 时额外算出账户层指标：现金 = 总资金 - 总市值、
    仓位占比 = 总市值 / 总资金、每条持仓相对总资金的占比（cap_weight）；
    =0（未设置）则这些字段为 None，前端引导用户去填。
    """
    total_value = 0.0  # 总市值
    total_cost = 0.0  # 总成本
    total_day_pnl = 0.0  # 今日盈亏（Σ 股数 ×（现价-昨收））

    for it in items:
        q = quotes.get(it["symbol"])
        cost = it["shares"] * it["cost_price"]
        total_cost += cost
        if q and q.get("price"):
            price = float(q["price"])
            value = it["shares"] * price
            prev = float(q.get("prev_close") or price)
            it["name"] = q.get("name", it["symbol"])
            it["price"] = price
            it["pct_change"] = float(q.get("pct_change") or 0)
            it["market_value"] = round(value, 2)
            it["pnl"] = round(value - cost, 2)
            cost_price = it["cost_price"]
            it["pnl_pct"] = round((price / cost_price - 1) * 100, 2) if cost_price else 0.0
            it["day_pnl"] = round(it["shares"] * (price - prev), 2)
            total_value += value
            total_day_pnl += it["day_pnl"]
        else:
            # 无报价：市值按成本计入总览（避免总市值骤降误导），盈亏未知
            it["name"] = it.get("name") or it["symbol"]
            it["price"] = None
            it["pct_change"] = None
            it["market_value"] = round(cost, 2)
            it["pnl"] = None
            it["pnl_pct"] = None
            it["day_pnl"] = None
            total_value += cost

    # 仓位占比（按持仓市值），全部算完才能除
    has_capital = total_capital and total_capital > 0
    for it in items:
        it["weight"] = round(it["market_value"] / total_value * 100, 1) if total_value > 0 else 0.0
        # 相对账户总资金的占比（衡量真实集中度，未设置总资金时为 None）
        it["cap_weight"] = (
            round(it["market_value"] / total_capital * 100, 1) if has_capital else None
        )

    total_pnl = total_value - total_cost
    # 账户层：现金与仓位（总资金未设置时为 None，前端引导填写）
    cash = round(total_capital - total_value, 2) if has_capital else None
    invested_ratio = round(total_value / total_capital * 100, 1) if has_capital else None
    return {
        "items": items,
        "overview": {
            "count": len(items),
            "total_value": round(total_value, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0.0,
            "day_pnl": round(total_day_pnl, 2),
            "total_capital": round(float(total_capital), 2) if has_capital else None,
            "cash": cash,
            "invested_ratio": invested_ratio,
        },
    }


def position_context(symbol: str, quote: dict | None, total_capital: float = 0.0) -> str:
    """生成某只持仓的 AI 诊断上下文（注入诊股工作流的首席决策阶段）。

    返回空串表示该股没有持仓记录（普通诊股，不注入）。
    total_capital>0 时附加「该持仓占总资金比例 + 账户整体仓位/现金」，
    让 AI 结合集中度与可用现金给出加仓空间是否充足等判断。
    """
    with create_session() as db:
        item = db.execute(select(Holding).where(Holding.symbol == symbol)).scalar_one_or_none()
        if item is None:
            return ""
        lines = [
            f"用户当前持有该股 {item.shares} 股，摊薄成本价 {item.cost_price:.2f} 元。",
        ]
        market_value = 0.0
        if quote and quote.get("price"):
            price = float(quote["price"])
            market_value = item.shares * price
            pnl_pct = (price / item.cost_price - 1) * 100 if item.cost_price else 0
            state = "浮盈" if pnl_pct >= 0 else "浮亏"
            lines.append(f"按现价 {price:.2f} 元计算，{state} {abs(pnl_pct):.1f}%。")
        else:
            market_value = item.shares * item.cost_price
        # 账户层资金背景：让 AI 判断单票集中度与加仓的现金约束
        if total_capital and total_capital > 0:
            cap_weight = market_value / total_capital * 100
            total_mv = _total_market_value(db, quote, symbol, market_value)
            invested = total_mv / total_capital * 100
            cash_pct = max(0.0, 100 - invested)
            lines.append(
                f"账户总资金约 {total_capital / 1e4:.1f} 万元，该持仓约占总资金 {cap_weight:.1f}%；"
                f"账户整体仓位约 {invested:.0f}%（可用现金约 {cash_pct:.0f}%）。"
                "请据此评估单票集中度风险与「补」的现金空间是否充足。"
            )
        if item.note:
            lines.append(f"用户备注：{item.note}")
        lines.append(
            "请站在该持仓者的立场做决策：评级与 summary 必须明确对应"
            "「割（止损/清仓）/ 守（持有观察）/ 补（加仓摊低）」三选一的操作建议，"
            "并结合其成本价给出具体的执行价位与条件。"
        )
        return "\n".join(lines)


def _total_market_value(db, quote: dict | None, symbol: str, this_mv: float) -> float:
    """估算账户全部持仓的总市值（用于算账户整体仓位）。

    其他持仓没有实时报价时按成本价估值——只为给 AI 一个仓位量级，
    无需精确；当前票用已算好的 this_mv（含实时价）。
    """
    total = 0.0
    for h in db.execute(select(Holding)).scalars().all():
        if h.symbol == symbol:
            total += this_mv
        else:
            total += h.shares * h.cost_price
    return total
