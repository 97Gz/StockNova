"""健康检查接口：前端启动时探测后端是否就绪，也用于 E2E 冒烟。"""

from fastapi import APIRouter

from app.core.exceptions import ok

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict:
    """返回服务状态与版本号。前端代理调通的第一个验证点。"""
    return ok({"status": "up", "app": "StockNova", "version": "0.1.0"})
