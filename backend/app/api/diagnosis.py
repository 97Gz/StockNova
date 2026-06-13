"""AI 诊股与提示词管理 API（M6）。

- POST /ai/diagnosis/{symbol}     发起多角色诊股工作流（后台执行，WS 推进度）
- GET  /ai/diagnosis/run/{id}     查询一次诊股的完整状态（轮询备用通道）
- GET  /ai/diagnosis/run/{id}/export  导出 Markdown 报告（下载存档）
- GET  /ai/diagnosis/latest/{symbol}  某股最近一次诊股
- GET  /ai/diagnosis/history      诊股历史列表
- GET  /ai/prompts                全部提示词（默认+自定义）
- PUT  /ai/prompts/{id}           保存自定义提示词
- DELETE /ai/prompts/{id}         重置为默认
"""

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.exceptions import BizError, ok
from app.services import prompt_service

router = APIRouter(prefix="/ai", tags=["ai"])


# ---------------- 多角色诊股 ----------------


@router.post("/diagnosis/{symbol}")
async def start_diagnosis(
    symbol: str, request: Request, mode: str = "deep", as_of: str = ""
) -> dict:
    """发起一次多角色 AI 诊股（进度走 WS type="diagnosis"）。

    mode：deep=完整专业版工作流（全角色，约 1~3 分钟）；
          quick=快速模式（核心4分析师+组合经理，秒级出结论）。
    as_of（YYYY-MM-DD）非空 = 回溯模式：以该历史交易日为"今天"做诊断，
          数据严格截断到该日，用于检验 AI 当时的判断 vs 后续真实走势。
    """
    as_of = as_of.strip() or None
    if as_of:
        as_of = _validate_as_of(request, symbol, as_of)
    return ok(request.app.state.diagnosis_service.start(symbol, mode=mode, as_of=as_of))


def _validate_as_of(request: Request, symbol: str, as_of: str) -> str:
    """校验回溯日期：格式合法、该股在该日有数据、且早于最新交易日（以便有后续走势可比对）。"""
    from datetime import date

    try:
        date.fromisoformat(as_of)
    except ValueError:
        raise BizError(40001, "回溯日期格式应为 YYYY-MM-DD") from None
    store = request.app.state.market_store
    latest = store.latest_bar_date(symbol)
    if latest is None:
        raise BizError(40404, f"股票 {symbol} 暂无行情数据，无法回溯", http_status=404)
    if as_of >= latest:
        raise BizError(40001, f"回溯日期需早于最新交易日（{latest}），这样才有后续走势可校验")
    bars = store.query_daily_bars(symbol, limit=30, as_of=as_of)
    if len(bars) < 20:
        raise BizError(40001, "该日期之前的历史数据不足（少于20根日K），请选更晚的日期")
    return as_of


@router.get("/diagnosis/run/{run_id}/verify")
async def verify_diagnosis(run_id: int, request: Request) -> dict:
    """回测校验：用 as_of 之后的真实走势检验该次回溯诊断（仅回溯诊断有效）。"""
    data = request.app.state.diagnosis_service.verify_run(run_id)
    if data is None:
        raise BizError(40001, "该诊断不是回溯模式，或记录不存在，无法校验")
    return ok(data)


@router.get("/diagnosis/run/{run_id}")
async def get_diagnosis_run(run_id: int, request: Request) -> dict:
    """一次诊股的完整状态与各阶段输出。"""
    data = request.app.state.diagnosis_service.get_run(run_id)
    if data is None:
        raise BizError(40400, f"诊股记录 {run_id} 不存在")
    return ok(data)


@router.get("/diagnosis/run/{run_id}/export")
async def export_diagnosis(run_id: int, request: Request) -> Response:
    """导出一次诊股的 Markdown 报告（浏览器直接触发下载）。"""
    service = request.app.state.diagnosis_service
    run = service.get_run(run_id)
    if run is None:
        raise BizError(40400, f"诊股记录 {run_id} 不存在")
    if run["status"] != "done":
        raise BizError(40001, "该诊断尚未完成，无法导出")
    md = service.to_markdown(run)
    day = str(run.get("created_at", ""))[:10]
    filename = f"AI诊股_{run['name']}_{run['symbol']}_{day}.md"
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        # RFC 5987 编码中文文件名，浏览器才能正确还原
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/diagnosis/latest/{symbol}")
async def get_latest_diagnosis(symbol: str, request: Request) -> dict:
    """某股最近一次诊股（没有返回 null，前端据此显示"开始诊断"）。"""
    return ok(request.app.state.diagnosis_service.latest_of(symbol))


@router.get("/diagnosis/history")
async def diagnosis_history(request: Request, symbol: str = "", limit: int = 20) -> dict:
    """诊股历史列表（可按股票过滤）。"""
    return ok(request.app.state.diagnosis_service.history(symbol or None, limit))


@router.get("/diagnosis/library")
async def diagnosis_library(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    symbol: str = "",
    status: str = "",
) -> dict:
    """AI 研报库：分页 + 按股票/状态过滤的诊股归档列表。"""
    return ok(
        request.app.state.diagnosis_service.history_paged(
            page=page, page_size=page_size, symbol=symbol or None, status=status or None
        )
    )


# ---------------- 定时 AI 研报 ----------------


@router.post("/reports/run")
async def run_report(request: Request) -> dict:
    """手动触发一次定时研报（无视启用开关，立即对自选+持仓批量出诊并按需推送）。

    后台执行，立即返回；进度与结果可在研报库查看。
    """
    import asyncio

    service = request.app.state.report_service
    asyncio.create_task(service.run_scheduled(reason="manual", force=True))
    return ok({"started": True})


@router.get("/reports/last")
async def last_report(request: Request) -> dict:
    """最近一次定时研报的运行摘要（次数/时间/推送结果）。"""
    return ok(request.app.state.report_service.last_run)


# ---------------- 提示词管理 ----------------


class PromptBody(BaseModel):
    template: str


@router.get("/prompts")
async def list_prompts() -> dict:
    """全部提示词：默认模板 + 用户自定义版本 + 占位符说明。"""
    return ok(prompt_service.list_prompts())


@router.put("/prompts/{prompt_id}")
async def save_prompt(prompt_id: str, body: PromptBody) -> dict:
    """保存自定义提示词（立即生效，下次 AI 调用即使用新模板）。"""
    prompt_service.save_prompt(prompt_id, body.template)
    return ok({"saved": True})


@router.delete("/prompts/{prompt_id}")
async def reset_prompt(prompt_id: str) -> dict:
    """恢复默认提示词。"""
    prompt_service.reset_prompt(prompt_id)
    return ok({"reset": True})
