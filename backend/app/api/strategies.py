"""策略接口（M3）：策略广场列表 / 因子注册表 / 扫描执行 / 跑批 / 自定义策略 CRUD。"""

import json
import logging

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel, Field

from app.core.database import create_session
from app.core.exceptions import BizError, ok
from app.models.orm import CustomStrategy
from app.strategy.builtin import STRATEGIES, get_strategy
from app.strategy.engine import ConditionError, evaluate
from app.strategy.factors import FACTOR_META

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("")
def list_strategies() -> dict:
    """策略广场卡片墙数据（不含条件树细节，讲解全量给出）。"""
    items = [
        {
            "id": s["id"],
            "name": s["name"],
            "tech_name": s["tech_name"],
            "category": s["category"],
            "period": s["period"],
            "risk": s["risk"],
            "summary": s["summary"],
            "explain": s["explain"],
            "available": s.get("available", True),
            "unavailable_reason": s.get("unavailable_reason", ""),
            # 板块类特殊策略依赖盘面情绪，选股可用但不支持机械历史回测
            "special": bool(s.get("special")),
        }
        for s in STRATEGIES
    ]
    return ok(items)


@router.get("/factors")
def list_factors() -> dict:
    """因子注册表：自定义策略构建器的下拉数据源。"""
    items = [
        {
            "name": name,
            "label": meta.get("label", name),
            "kind": meta.get("kind", "number"),
            "unit": meta.get("unit", ""),
            "desc": meta.get("desc", ""),
        }
        for name, meta in FACTOR_META.items()
    ]
    return ok(items)


class RunRequest(BaseModel):
    """扫描请求：内置策略多选 + 可选自定义条件树。"""

    strategy_ids: list[str] = Field(default_factory=list)
    custom_condition: dict | None = None
    require_all: bool = False
    limit: int = Field(100, ge=1, le=300)


@router.post("/run")
async def run_strategies(request: Request, body: RunRequest) -> dict:
    """执行选股扫描（共振）。CPU 密集部分在线程池里跑，不阻塞事件循环。"""
    if not body.strategy_ids and body.custom_condition is None:
        raise BizError(40020, "至少选择一个策略或提供自定义条件")
    for sid in body.strategy_ids:
        spec = get_strategy(sid)
        if spec is None:
            raise BizError(40021, f"未知策略: {sid}", http_status=404)
        if not spec.get("available", True):
            raise BizError(40022, f"策略「{spec['name']}」暂未上线: {spec['unavailable_reason']}")

    import asyncio

    svc = request.app.state.strategy_service
    try:
        result = await asyncio.to_thread(
            svc.run,
            body.strategy_ids,
            custom_condition=body.custom_condition,
            require_all=body.require_all,
            limit=body.limit,
        )
    except ConditionError as e:
        raise BizError(40023, f"条件树不合法: {e}") from e
    return ok(result)


@router.post("/batch")
async def run_batch(request: Request) -> dict:
    """手动触发一次全策略跑批存档（定时任务也调用同一入口）。"""
    import asyncio

    result = await asyncio.to_thread(request.app.state.strategy_service.run_daily_batch)
    return ok(result)


@router.get("/today-signals")
def today_signals(
    request: Request,
    top: int = 8,
    strategies: str = "",
) -> dict:
    """最新跑批的共振排行。

    - top: 返回前 N 名（首页卡用 8，选股结果页用 100）
    - strategies: 逗号分隔的策略 id，只看选中策略的组合命中（空 = 全部）
    """
    ids = [s for s in strategies.split(",") if s.strip()] or None
    return ok(request.app.state.strategy_service.today_signals_summary(top=top, strategy_ids=ids))


# ---------------- 自定义策略 CRUD ----------------


