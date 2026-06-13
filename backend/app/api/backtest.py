"""回测接口（M4）：策略时光机 / 定期调仓 / 历史记录查询。

回测属于重计算（全市场两年因子面板峰值内存约 1~2GB、耗时 5~30 秒），
用全局信号量限制同一时刻只跑一个任务；计算放线程池避免阻塞事件循环。
"""

import asyncio
import logging
import re

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from app.core.exceptions import BizError, ok
from app.services.backtest_service import BacktestError
from app.strategy import engine as cond_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backtest", tags=["backtest"])

# 同时只允许一个回测在跑（内存与 CPU 保护）；非阻塞获取，拿不到直接报"忙"
_busy = asyncio.Semaphore(1)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_date(value: str, label: str) -> str:
    if not _DATE_RE.match(value):
        raise BizError(40030, f"{label} 格式应为 yyyy-MM-dd")
    return value


class SnapshotRequest(BaseModel):
    """策略时光机：历史某天按策略买入，持有 N 天的成绩单。"""

    strategy_ids: list[str] = Field(default_factory=list)
    custom_condition: dict | None = None
    require_all: bool = False
    signal_date: str
    hold_days: list[int] = Field(default_factory=lambda: [5, 10, 20])

    @field_validator("hold_days")
    @classmethod
    def _hold_days_valid(cls, v: list[int]) -> list[int]:
        if not v or any(n < 1 or n > 120 for n in v):
            raise ValueError("hold_days 需为 1~120 之间的整数列表")
        return sorted(set(v))


class RebalanceRequest(BaseModel):
    """定期调仓：start~end 区间内每 freq_days 个交易日换仓一次。"""

    strategy_ids: list[str] = Field(default_factory=list)
    custom_condition: dict | None = None
    require_all: bool = False
    start: str
    end: str
    freq_days: int = Field(5, ge=1, le=60)
    top_n: int = Field(10, ge=1, le=50)
    init_cash: float = Field(100_000, ge=10_000, le=100_000_000)


def _build_pack(strategy_ids: list[str], custom_condition: dict | None, require_all: bool):
    """组装策略包，把业务校验错误统一翻译成 BizError。"""
    from app.services.backtest_service import build_pack

    try:
        return build_pack(strategy_ids, custom_condition, require_all)
    except BacktestError as e:
        raise BizError(40031, str(e)) from e


@router.post("/snapshot")
async def run_snapshot(request: Request, body: SnapshotRequest) -> dict:
    _check_date(body.signal_date, "signal_date")
    pack = _build_pack(body.strategy_ids, body.custom_condition, body.require_all)
    if _busy.locked():
        raise BizError(40032, "已有回测正在运行，请稍后再试")
    svc = request.app.state.backtest_service
    archive = body.model_dump()
    async with _busy:
        try:
            result = await asyncio.to_thread(
                svc.snapshot, pack, body.signal_date, body.hold_days, archive
            )
        except BacktestError as e:
            raise BizError(40031, str(e)) from e
        except cond_engine.ConditionError as e:
            raise BizError(40023, f"条件树不合法: {e}") from e
    return ok(result)


@router.post("/rebalance")
async def run_rebalance(request: Request, body: RebalanceRequest) -> dict:
    _check_date(body.start, "start")
    _check_date(body.end, "end")
    pack = _build_pack(body.strategy_ids, body.custom_condition, body.require_all)
    if _busy.locked():
        raise BizError(40032, "已有回测正在运行，请稍后再试")
    svc = request.app.state.backtest_service
    archive = body.model_dump()

    from app.backtest.engine import RebalanceParams

    params = RebalanceParams(
        start=body.start,
        end=body.end,
        freq_days=body.freq_days,
        top_n=body.top_n,
        init_cash=body.init_cash,
    )
    async with _busy:
        try:
            result = await asyncio.to_thread(svc.rebalance, pack, params, archive)
        except BacktestError as e:
            raise BizError(40031, str(e)) from e
        except cond_engine.ConditionError as e:
            raise BizError(40023, f"条件树不合法: {e}") from e
    return ok(result)


@router.get("/runs")
def list_runs(request: Request, kind: str | None = None, limit: int = 50) -> dict:
    """历史回测记录（摘要列表，按时间倒序）。"""
    if kind not in (None, "snapshot", "rebalance"):
        raise BizError(40030, "kind 仅支持 snapshot / rebalance")
    return ok(request.app.state.backtest_service.list_runs(kind, min(max(limit, 1), 200)))


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: int) -> dict:
    row = request.app.state.backtest_service.get_run(run_id)
    if row is None:
        raise BizError(40404, f"回测记录 {run_id} 不存在", http_status=404)
    return ok(row)


@router.delete("/runs/{run_id}")
def delete_run(request: Request, run_id: int) -> dict:
    if not request.app.state.backtest_service.delete_run(run_id):
        raise BizError(40404, f"回测记录 {run_id} 不存在", http_status=404)
    return ok()
