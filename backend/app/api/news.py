"""消息中心接口（M5）：快讯流 / 个股新闻 / AI 情绪分析 / 扩展数据同步。

错误码约定（延续既有分配）：
- 40050：AI 未配置（前端跳设置中心引导）
- 40051：AI 调用/解析失败
- 40052：无新闻可分析
"""

import asyncio
import logging
import re

import httpx
from fastapi import APIRouter, Request

from app.core.exceptions import BizError, ok
from app.services.ai_client import AINotConfigured, test_connection
from app.services.news_service import build_market_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news", tags=["news"])

_SYMBOL_RE = re.compile(r"^\d{6}$")

# AI 情绪分析逐只串行（LLM 响应 5~20s，多只并发容易触发服务商限流）
_ai_busy = asyncio.Semaphore(1)


def _check_symbol(symbol: str) -> str:
    if not _SYMBOL_RE.match(symbol):
        raise BizError(40010, "股票代码应为 6 位数字")
    return symbol


@router.get("/feed")
async def news_feed(request: Request, cursor: str = "", column: str = "102") -> dict:
    """7×24 快讯流。cursor 传上一页的 next_cursor 翻更早内容；column: 102=全部 101=重点。"""
    if column not in ("101", "102"):
        raise BizError(40010, "column 仅支持 101（重点）/ 102（全部）")
    svc = request.app.state.news_service
    try:
        return ok(await svc.feed(cursor=cursor, column=column))
    except httpx.HTTPError as e:
        raise BizError(50010, f"快讯源暂时不可用：{type(e).__name__}") from e


@router.get("/stock/{symbol}")
async def stock_news(request: Request, symbol: str) -> dict:
    """个股新闻列表（按时间倒序，最多 20 条）。"""
    _check_symbol(symbol)
    svc = request.app.state.news_service
    try:
        return ok(await svc.stock_news(symbol))
    except httpx.HTTPError as e:
        raise BizError(50010, f"新闻源暂时不可用：{type(e).__name__}") from e


@router.get("/sentiment/{symbol}")
def cached_sentiment(request: Request, symbol: str) -> dict:
    """读当日 AI 情绪缓存；尚未分析过返回 data=null（前端据此显示"开始分析"按钮）。"""
    _check_symbol(symbol)
    return ok(request.app.state.news_service.get_cached_sentiment(symbol))


@router.post("/sentiment/{symbol}")
async def analyze_sentiment(request: Request, symbol: str, name: str = "") -> dict:
    """触发 AI 情绪分析（当日已有缓存直接返回，不重复花 token）。"""
    _check_symbol(symbol)
    svc = request.app.state.news_service
    async with _ai_busy:
        try:
            return ok(await svc.analyze_sentiment(symbol, name))
        except AINotConfigured as e:
            raise BizError(40050, str(e)) from e
        except ValueError as e:
            # 无新闻可分析 / LLM 返回非 JSON
            code = 40052 if "未找到" in str(e) else 40051
            raise BizError(code, str(e)) from e
        except httpx.HTTPStatusError as e:
            raise BizError(
                40051, f"AI 服务返回 {e.response.status_code}，请检查密钥与模型配置"
            ) from e
        except httpx.HTTPError as e:
            raise BizError(40051, f"AI 服务连接失败：{type(e).__name__}") from e


@router.get("/sentiment-batch")
def sentiment_batch(request: Request, symbols: str) -> dict:
    """批量读当日情绪缓存（自选页用）。symbols 逗号分隔，最多 100 只。"""
    syms = [s.strip() for s in symbols.split(",") if s.strip()][:100]
    for s in syms:
        _check_symbol(s)
    return ok(request.app.state.news_service.batch_cached_sentiment(syms))


# ---------------- AI 盘面摘要 ----------------


@router.get("/market-summary")
def cached_market_summary(request: Request) -> dict:
    """读当日 AI 盘面摘要缓存；尚未生成返回 data=null。"""
    return ok(request.app.state.news_service.get_cached_market_summary())


@router.post("/market-summary")
async def generate_market_summary(request: Request) -> dict:
    """生成当日 AI 盘面摘要（已有缓存直接返回）。"""
    svc = request.app.state.news_service
    async with _ai_busy:
        try:
            # 重点快讯标题作为消息面素材（拉取失败不阻断生成）
            titles: list[str] = []
            try:
                feed = await svc.feed(column="101")
                titles = [it["title"] for it in feed["items"][:10]]
            except httpx.HTTPError:
                logger.warning("盘面摘要：快讯拉取失败，仅用盘面统计生成")
            text = build_market_text(request.app.state.market_store, titles)
            return ok(await svc.analyze_market(text, titles))
        except AINotConfigured as e:
            raise BizError(40050, str(e)) from e
        except ValueError as e:
            raise BizError(40051, str(e)) from e
        except httpx.HTTPStatusError as e:
            raise BizError(
                40051, f"AI 服务返回 {e.response.status_code}，请检查密钥与模型配置"
            ) from e
        except httpx.HTTPError as e:
            raise BizError(40051, f"AI 服务连接失败：{type(e).__name__}") from e


@router.post("/ai/test")
async def ai_test(request: Request) -> dict:
    """设置中心"测试 AI 连接"按钮。"""
    try:
        return ok(await test_connection())
    except AINotConfigured as e:
        raise BizError(40050, str(e)) from e
    except httpx.HTTPStatusError as e:
        raise BizError(40051, f"AI 服务返回 {e.response.status_code}，请检查密钥与模型配置") from e
    except httpx.HTTPError as e:
        raise BizError(40051, f"AI 服务连接失败：{type(e).__name__}") from e


# ---------------- 扩展数据同步（资金流/龙虎榜/业绩预告/人气榜） ----------------

ext_router = APIRouter(prefix="/tasks/ext", tags=["tasks"])


@ext_router.post("/sync")
async def trigger_ext_sync(request: Request) -> dict:
    """手动触发扩展数据同步（后台执行，整轮 1~2 分钟，分项容错）。"""
    svc = request.app.state.ext_sync_service
    if svc.running:
        raise BizError(40060, "扩展数据同步正在进行中")
    asyncio.create_task(svc.run())
    return ok({"state": "running"})


@ext_router.get("/status")
async def ext_sync_status(request: Request) -> dict:
    """扩展数据同步状态 + 各表库存（设置中心数据管理区展示）。

    分钟线同步是独立服务，但状态合并在这一个接口里返回（前端一张卡展示）。
    """
    svc = request.app.state.ext_sync_service
    minute = request.app.state.minute_sync_service
    stats = await asyncio.to_thread(request.app.state.market_store.ext_stats)
    return ok(
        {
            "running": svc.running,
            "last_run": svc.last_run,
            "stats": stats,
            "minute": {
                "running": minute.running,
                "progress": minute.progress,
                "last_run": minute.last_run,
            },
        }
    )


@ext_router.post("/minute-sync")
async def trigger_minute_sync(request: Request) -> dict:
    """手动触发当日 5 分钟线同步（后台执行，全市场 2~4 分钟）。"""
    svc = request.app.state.minute_sync_service
    if svc.running:
        raise BizError(40061, "分钟线同步正在进行中")
    asyncio.create_task(svc.run())
    return ok({"state": "running"})