def _validate_condition(condition: dict) -> None:
    """保存前做一次"空表求值"校验，把不合法的条件树挡在入库之前。"""
    import pandas as pd

    probe = pd.DataFrame({name: [0.0] for name in FACTOR_META}, index=["000000"])
    try:
        evaluate(condition, probe)
    except ConditionError as e:
        raise BizError(40023, f"条件树不合法: {e}") from e


class AiParseRequest(BaseModel):
    """白话选股描述 → AI 解析为条件树。"""

    text: str = Field(..., min_length=2, max_length=500)


@router.post("/ai-parse")
async def ai_parse_strategy(body: AiParseRequest) -> dict:
    """把用户的白话描述交给 LLM 翻译成条件树（策略广场的 AI 创建入口）。

    返回的 condition 已通过结构校验；AI 无法映射的条件原样列在
    unmatched 里告知用户（诚实优先，不硬凑因子）。
    """
    from app.services import prompt_service
    from app.services.ai_client import AIClient, AINotConfigured

    # 因子清单注入提示词（id/名称/类型/说明，AI 只能用这里列出的因子）
    lines = [
        f"- {name}（{meta.get('label', name)}，{'布尔' if meta.get('kind') == 'bool' else '数值'}"
        f"{('，单位' + meta['unit']) if meta.get('unit') else ''}）：{meta.get('desc', '')}"
        for name, meta in FACTOR_META.items()
    ]
    system = prompt_service.render("strategy_parse", factors="\n".join(lines))

    try:
        data = await AIClient().chat_json(system, body.text, timeout=90)
    except AINotConfigured as e:
        raise BizError(40050, str(e)) from e
    except ValueError as e:
        raise BizError(40052, f"AI 返回内容无法解析：{e}") from e

    if not data.get("ok") or not isinstance(data.get("condition"), dict):
        reason = str(data.get("reason") or "AI 无法将这段描述解析为选股条件，请补充细节")
        raise BizError(40053, reason)

    condition = data["condition"]
    _validate_condition(condition)  # AI 输出也要过同一道结构校验
    return ok(
        {
            "name": str(data.get("name", "AI策略"))[:20],
            "summary": str(data.get("summary", ""))[:60],
            "condition": condition,
            "unmatched": [str(u)[:80] for u in list(data.get("unmatched", []))[:5]],
        }
    )


@router.get("/custom")
def list_custom() -> dict:
    with create_session() as db:
        rows = db.query(CustomStrategy).order_by(CustomStrategy.id.desc()).all()
        items = [
            {
                "id": r.id,
                "name": r.name,
                "condition": json.loads(r.condition_json),
                "created_at": r.created_at.isoformat(sep=" ", timespec="seconds"),
            }
            for r in rows
        ]
    return ok(items)


@router.post("/custom")
def create_custom(
    name: str = Body(..., embed=True, min_length=1, max_length=50),
    condition: dict = Body(..., embed=True),
) -> dict:
    _validate_condition(condition)
    with create_session() as db:
        row = CustomStrategy(name=name, condition_json=json.dumps(condition, ensure_ascii=False))
        db.add(row)
        db.commit()
        return ok({"id": row.id, "name": row.name})


@router.put("/custom/{custom_id}")
def update_custom(
    custom_id: int,
    name: str = Body(..., embed=True, min_length=1, max_length=50),
    condition: dict = Body(..., embed=True),
) -> dict:
    _validate_condition(condition)
    with create_session() as db:
        row = db.get(CustomStrategy, custom_id)
        if row is None:
            raise BizError(40404, f"自定义策略 {custom_id} 不存在", http_status=404)
        row.name = name
        row.condition_json = json.dumps(condition, ensure_ascii=False)
        db.commit()
    return ok()


@router.delete("/custom/{custom_id}")
def delete_custom(custom_id: int) -> dict:
    with create_session() as db:
        row = db.get(CustomStrategy, custom_id)
        if row is None:
            raise BizError(40404, f"自定义策略 {custom_id} 不存在", http_status=404)
        db.delete(row)
        db.commit()
    return ok()
