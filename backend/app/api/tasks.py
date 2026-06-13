"""数据任务接口：历史初始化 / 每日增量的启动与控制 + 库存统计。

对应设置中心"数据管理"区块：
- 初始化按钮 → POST /tasks/sync/init（断点续传）
- 清库重建   → POST /tasks/sync/init?rebuild=true
- 手动增量   → POST /tasks/sync/daily
- 暂停/恢复/取消 → POST /tasks/sync/pause|resume|cancel
- 进度条     → GET /tasks/sync/status（WS 推送为主，此接口兜底轮询）
"""

import asyncio

from fastapi import APIRouter, Request

from app.core.exceptions import ok

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/sync/status")
def sync_status(request: Request) -> dict:
    """当前同步任务状态与进度快照。"""
    return ok(request.app.state.sync_service.status())


@router.post("/sync/init")
async def start_init(request: Request, rebuild: bool = False) -> dict:
    """启动历史初始化。rebuild=true 先清空行情库（重头再来），否则断点续传。"""
    await request.app.state.sync_service.start_init_history(rebuild=rebuild)
    return ok({"state": "running"})


@router.post("/sync/daily")
async def start_daily(request: Request) -> dict:
    """手动触发每日增量同步（仅日线）。"""
    await request.app.state.sync_service.start_daily_sync()
    return ok({"state": "running"})


@router.post("/sync/today")
def sync_today(request: Request) -> dict:
    """手动触发「立即同步今日」：后台串行跑完整盘后流水线。

    与 /sync/daily 的区别：daily 只补日线；today 跑全套
    （日线→分钟线→扩展数据→策略跑批→盘面摘要→定时研报），
    给"开机时发现今天还没同步"的用户一键补齐当天所有数据。
    """
    request.app.state.scheduler.trigger_full_pipeline_bg()
    return ok({"state": "running"})


@router.post("/sync/pause")
def pause_sync(request: Request) -> dict:
    request.app.state.sync_service.pause()
    return ok()


@router.post("/sync/resume")
def resume_sync(request: Request) -> dict:
    request.app.state.sync_service.resume()
    return ok()


@router.post("/sync/cancel")
def cancel_sync(request: Request) -> dict:
    request.app.state.sync_service.cancel()
    return ok()


@router.get("/sync/logs")
def sync_logs(request: Request, page: int = 1, page_size: int = 10) -> dict:
    """分页同步历史：返回 {items, total, page, page_size}。"""
    return ok(request.app.state.sync_service.logs_paged(page=page, page_size=page_size))


@router.get("/data/stats")
async def data_stats(request: Request) -> dict:
    """行情库库存统计（数据管理页顶部卡片）。"""
    stats = await asyncio.to_thread(request.app.state.market_store.stats)
    return ok(stats)
