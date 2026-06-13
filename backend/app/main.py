"""星智股后端入口：装配 FastAPI 应用。

职责（只做装配，不写业务）：
1. 应用生命周期：启动时初始化数据库、装配服务单例、启动调度器与报价轮询
2. 注册全局异常处理器（统一响应包）
3. 挂载各域的路由（统一前缀 /api/v1）+ WebSocket（/ws）

本地开发运行方式（backend 目录下）：
    uv run uvicorn app.main:app --reload --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (
    backtest,
    diagnosis,
    health,
    holdings,
    market,
    news,
    settings,
    strategies,
    tasks,
    watchlist,
    ws,
)
from app.core.database import init_duckdb, init_sqlite
from app.core.exceptions import BizError, biz_error_handler, unhandled_error_handler
from app.core.scheduler import app_scheduler
from app.services import holdings_service, watchlist_service
from app.services.backtest_service import BacktestService
from app.services.calendar_service import CalendarService
from app.services.diagnosis_service import DiagnosisService
from app.services.ext_sync_service import ExtSyncService
from app.services.market_store import MarketStore
from app.services.minute_sync_service import MinuteSyncService
from app.services.news_service import NewsService
from app.services.quote_service import QuoteService
from app.services.report_service import ReportService
from app.services.strategy_service import StrategyService
from app.services.sync_service import SyncService


def _setup_app_logging() -> None:
    """让应用自身的 INFO 日志可见。

    uvicorn 只配置它自己的 logger（uvicorn.access 等），应用模块里
    `logging.getLogger(__name__)` 默认无输出 —— 因子表重建耗时、跑批结果
    这类关键运维信息会被静默丢弃。这里给 `app` 命名空间挂一个简单的
    stderr handler（与 uvicorn 输出汇合到同一个日志文件）。
    """
    app_logger = logging.getLogger("app")
    if app_logger.handlers:  # --reload 等场景重复执行时不叠加 handler
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期钩子：yield 之前是启动逻辑，之后是关闭逻辑。"""
    _setup_app_logging()
    # 1) 数据库就绪（重复执行安全，已存在则跳过）
    init_sqlite()
    init_duckdb()

    # 2) 服务装配：单例挂到 app.state，路由通过 request.app.state 取用
    store = MarketStore()
    calendar = CalendarService(store)
    sync_service = SyncService(store, calendar)
    quote_service = QuoteService(calendar)
    strategy_service = StrategyService(store)
    backtest_service = BacktestService(store)
    ext_sync_service = ExtSyncService(store, calendar)
    minute_sync_service = MinuteSyncService(store, calendar)
    news_service = NewsService()
    diagnosis_service = DiagnosisService(store, strategy_service, news_service)
    report_service = ReportService(diagnosis_service, quote_service)
    app.state.market_store = store
    app.state.sync_service = sync_service
    app.state.quote_service = quote_service
    app.state.strategy_service = strategy_service
    app.state.backtest_service = backtest_service
    app.state.ext_sync_service = ext_sync_service
    app.state.minute_sync_service = minute_sync_service
    app.state.news_service = news_service
    app.state.diagnosis_service = diagnosis_service
    app.state.report_service = report_service
    app.state.scheduler = app_scheduler  # 供 API 触发完整流水线（立即同步今日）

    # 3) 交易日历加载（库里有就秒加载，没有才请求数据源）
    await calendar.ensure_loaded()

    # 4) 后台组件启动：定时任务调度器 + 实时报价轮询
    #    盘中 5 秒轮询标的 = 自选 ∪ 持仓（两类都是用户最关心、需实时跳动的）；
    #    后续增删自选/持仓时由各自 API 同步刷新这个订阅集合。
    quote_service.set_watch_symbols(
        watchlist_service.list_symbols() + holdings_service.list_symbols()
    )
    app_scheduler.setup(
        sync_service,
        strategy_service,
        ext_sync_service,
        minute_sync_service,
        news_service=news_service,
        market_store=store,
        report_service=report_service,
    )
    quote_service.start()

    # 启动补偿：错过定时同步点（如 15:35 没开机）的当天数据，启动后自动补同步。
    # 放后台执行，不阻塞应用启动；内部自带交易日/已同步判断与容错。
    asyncio.create_task(app_scheduler.maybe_catchup_on_startup())

    # 5) 因子表后台预热（首次全市场计算 2~4s，提前算好让首次扫描秒回）。
    #    warmup 内部自带 try/except（失败只记 warning，不影响启动）。
    asyncio.get_running_loop().run_in_executor(None, strategy_service.warmup)

    yield

    # ---- 优雅停机 ----
    app_scheduler.shutdown()
    await quote_service.stop()
    await news_service.close()
    store.close()


app = FastAPI(
    title="星智股 StockNova API",
    version="0.1.0",
    lifespan=lifespan,
)

# 全局异常处理：业务异常与未知异常都转成统一响应包 { code, message, data }
app.add_exception_handler(BizError, biz_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

# 路由挂载：后续里程碑新增的路由都在这里集中注册
API_PREFIX = "/api/v1"
app.include_router(health.router, prefix=API_PREFIX)
app.include_router(settings.router, prefix=API_PREFIX)
app.include_router(tasks.router, prefix=API_PREFIX)
app.include_router(market.router, prefix=API_PREFIX)
app.include_router(watchlist.router, prefix=API_PREFIX)
app.include_router(holdings.router, prefix=API_PREFIX)
app.include_router(strategies.router, prefix=API_PREFIX)
app.include_router(backtest.router, prefix=API_PREFIX)
app.include_router(news.router, prefix=API_PREFIX)
app.include_router(news.ext_router, prefix=API_PREFIX)
app.include_router(diagnosis.router, prefix=API_PREFIX)
app.include_router(ws.router)  # WebSocket 不带 /api/v1 前缀，路径为 /ws


def _mount_frontend() -> None:
    """生产部署时托管已构建的前端（Docker/桌面端单端口同源访问）。

    仅当构建产物存在时挂载（开发期不存在 dist，前端走 vite dev server）。
    /assets 走静态文件；其余非 API 路径回退 index.html（支持前端路由刷新）。
    """
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from app.core.config import config

    dist = config.resolved_static_dir
    index = dist / "index.html"
    if not index.exists():
        return

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """SPA 回退：API/WS 路径交给上面的路由（这里只兜前端页面）。"""
        # 真实存在的静态文件（favicon、sponsor 图片等）直接返回
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index))


_mount_frontend()
