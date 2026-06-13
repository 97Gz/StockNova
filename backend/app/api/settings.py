"""设置中心接口：配置的读取 / 保存 / 数据源连通测试。"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.core.exceptions import ok
from app.core.scheduler import app_scheduler
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def list_settings(db: Session = Depends(get_db_session)) -> dict:
    """全部配置项（带分组/说明元数据），设置中心据此渲染表单。"""
    return ok(settings_service.get_all(db))


class UpdatePayload(BaseModel):
    """保存请求体：{ "values": { "data.request_delay_ms": 200, ... } }"""

    values: dict[str, Any]


@router.put("")
def update_settings(payload: UpdatePayload, db: Session = Depends(get_db_session)) -> dict:
    """批量保存配置。涉及定时任务的配置保存后立即重排程。"""
    settings_service.update_values(db, payload.values)
    # 同步时间/开关变更 → 调度器即时生效（不用重启应用）
    if any(k.startswith("data.daily_sync") for k in payload.values):
        app_scheduler.reschedule_daily_sync()
    return ok()


@router.post("/test-quote")
async def test_quote_source(request: Request) -> dict:
    """测试当前报价源连通性：拉取上证常见标的，返回原始报价供人工核对。"""
    quotes = await request.app.state.quote_service.snapshot(["600519", "000001"])
    return ok(quotes)


@router.post("/test-notify")
async def test_notify() -> dict:
    """向所有已配置的推送通道发一条测试消息，返回各通道成功/失败结果。"""
    from app.services import notify_service

    results = await notify_service.test_push()
    if not results:
        return ok({"results": [], "message": "未配置任何推送通道"})
    return ok({"results": results})
