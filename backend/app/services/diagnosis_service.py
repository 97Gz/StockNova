"""多角色 AI 诊股工作流（专业版）。

架构（学自 TradingAgents 多智能体框架，结合 A 股量化交易实践重构）：

    数据装配（Python 算好一切数值）
        │
    风险闸门 Risk Gate（Python 硬规则：ST/退市/一字板/流动性 → 降级或否决）
        │
    ┌──────── 7 位并行分析师 ────────┐
    │ 技术面 / 资金面 / 消息面 / 基本面 │
    │ 宏观择时 / 量化 / 板块同业        │
    └───────────────┬───────────────┘
        多头研究员 ⇄ 空头研究员（辩论）→ 研究总监（研究结论）
        │
    交易员 Trader：买入区间/止盈价/止损价 + 量化仓位（波动率×简化凯利×择时系数）
        │
    风控委员会：激进 / 中性 / 保守 三方评审
        │
    组合经理 Portfolio Manager：最终决策（评级/仓位/止损/目标价/操作清单/理论附录）

双模式：
- 深度模式（deep）：全角色、完整辩论与风控委评审，出完整研报。
- 快速模式（quick）：仅核心 4 分析师 + 组合经理，秒级出结论（定时批量/快速决策用）。

设计要点：
- 数据预喂：所有指标数值由因子表/行情库算好后喂给 LLM，LLM 只解读不算术；
- 流式进度：每个阶段开始/思考/完成通过 EventBus 推 WS（type="diagnosis"），
  前端工作流画布实时点亮节点；
- 结构化输出：每个角色输出固定 JSON，解析失败该路降级为中性，不拖垮全局；
- 量化仓位：Python 按波动率 + 择时系数算出建议仓位参考，交给交易员/组合经理微调；
- 理论标注：各分析师输出 theory 字段、组合经理输出 theory_refs，报告末尾附理论附录。
"""

import asyncio
import json
import logging
import statistics
import time
from datetime import datetime

import pandas as pd

from app.core.database import create_session
from app.core.events import event_bus
from app.models.orm import DiagnosisRun
from app.services import prompt_service, settings_service
from app.services.ai_client import AIClient
from app.services.market_store import MarketStore
from app.services.news_service import NewsService
from app.services.strategy_service import StrategyService
from app.strategy.factors import FACTOR_META, compute_factor_table

logger = logging.getLogger(__name__)

# 单角色思考过程的存档上限（思考流可能很长，存档取头部即可满足回看需求）
_THINKING_KEEP = 6000

# 七位分析师：(阶段key, 提示词id, 中文名)
ANALYSTS: list[tuple[str, str, str]] = [
    ("tech", "diag_tech", "技术面分析师"),
    ("fund", "diag_fund", "资金面分析师"),
    ("news", "diag_news", "消息面分析师"),
    ("fundamental", "diag_fundamental", "基本面分析师"),
    ("macro", "diag_macro", "宏观择时分析师"),
    ("quant", "diag_quant", "量化分析师"),
    ("sector", "diag_sector", "板块同业分析师"),
]

# 快速模式只跑核心 4 路（技术/资金/消息/量化），省时省钱
QUICK_ANALYST_KEYS = {"tech", "fund", "news", "quant"}

# 风控委员会三方：(阶段key, 提示词id, 中文名)
RISK_COMMITTEE: list[tuple[str, str, str]] = [
    ("risk_agg", "diag_risk_agg", "激进派"),
    ("risk_neu", "diag_risk_neu", "中性派"),
    ("risk_con", "diag_risk_con", "保守派"),
]


def _fmt(value: object, digits: int = 2) -> str:
    """数值人话化：None/NaN → "无数据"，浮点保留两位。"""
    if value is None:
        return "无数据"
    if isinstance(value, float):
        if pd.isna(value):
            return "无数据"
        return f"{value:.{digits}f}"
    return str(value)


class DiagnosisService:
    """诊股任务管理：发起（后台跑）/ 查询状态 / 历史记录。"""

    def __init__(
        self,
        store: MarketStore,
        strategy_service: StrategyService,
        news_service: NewsService,
    ) -> None:
        self._store = store
        self._strategy = strategy_service
        self._news = news_service
        self._ai = AIClient()
        # 进行中的任务：{run_id: asyncio.Task}；一只股票同时只允许一个任务
        self._tasks: dict[int, asyncio.Task] = {}

    # ---------------- 任务入口 ----------------

    def start(
        self,
        symbol: str,
        user_context: str = "",
        mode: str = "deep",
        as_of: str | None = None,
    ) -> dict:
        """发起一次诊股（后台执行）。同股有进行中任务时直接返回该任务。

        user_context：调用方注入的用户立场上下文（如持仓成本/浮亏比例），
        附加到组合经理输入，让最终建议针对用户处境（持仓的「割/守/补」由此实现）。
        mode：deep=完整工作流；quick=精简（核心4分析师+组合经理）。
        as_of（YYYY-MM-DD）非空 = 回溯模式：以该历史交易日为"今天"做诊断，
        数据严格截断到该日，用于回看 AI 当时的判断是否经得起后续走势检验。
        """
        mode = "quick" if mode == "quick" else "deep"
        with create_session() as db:
            # 回溯模式按 (symbol, as_of) 复用进行中任务；实时模式按 symbol
            running_q = db.query(DiagnosisRun).filter(
                DiagnosisRun.symbol == symbol, DiagnosisRun.status == "running"
            )
            running = running_q.order_by(DiagnosisRun.id.desc()).first()
            # 库里标 running 但任务已不在内存（如后端重启）→ 标失败后重新发起
            if running is not None and not as_of:
                if running.id in self._tasks and not self._tasks[running.id].done():
                    return {"run_id": running.id, "reused": True}
                running.status = "failed"
                running.error = "服务重启导致任务中断"
                db.commit()

            base = self._strategy.name_of(symbol)
            # 回溯记录在名称上标注历史节点，研报库一眼可辨
            name = f"{base} @{as_of} 回溯" if as_of else base
            row = DiagnosisRun(symbol=symbol, name=name, status="running")
            db.add(row)
            db.commit()
            run_id = row.id

        task = asyncio.create_task(
            self._run_workflow(run_id, symbol, user_context, mode, as_of)
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))
        return {"run_id": run_id, "reused": False}

    async def diagnose_now(
        self, symbol: str, mode: str = "quick", user_context: str = ""
    ) -> dict:
        """同步等待版诊股（定时研报/批量场景用）：建记录 → 直接 await 工作流 → 返回结果。

        与 start() 的区别：start() 把工作流丢后台 task 立即返回 run_id（交互场景，
        进度走 WS）；本方法当场跑完再返回完整结果，便于批量编排逐只收集结论。
        """
        mode = "quick" if mode == "quick" else "deep"
        with create_session() as db:
            name = self._strategy.name_of(symbol)
            row = DiagnosisRun(symbol=symbol, name=name, status="running")
            db.add(row)
            db.commit()
            run_id = row.id
        await self._run_workflow(run_id, symbol, user_context, mode)
        return self.get_run(run_id) or {"run_id": run_id, "symbol": symbol, "result": {}}

    def get_run(self, run_id: int) -> dict | None:
        """查询一次诊股的完整状态（轮询备用通道，WS 断了也能拿到结果）。"""
        with create_session() as db:
            row = db.get(DiagnosisRun, run_id)
            return self._row_to_dict(row) if row else None

    def verify_run(self, run_id: int) -> dict | None:
        """回测校验：用 as_of 之后的真实走势检验该次回溯诊断的判断。

        返回基准价、各窗口(+5/10/20/60日)真实涨跌、区间最大涨/跌幅，以及 AI
        给出的目标价/止损价是否被触及（首次触及在第几个交易日）+ 后续日线序列。
        非回溯诊断（无 as_of）返回 None；尚无后续数据时各窗口为 None（前端提示）。
        """
        run = self.get_run(run_id)
        if not run:
            return None
        result = run.get("result") or {}
        as_of = result.get("as_of")
        if not as_of:
            return None  # 非回溯诊断，无可校验对象
        symbol = run["symbol"]
        base = float(result.get("as_of_close") or 0.0)
        if not base:
            bars = self._store.query_daily_bars(symbol, limit=1, as_of=as_of)
            base = float(bars[-1]["close"]) if bars else 0.0
        fwd = self._store.forward_path(symbol, as_of, limit=60)

        # 各持有窗口的真实收益（基准 = as_of 收盘）
        windows: dict[str, float | None] = {}
        for n in (5, 10, 20, 60):
            windows[f"d{n}"] = (
                round((fwd[n - 1]["close"] / base - 1) * 100, 2)
                if base and len(fwd) >= n
                else None
            )
        max_high = max((b["high"] for b in fwd), default=None)
        min_low = min((b["low"] for b in fwd), default=None)
        target = float(result.get("target_price") or 0)
        stop = float(result.get("stop_loss_price") or 0)
        return {
            "as_of": as_of,
            "base_price": round(base, 3),
            "bars": len(fwd),
            "last_date": fwd[-1]["date"] if fwd else as_of,
            "windows": windows,
            "max_gain": round((max_high / base - 1) * 100, 2) if max_high and base else None,
            "max_drop": round((min_low / base - 1) * 100, 2) if min_low and base else None,
            "target_price": target or None,
            "stop_loss_price": stop or None,
            "target_hit_day": _first_touch(fwd, target, "high") if target else None,
            "stop_hit_day": _first_touch(fwd, stop, "low") if stop else None,
            "forward": fwd,
        }

    def latest_of(self, symbol: str) -> dict | None:
        """某股最近一次诊股记录。"""
        with create_session() as db:
            row = (
                db.query(DiagnosisRun)
                .filter(DiagnosisRun.symbol == symbol)
                .order_by(DiagnosisRun.id.desc())
                .first()
            )
            return self._row_to_dict(row) if row else None

    def history(self, symbol: str | None = None, limit: int = 20) -> list[dict]:
        """诊股历史列表（不带阶段明细，列表页轻量展示）。"""
        with create_session() as db:
            q = db.query(DiagnosisRun).order_by(DiagnosisRun.id.desc())
            if symbol:
                q = q.filter(DiagnosisRun.symbol == symbol)
            rows = q.limit(limit).all()
            return [self._row_to_dict(r, with_stages=False) for r in rows]

    def history_paged(
        self,
        page: int = 1,
        page_size: int = 20,
        symbol: str | None = None,
        status: str | None = None,
    ) -> dict:
        """诊股历史分页（研报库页面用）：支持按股票/状态过滤，按时间倒序。"""
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        with create_session() as db:
            q = db.query(DiagnosisRun)
            if symbol:
                q = q.filter(DiagnosisRun.symbol == symbol)
            if status:
                q = q.filter(DiagnosisRun.status == status)
            total = q.count()
            rows = (
                q.order_by(DiagnosisRun.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return {
                "items": [self._row_to_dict(r, with_stages=False) for r in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    def latest_map(self, symbols: list[str]) -> dict[str, dict]:
        """批量取一组股票各自「最近一次已完成」诊断的决策摘要。

        供持仓表内联 AI 字段使用——一次查询拿齐，避免前端逐只请求。
        返回 {symbol: {rating, action, score, position_pct, target_price,
        stop_loss_price, mode, risk_level, run_id, updated_at}}。
        """
        if not symbols:
            return {}
        result: dict[str, dict] = {}
        with create_session() as db:
            # 每只股票按 id 倒序取第一条 done 记录（最近一次完成）
            rows = (
                db.query(DiagnosisRun)
                .filter(DiagnosisRun.symbol.in_(symbols), DiagnosisRun.status == "done")
                .order_by(DiagnosisRun.id.desc())
                .all()
            )
            for r in rows:
                if r.symbol in result:
                    continue  # 已有更近的一条
                data = json.loads(r.result_json or "{}")
                if not data:
                    continue
                result[r.symbol] = {
                    "rating": data.get("rating"),
                    "action": data.get("action") or "",
                    "score": data.get("score"),
                    "position_pct": data.get("position_pct"),
                    "target_price": data.get("target_price") or 0,
                    "stop_loss_price": data.get("stop_loss_price") or 0,
                    "mode": data.get("mode"),
                    "risk_level": data.get("risk_level"),
                    "run_id": r.id,
                    "updated_at": r.created_at.isoformat(sep=" ", timespec="minutes"),
                }
        return result

    @staticmethod
    def _row_to_dict(row: DiagnosisRun, *, with_stages: bool = True) -> dict:
        d = {
            "run_id": row.id,
            "symbol": row.symbol,
            "name": row.name,
            "status": row.status,
            "result": json.loads(row.result_json or "{}"),
            "error": row.error,
            "cost_seconds": row.cost_seconds,
            "model": row.model,
            "created_at": row.created_at.isoformat(sep=" ", timespec="seconds"),
        }
        if with_stages:
            d["stages"] = json.loads(row.stages_json or "{}")
        return d

    # ---------------- 工作流执行 ----------------

    def _emit(
        self, run_id: int, symbol: str, stage: str, status: str, payload: dict | None = None
    ) -> None:
        """向前端推送阶段事件（WS type="diagnosis"）。"""
        event_bus.publish(
            {
                "type": "diagnosis",
                "run_id": run_id,
                "symbol": symbol,
                "stage": stage,
                "status": status,  # running / thinking / done / failed
                "payload": payload or {},
            }
        )

    def _thinking_pusher(self, run_id: int, symbol: str, stage: str):
        """思考流节流推送器：攒一小段再发，避免逐 token 的 WS 消息风暴。

        触发条件：缓冲 ≥ 48 字 或 距上次推送 ≥ 0.35s（满足其一即发）。
        """
        buf: list[str] = []
        last = [0.0]

        async def push(delta: str) -> None:
            buf.append(delta)
            now = time.monotonic()
            if sum(len(s) for s in buf) >= 48 or (now - last[0]) >= 0.35:
                self._emit(run_id, symbol, stage, "thinking", {"delta": "".join(buf)})
                buf.clear()
                last[0] = now

        return push

    async def _run_workflow(
        self,
        run_id: int,
        symbol: str,
        user_context: str = "",
        mode: str = "deep",
        as_of: str | None = None,
    ) -> None:
        """完整工作流编排：数据→风控门→分析师→（辩论→研究总监→交易员→风控委）→组合经理。"""
        started = datetime.now()
        stages: dict[str, dict] = {}
        deep = mode == "deep"
        try:
            # ---- 阶段 0：数据装配 ----
            self._emit(run_id, symbol, "data", "running")
            bundle = await asyncio.to_thread(self._build_bundle, symbol, as_of)
            # 消息面：实时模式拉最新新闻；回溯模式新闻源无历史快照，如实声明并跳过
            if as_of:
                bundle["news_input"] = (
                    f"标的：{bundle['title']}\n"
                    f"（回溯模式：新闻源不提供 {as_of} 当时的历史快照，本次消息面不参与判断，"
                    "请按中性处理并在结论中说明该局限）"
                )
            else:
                bundle["news_input"] = await self._news_text(symbol)
            self._emit(run_id, symbol, "data", "done", {"summary": bundle["data_summary"]})

            # ---- 阶段 0.5：风险闸门（纯 Python 硬规则，秒出） ----
            self._emit(run_id, symbol, "riskgate", "running")
            gate = bundle["risk_gate"]
            stages["riskgate"] = gate
            self._emit(run_id, symbol, "riskgate", "done", gate)

            # ---- 阶段 1：分析师并行（模式决定跑几路） ----
            active = [a for a in ANALYSTS if deep or a[0] in QUICK_ANALYST_KEYS]
            for key, _, label in active:
                self._emit(run_id, symbol, key, "running", {"label": label})

            async def run_analyst(key: str, prompt_id: str, label: str) -> None:
                """单个分析师：流式调用透出思考；失败降级为中性观点（不拖垮全局）。"""
                try:
                    out = await self._ai.chat_json_stream(
                        prompt_service.render(prompt_id),
                        bundle[f"{key}_input"],
                        timeout=180,
                        on_thinking=self._thinking_pusher(run_id, symbol, key),
                    )
                    data = out["data"]
                    report = {
                        "label": label,
                        "score": max(0, min(100, int(data.get("score", 50)))),
                        "stance": str(data.get("stance", "neutral")),
                        "summary": str(data.get("summary", ""))[:120],
                        "points": [str(p)[:100] for p in list(data.get("points", []))[:5]],
                        "theory": str(data.get("theory", ""))[:60],
                        "thinking": out["thinking"][:_THINKING_KEEP],
                    }
                    # 宏观分析师额外携带择时系数（总仓位闸门）
                    if key == "macro":
                        report["timing_coef"] = _clip_float(data.get("timing_coef"), 0.1, 1.3, 1.0)
                except Exception as e:  # noqa: BLE001 - 单路容错边界
                    logger.warning("诊股分析师 %s 失败：%s", key, e)
                    report = {
                        "label": label,
                        "score": 50,
                        "stance": "neutral",
                        "summary": f"分析失败（{type(e).__name__}），按中性处理",
                        "points": [],
                        "failed": True,
                    }
                stages[key] = report
                self._emit(run_id, symbol, key, "done", report)

            await asyncio.gather(*[run_analyst(k, pid, lbl) for k, pid, lbl in active])

            # 择时系数：深度模式取宏观分析师，快速模式用 Python 启发式
            timing_coef = (
                float(stages.get("macro", {}).get("timing_coef", bundle["heuristic_timing"]))
                if deep
                else bundle["heuristic_timing"]
            )
            # 量化仓位参考（波动率 × 择时系数）
            sizing = _quant_sizing(bundle["realized_vol"], timing_coef)
            sizing_line = (
                f"量化仓位参考：{sizing['suggested_pos']}%"
                f"（20日波动率 {sizing['realized_vol']:.1f}%，波动因子 {sizing['vol_factor']:.2f}，"
                f"择时系数 {timing_coef:.2f}）"
            )
            reports_text = self._reports_text(stages, [a[0] for a in active])

            research_summary = ""
            trader_proposal = ""
            if deep:
                # ---- 阶段 2：多空辩论（并行两路） ----
                for side in ("bull", "bear"):
                    self._emit(run_id, symbol, side, "running")

                async def run_debater(side: str, prompt_id: str) -> None:
                    try:
                        out = await self._ai.chat_json_stream(
                            prompt_service.render(prompt_id),
                            f"标的：{bundle['title']}\n\n分析师报告：\n{reports_text}",
                            timeout=180,
                            on_thinking=self._thinking_pusher(run_id, symbol, side),
                        )
                        data = out["data"]
                        speech = {
                            "argument": str(data.get("argument", ""))[:400],
                            "key_points": [
                                str(p)[:100] for p in list(data.get("key_points", []))[:4]
                            ],
                            "thinking": out["thinking"][:_THINKING_KEEP],
                        }
                    except Exception as e:  # noqa: BLE001 - 单路容错边界
                        logger.warning("诊股辩手 %s 失败：%s", side, e)
                        speech = {"argument": f"辩论失败（{type(e).__name__}）", "key_points": []}
                    stages[side] = speech
                    self._emit(run_id, symbol, side, "done", speech)

                await asyncio.gather(
                    run_debater("bull", "diag_bull"), run_debater("bear", "diag_bear")
                )

                # ---- 阶段 3：研究总监（裁决辩论） ----
                research_summary = await self._run_research(run_id, symbol, bundle, stages)

                # ---- 阶段 4：交易员（落地交易方案 + 量化仓位） ----
                trader_proposal = await self._run_trader(
                    run_id, symbol, bundle, stages, research_summary, sizing_line
                )

                # ---- 阶段 5：风控委员会（三方并行评审） ----
                await self._run_risk_committee(run_id, symbol, bundle, stages, trader_proposal)

            # ---- 阶段 6：组合经理最终决策 ----
            self._emit(run_id, symbol, "chief", "running")
            chief_input = self._chief_input(
                bundle=bundle,
                stages=stages,
                reports_text=reports_text,
                research_summary=research_summary,
                trader_proposal=trader_proposal,
                sizing_line=sizing_line,
                user_context=user_context,
                deep=deep,
            )
            chief_out = await self._ai.chat_json_stream(
                prompt_service.render("diag_chief"),
                chief_input,
                timeout=240,
                on_thinking=self._thinking_pusher(run_id, symbol, "chief"),
            )
            result = self._normalize_decision(chief_out["data"])
            # 合并交易员价位（交易员给的价位优先于组合经理回填）
            trader = stages.get("trader") or {}
            if trader.get("target_price") and not result["target_price"]:
                result["target_price"] = trader["target_price"]
            if trader.get("stop_loss_price") and not result["stop_loss_price"]:
                result["stop_loss_price"] = trader["stop_loss_price"]
            if trader.get("buy_zone"):
                result["buy_zone"] = trader["buy_zone"]
            # 附加工作流元信息（研报库/持仓字段读取）
            result["mode"] = mode
            result["timing_coef"] = round(timing_coef, 2)
            result["risk_level"] = gate.get("level", "pass")
            result["risk_flags"] = gate.get("flags", [])
            # 回溯模式：记录历史节点 + 诊断时基准价，供回测校验对比后续走势
            if as_of:
                result["as_of"] = as_of
                result["as_of_close"] = _clip_float(bundle.get("close_str"), 0, 1e6, 0.0)
            stages["chief"] = {**result, "thinking": chief_out["thinking"][:_THINKING_KEEP]}
            self._emit(run_id, symbol, "chief", "done", result)

            # ---- 收尾：落库 + 完成事件 ----
            cost = round((datetime.now() - started).total_seconds(), 1)
            with create_session() as db:
                row = db.get(DiagnosisRun, run_id)
                if row is not None:
                    row.status = "done"
                    row.stages_json = json.dumps(stages, ensure_ascii=False)
                    row.result_json = json.dumps(result, ensure_ascii=False)
                    row.cost_seconds = cost
                    row.model = self._model_name()
                    db.commit()
            self._emit(run_id, symbol, "all", "done", {"cost_seconds": cost})
            logger.info(
                "诊股完成：%s run=%d 模式=%s 耗时 %.1fs 评级=%s",
                symbol,
                run_id,
                mode,
                cost,
                result.get("rating"),
            )

        except Exception as e:  # noqa: BLE001 - 工作流总容错边界
            logger.exception("诊股工作流失败：%s run=%d", symbol, run_id)
            with create_session() as db:
                row = db.get(DiagnosisRun, run_id)
                if row is not None:
                    row.status = "failed"
                    row.error = f"{type(e).__name__}: {e}"
                    row.stages_json = json.dumps(stages, ensure_ascii=False)
                    db.commit()
            self._emit(run_id, symbol, "all", "failed", {"error": str(e)})

    # ---------------- 后半程角色 ----------------

    async def _run_research(
        self, run_id: int, symbol: str, bundle: dict, stages: dict
    ) -> str:
        """研究总监：裁决多空辩论，形成统一研究结论（返回结论文本供下游使用）。"""
        self._emit(run_id, symbol, "research", "running")
        bull = stages.get("bull", {})
        bear = stages.get("bear", {})
        bull_pts = "；".join(bull.get("key_points", []))
        bear_pts = "；".join(bear.get("key_points", []))
        text = (
            f"标的：{bundle['title']}\n\n"
            f"多方陈词：{bull.get('argument', '')}\n要点：{bull_pts}\n\n"
            f"空方陈词：{bear.get('argument', '')}\n要点：{bear_pts}"
        )
        try:
            out = await self._ai.chat_json_stream(
                prompt_service.render("diag_research"),
                text,
                timeout=180,
                on_thinking=self._thinking_pusher(run_id, symbol, "research"),
            )
            data = out["data"]
            stage = {
                "stance": str(data.get("stance", "neutral")),
                "conviction": max(0, min(100, int(data.get("conviction", 50)))),
                "summary": str(data.get("summary", ""))[:300],
                "key_points": [str(p)[:100] for p in list(data.get("key_points", []))[:4]],
                "thinking": out["thinking"][:_THINKING_KEEP],
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("研究总监失败：%s", e)
            stage = {
                "stance": "neutral",
                "conviction": 50,
                "summary": "研究汇总失败",
                "key_points": [],
            }
        stages["research"] = stage
        self._emit(run_id, symbol, "research", "done", stage)
        return f"研究结论（置信度 {stage['conviction']}）：{stage['summary']}"

    async def _run_trader(
        self,
        run_id: int,
        symbol: str,
        bundle: dict,
        stages: dict,
        research_summary: str,
        sizing_line: str,
    ) -> str:
        """交易员：把研究结论落地为买入区间/止盈价/止损价 + 仓位（返回方案文本）。"""
        self._emit(run_id, symbol, "trader", "running")
        text = (
            f"标的：{bundle['title']}\n当前价：{bundle['close_str']} 元\n"
            f"{research_summary}\n{sizing_line}\n"
            f"技术要点：{stages.get('tech', {}).get('summary', '')}\n"
            f"风险闸门：{bundle['risk_gate'].get('note', '无')}"
        )
        try:
            out = await self._ai.chat_json_stream(
                prompt_service.render("diag_trader"),
                text,
                timeout=180,
                on_thinking=self._thinking_pusher(run_id, symbol, "trader"),
            )
            data = out["data"]
            stage = {
                "buy_zone": str(data.get("buy_zone", ""))[:60],
                "target_price": _clip_float(data.get("target_price"), 0, 1e6, 0.0),
                "stop_loss_price": _clip_float(data.get("stop_loss_price"), 0, 1e6, 0.0),
                "position_pct": max(0, min(100, int(data.get("position_pct", 0)))),
                "horizon": str(data.get("horizon", ""))[:20],
                "summary": str(data.get("summary", ""))[:200],
                "theory": str(data.get("theory", ""))[:60],
                "thinking": out["thinking"][:_THINKING_KEEP],
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("交易员失败：%s", e)
            stage = {
                "buy_zone": "",
                "target_price": 0.0,
                "stop_loss_price": 0.0,
                "summary": "交易方案失败",
            }
        stages["trader"] = stage
        self._emit(run_id, symbol, "trader", "done", stage)
        return (
            f"交易方案：买入区间 {stage.get('buy_zone', '—')}，"
            f"目标价 {stage.get('target_price', 0)}，止损价 {stage.get('stop_loss_price', 0)}，"
            f"建议仓位 {stage.get('position_pct', 0)}%"
        )

    async def _run_risk_committee(
        self, run_id: int, symbol: str, bundle: dict, stages: dict, trader_proposal: str
    ) -> None:
        """风控委员会：激进/中性/保守三方并行评审交易员方案。"""
        for key, _, label in RISK_COMMITTEE:
            self._emit(run_id, symbol, key, "running", {"label": label})

        async def run_member(key: str, prompt_id: str, label: str) -> None:
            text = (
                f"标的：{bundle['title']}\n{trader_proposal}\n"
                f"风险闸门：{bundle['risk_gate'].get('note', '无')}"
            )
            try:
                out = await self._ai.chat_json_stream(
                    prompt_service.render(prompt_id),
                    text,
                    timeout=150,
                    on_thinking=self._thinking_pusher(run_id, symbol, key),
                )
                data = out["data"]
                stage = {
                    "label": label,
                    "stance": str(data.get("stance", "维持"))[:10],
                    "position_adjust": int(_clip_float(data.get("position_adjust"), -100, 100, 0)),
                    "summary": str(data.get("summary", ""))[:120],
                    "thinking": out["thinking"][:_THINKING_KEEP],
                }
            except Exception as e:  # noqa: BLE001
                logger.warning("风控委员 %s 失败：%s", key, e)
                stage = {
                    "label": label,
                    "stance": "维持",
                    "position_adjust": 0,
                    "summary": "评审失败",
                }
            stages[key] = stage
            self._emit(run_id, symbol, key, "done", stage)

        await asyncio.gather(*[run_member(k, pid, lbl) for k, pid, lbl in RISK_COMMITTEE])

    def _chief_input(
        self,
        *,
        bundle: dict,
        stages: dict,
        reports_text: str,
        research_summary: str,
        trader_proposal: str,
        sizing_line: str,
        user_context: str,
        deep: bool,
    ) -> str:
        """组装组合经理输入：按模式拼接可用的上游产出。"""
        parts = [f"标的：{bundle['title']}", f"当前价：{bundle['close_str']} 元", sizing_line]
        gate = bundle["risk_gate"]
        parts.append(f"风险闸门：[{gate.get('level')}] {gate.get('note', '无')}")
        parts.append(f"\n分析师报告：\n{reports_text}")
        if deep:
            if research_summary:
                parts.append(f"\n{research_summary}")
            if trader_proposal:
                parts.append(f"\n{trader_proposal}")
            # 风控委三方意见
            rc_lines = []
            for key, _, label in RISK_COMMITTEE:
                s = stages.get(key, {})
                if s:
                    rc_lines.append(
                        f"{label}：{s.get('stance', '')}（仓位调整 {s.get('position_adjust', 0)}）"
                        f" {s.get('summary', '')}"
                    )
            if rc_lines:
                parts.append("\n风控委员会意见：\n" + "\n".join(rc_lines))
        if user_context:
            parts.append(f"\n【用户持仓情况】\n{user_context}")
        return "\n".join(parts)

    # ---------------- 报告导出 ----------------

    @staticmethod
    def to_markdown(run: dict) -> str:
        """把一次诊股的完整结果渲染成 Markdown 报告（下载存档用）。"""
        stages: dict = run.get("stages") or {}
        result: dict = run.get("result") or {}
        stance_cn = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
        mode_cn = {"deep": "深度模式", "quick": "快速模式"}

        # 决策关键数值（提取局部变量，便于拼接且避免单行过长）
        score = result.get("score", "—")
        conf = result.get("confidence", "—")
        pos = result.get("position_pct", "—")
        timing = result.get("timing_coef", "—")
        tp = result.get("target_price") or "—"
        sl = result.get("stop_loss_price") or "—"
        meta = (
            f"> 生成时间：{run.get('created_at', '')} ｜ 模型：{run.get('model', '')} ｜ "
            f"耗时：{run.get('cost_seconds', '')}s ｜ {mode_cn.get(result.get('mode'), '')}"
        )

        lines: list[str] = [
            f"# AI 诊股报告：{run.get('name', '')}（{run.get('symbol', '')}）",
            "",
            meta,
            "> 本报告由多角色 AI 工作流生成，仅为多角度推演分析，不构成投资建议。",
            "",
            "## 最终决策（组合经理）",
            "",
            f"- **评级**：{result.get('rating', '—')}"
            + (f" ｜ 持仓建议：{result.get('action')}" if result.get("action") else ""),
            f"- **综合评分**：{score} / 100 ｜ 置信度：{conf}",
            f"- **建议仓位**：{pos}%（择时系数 {timing}）",
            f"- **目标价/止损价**：{tp} / {sl} 元",
            f"- **操作周期**：{result.get('horizon', '—')} ｜ "
            f"买卖点：{result.get('entry_note', '—')}",
            f"- **止损纪律**：跌破入场价 {result.get('stop_loss_pct', '—')}%",
            "",
            f"**决策陈述**：{result.get('summary', '')}",
            "",
        ]
        gate = stages.get("riskgate") or {}
        if gate.get("flags"):
            lines += [f"> 风险闸门[{gate.get('level')}]：{'、'.join(gate['flags'])}", ""]
        if result.get("checklist"):
            lines.append("**操作清单**：")
            lines.extend(f"- [ ] {c}" for c in result["checklist"])
            lines.append("")
        if result.get("reasons"):
            lines.append("**决策依据**：")
            lines.extend(f"1. {r}" for r in result["reasons"])
            lines.append("")
        if result.get("risks"):
            lines.append("**风险盯防**：")
            lines.extend(f"- {r}" for r in result["risks"])
            lines.append("")

        lines.append("## 分析师报告")
        for key, _, label in ANALYSTS:
            r = stages.get(key)
            if not r:
                continue
            stance_text = stance_cn.get(r.get("stance"), "中性")
            lines += [
                "",
                f"### {label}",
                "",
                f"- 评分：{r.get('score', '—')} / 100 ｜ 立场：{stance_text}"
                + (f" ｜ 依据：{r.get('theory')}" if r.get("theory") else ""),
                f"- 结论：{r.get('summary', '')}",
            ]
            if r.get("points"):
                lines.append("- 论据：")
                lines.extend(f"  - {p}" for p in r["points"])

        # 多空辩论 + 研究总监 + 交易员 + 风控委（深度模式才有）
        if stages.get("bull") or stages.get("bear"):
            lines.append("")
            lines.append("## 多空辩论")
            for side, title in (("bull", "多方陈词"), ("bear", "空方陈词")):
                s = stages.get(side) or {}
                if s:
                    lines += ["", f"### {title}", "", s.get("argument", "")]
                    lines.extend(f"- {p}" for p in s.get("key_points", []))
        if stages.get("research"):
            s = stages["research"]
            lines += ["", "## 研究总监结论", "", s.get("summary", "")]
            lines.extend(f"- {p}" for p in s.get("key_points", []))
        if stages.get("trader"):
            s = stages["trader"]
            lines += [
                "",
                "## 交易员方案",
                "",
                f"- 买入区间：{s.get('buy_zone', '—')}",
                f"- 目标价：{s.get('target_price', '—')} 元 ｜ "
                f"止损价：{s.get('stop_loss_price', '—')} 元",
                f"- 建议仓位：{s.get('position_pct', '—')}% ｜ 周期：{s.get('horizon', '—')}",
                f"- {s.get('summary', '')}",
            ]
        rc = [stages.get(k) for k, _, _ in RISK_COMMITTEE]
        if any(rc):
            lines += ["", "## 风控委员会"]
            for key, _, label in RISK_COMMITTEE:
                s = stages.get(key) or {}
                if s:
                    lines.append(
                        f"- **{label}**：{s.get('stance', '')}"
                        f"（仓位调整 {s.get('position_adjust', 0)}）{s.get('summary', '')}"
                    )

        if result.get("theory_refs"):
            lines += ["", "## 本次引用的理论框架", ""]
            lines.extend(f"- {t}" for t in result["theory_refs"])

        return "\n".join(lines)

    @staticmethod
    def _normalize_decision(raw: dict) -> dict:
        """组合经理输出的字段清洗：限定取值范围，缺失给安全默认。"""
        rating = str(raw.get("rating", "持有"))
        if rating not in ("强烈买入", "买入", "持有", "减仓", "卖出"):
            rating = "持有"
        action = str(raw.get("action", ""))
        if action not in ("割", "守", "补", ""):
            action = ""
        return {
            "rating": rating,
            "action": action,
            "score": max(0, min(100, int(raw.get("score", 50)))),
            "confidence": max(0, min(100, int(raw.get("confidence", 50)))),
            "position_pct": max(0, min(100, int(raw.get("position_pct", 0)))),
            "horizon": str(raw.get("horizon", ""))[:20],
            "entry_note": str(raw.get("entry_note", ""))[:80],
            "stop_loss_pct": float(raw.get("stop_loss_pct", 8) or 8),
            "target_price": _clip_float(raw.get("target_price"), 0, 1e6, 0.0),
            "stop_loss_price": _clip_float(raw.get("stop_loss_price"), 0, 1e6, 0.0),
            "buy_zone": str(raw.get("buy_zone", ""))[:60],
            "summary": str(raw.get("summary", ""))[:300],
            "reasons": [str(r)[:120] for r in list(raw.get("reasons", []))[:5]],
            "risks": [str(r)[:120] for r in list(raw.get("risks", []))[:5]],
            "checklist": [str(c)[:100] for c in list(raw.get("checklist", []))[:6]],
            "theory_refs": [str(t)[:60] for t in list(raw.get("theory_refs", []))[:6]],
        }

    @staticmethod
    def _model_name() -> str:
        with create_session() as db:
            return str(settings_service.get_value(db, "ai.model"))

    @staticmethod
    def _reports_text(stages: dict, keys: list[str]) -> str:
        """分析师报告 → 辩论/决策阶段的输入文本（只拼实际跑了的角色）。"""
        stance_cn = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
        label_map = {a[0]: a[2] for a in ANALYSTS}
        parts = []
        for key in keys:
            r = stages.get(key)
            if not r:
                continue
            points = "；".join(r.get("points", []))
            stance_text = stance_cn.get(r.get("stance"), "中性")
            parts.append(
                f"【{label_map.get(key, key)}】评分 {r.get('score', 50)}，立场：{stance_text}\n"
                f"结论：{r.get('summary', '')}\n论据:{points}"
            )
        return "\n\n".join(parts)

    # ---------------- 数据装配 ----------------

    def _build_bundle(self, symbol: str, as_of: str | None = None) -> dict:
        """组装各分析师输入 + 风险闸门 + 量化原料（数值 Python 算好，LLM 只解读）。

        同步阻塞函数（DuckDB/pandas 操作），调用方用 to_thread 包装。

        as_of（YYYY-MM-DD）非空 = 回溯模式：所有行情/因子/资金面数据严格截断到
        该历史交易日，技术因子用"截断到 as_of 的单股面板"经 compute_factor_table
        复算（无未来泄露）；全市场横截面（量化分位/同业排名）与历史新闻在该模式
        无法无泄露还原，相应分析师改用 as-of 安全变体并在文本中如实声明局限。
        """
        name = self._strategy.name_of(symbol)
        title = f"{name}（{symbol}）@{as_of} 回溯" if as_of else f"{name}（{symbol}）"

        # 因子行：实时模式取全市场因子表的该股一行；回溯模式用截断到 as_of 的
        # 单股面板复算（table 置空，量化/板块改走 as-of 变体，杜绝横截面未来泄露）
        if as_of:
            table = pd.DataFrame()
            row = self._asof_factor_row(symbol, as_of)
        else:
            table = self._strategy.factor_table()
            row = table.loc[symbol] if symbol in table.index else pd.Series(dtype=object)

        def f(key: str, digits: int = 2) -> str:
            return _fmt(row.get(key), digits)

        # 命中的形态/技术布尔因子（让技术分析师一眼看到信号清单）
        hit_patterns = [
            str(FACTOR_META[k]["label"])
            for k, meta in FACTOR_META.items()
            if meta.get("kind") == "bool"
            and k in row.index
            and bool(row.get(k)) is True
            and k not in ("is_st",)
        ]

        # 近 60 根日K（趋势 + 波动率计算原料；展示给技术面只取近 10 根）
        bars = self._store.query_daily_bars(symbol, limit=60, as_of=as_of)
        kline_lines = [
            f"{b['date']} 开{_fmt(b['open'])} 高{_fmt(b['high'])} 低{_fmt(b['low'])} "
            f"收{_fmt(b['close'])} 量{_fmt(b['volume'] / 1e4, 1)}万手"
            for b in bars[-10:]
        ]

        ma_line = (
            f"均线：MA5={f('ma5')} MA10={f('ma10')} MA20={f('ma20')} "
            f"MA60={f('ma60')} 年线={f('ma250')}"
        )
        momentum_line = (
            f"动量：RSI14={f('rsi14')} MACD-DIF={f('macd_dif', 3)} "
            f"MACD柱={f('macd_hist', 3)} KDJ-J={f('kdj_j', 1)}"
        )
        range_line = (
            f"区间涨幅：5日={f('chg_5d')}% 10日={f('chg_10d')}% "
            f"20日={f('chg_20d')}% 60日={f('drawdown_60d')}%"
        )
        tech_input = (
            f"标的：{title}\n"
            f"最新价：{f('close')} 元，今日涨跌：{f('pct_change')}%，换手率：{f('turnover')}%\n"
            f"{ma_line}\n{momentum_line}\n"
            f"量价：量比={f('vol_ratio')} 成交额={f('amount_yi')}亿 20日乖离率={f('bias20')}%\n"
            f"{range_line}\n"
            f"位置：收盘强弱位置={f('close_position', 0)}/100\n"
            f"盘中：尾盘30分钟={f('late30_pct')}% 早盘1小时={f('am60_pct')}% "
            f"站上分时均价={'是' if bool(row.get('above_vwap')) else '否'}\n"
            f"今日命中形态信号：{('、'.join(hit_patterns)) if hit_patterns else '无'}\n"
            f"近10个交易日K线：\n" + "\n".join(kline_lines)
        )

        # 资金面：因子表快照 + 近10日资金流 + 龙虎榜记录（回溯模式同样截断到 as_of）
        flows = self._store.stock_fund_flow(symbol, days=10, as_of=as_of)
        flow_lines = [
            f"{x['date']} 主力净流入{_fmt(x['main_net'] / 1e8)}亿（占比{_fmt(x['main_pct'])}%）"
            for x in flows
        ]
        dts = self._store.stock_dragon_tiger(symbol, limit=5, as_of=as_of)
        dt_lines = [
            f"{x['date']} 净买{_fmt(x['net_amt'] / 1e8)}亿 上榜原因：{x['reason']}" for x in dts
        ]
        fund_input = (
            f"标的：{title}\n"
            f"今日：主力净流入={f('main_net_yi')}亿 净占比={f('main_pct')}% "
            f"换手率={f('turnover')}%\n"
            f"人气榜：名次={f('pop_rank', 0)} 名次变化={f('pop_jump', 0)}\n"
            f"近10日主力资金流：\n" + ("\n".join(flow_lines) if flow_lines else "无数据") + "\n"
            "近期龙虎榜：\n" + ("\n".join(dt_lines) if dt_lines else "近期未上榜")
        )

        # 基本面：估值 + 业绩预告 + 股息
        fundamental_input = (
            f"标的：{title}\n"
            f"估值：PE(TTM)={f('pe_ttm', 1)} PB={f('pb')} 总市值={f('total_mv_yi', 0)}亿\n"
            f"股息率(TTM)：{f('dv_ttm')}%\n"
            f"业绩预告：{'预告向好' if bool(row.get('earn_is_up')) else '无向好预告'}，"
            f"净利变动下限={f('earn_amp_lower', 0)}%\n"
            f"（注：本系统为行情快照数据，未含完整财报三表，请基于现有数据判断并说明局限）"
        )

        # 宏观/量化/板块：回溯模式改用 as-of 安全变体（杜绝全市场横截面未来泄露）
        if as_of:
            macro_input, heuristic_timing = self._macro_input_asof(as_of)
            quant_input = self._quant_input_asof(symbol, row)
            sector_input = self._sector_input_asof(symbol, name)
        else:
            macro_input, heuristic_timing = self._macro_input()
            quant_input = self._quant_input(symbol, table, row)
            sector_input = self._sector_input(symbol, table, row, name)
        risk_gate = self._risk_gate(row, bars)
        realized_vol = _realized_vol(bars)

        asof_note = f"；回溯至 {as_of}（数据已截断，无未来泄露）" if as_of else ""
        data_summary = (
            f"已装配：{len(bars)}根日K、{len(flows)}日资金流、{len(dts)}条龙虎榜、"
            f"{len(hit_patterns)}个命中形态信号；风险闸门[{risk_gate['level']}]{asof_note}"
        )

        return {
            "title": title,
            "as_of": as_of,
            "close_str": f("close"),
            "tech_input": tech_input,
            "fund_input": fund_input,
            "news_input": "",  # 由 _news_text 异步填充
            "fundamental_input": fundamental_input,
            "macro_input": macro_input,
            "quant_input": quant_input,
            "sector_input": sector_input,
            "risk_gate": risk_gate,
            "realized_vol": realized_vol,
            "heuristic_timing": heuristic_timing,
            "data_summary": data_summary,
        }

    def _asof_factor_row(self, symbol: str, as_of: str) -> pd.Series:
        """回溯模式的单股因子行：用截断到 as_of 的单股面板复算全部技术因子。

        复用 compute_factor_table（与实时选股同一套口径），面板止于 as_of 故
        所有滚动/平滑指标天然无未来泄露；基本面取 as_of 当期估值快照，
        扩展因子（资金流/人气/龙虎榜聚合等）无完整历史 → 缺列即"无数据"。
        """
        panel = self._store.query_symbol_panel_asof(symbol, as_of, days=300)
        if panel.empty:
            return pd.Series(dtype=object)
        basics = self._store.basics_one_df(symbol)
        funds = self._store.fundamentals_asof_df(symbol, as_of)
        wide = compute_factor_table(panel, basics, funds)
        return wide.loc[symbol] if symbol in wide.index else pd.Series(dtype=object)

    def _macro_input_asof(self, as_of: str) -> tuple[str, float]:
        """回溯版宏观输入：指数趋势截断到 as_of；全市场涨跌温度无历史快照故从略。"""
        try:
            sh = self._store.query_index_daily("000001", limit=60, as_of=as_of)
            gem = self._store.query_index_daily("399006", limit=60, as_of=as_of)
        except Exception as e:  # noqa: BLE001 - 宏观数据缺失不阻塞诊股
            logger.warning("回溯宏观数据装配失败：%s", e)
            return (f"标的：A股大盘环境（回溯至 {as_of}）\n（宏观数据暂缺，按中性环境判断）", 1.0)

        # 启发式择时：上证站上 MA20 偏多、跌破偏空，叠加近 20 日涨跌幅微调
        heuristic = 1.0
        if sh:
            closes = [b["close"] for b in sh]
            ma20 = sum(closes[-20:]) / min(20, len(closes))
            chg20 = (closes[-1] / closes[-21] - 1) if len(closes) > 20 else 0.0
            above = closes[-1] >= ma20
            heuristic = round(max(0.4, min(1.15, 0.8 + (0.2 if above else -0.2) + chg20)), 2)
        text = (
            f"标的：A股大盘环境（回溯至 {as_of}）\n"
            f"{_index_trend_line('上证指数', sh)}\n{_index_trend_line('创业板指', gem)}\n"
            f"（回溯模式：全市场涨跌家数/涨跌停温度为当前快照、无历史回溯，本次从略；"
            f"系统启发式择时系数参考 {heuristic:.2f}，请据指数趋势独立给出 timing_coef）"
        )
        return text, heuristic

    def _quant_input_asof(self, symbol: str, row: pd.Series) -> str:
        """回溯版量化输入：不做全市场横截面分位（需全量历史快照，本系统未存），
        改给该股自身动量，供量化视角参考。"""

        def f(key: str, digits: int = 2) -> str:
            return _fmt(row.get(key), digits)

        return (
            f"标的：{symbol}\n"
            "（回溯模式：全市场横截面分位/RPS 需要 as_of 当日全量因子快照，"
            "本系统未做全市场历史快照，故不提供跨股分位；以下为该股自身动量）\n"
            f"自身动量：5日涨幅={f('chg_5d')}% 20日涨幅={f('chg_20d')}% "
            f"量比={f('vol_ratio')} 换手率={f('turnover')}% RSI14={f('rsi14')}"
        )

    def _sector_input_asof(self, symbol: str, name: str) -> str:
        """回溯版板块输入：给出当前行业归属，但不做历史同业横截面（避免未来泄露）。"""
        try:
            boards = self._store.boards_of_symbol(symbol, "industry")
        except Exception as e:  # noqa: BLE001
            logger.warning("回溯板块归属查询失败：%s", e)
            boards = []
        if not boards:
            return f"标的：{name}（{symbol}）\n（未查到行业板块归属，请按个股独立逻辑判断）"
        board = boards[0]
        return (
            f"标的：{name}（{symbol}）\n所属行业板块：{board['name']}（{board['code']}）\n"
            "（回溯模式：板块归属为当前结构，历史同业横截面对比不提供，"
            "请以个股自身逻辑为主、板块归属为辅判断）"
        )

    def _macro_input(self) -> tuple[str, float]:
        """宏观择时输入：大盘指数趋势 + 全市场温度；附带 Python 启发式择时系数。"""
        try:
            sh = self._store.query_index_daily("000001", limit=60)
            gem = self._store.query_index_daily("399006", limit=60)
            overview = self._store.eod_market_overview()
        except Exception as e:  # noqa: BLE001 - 宏观数据缺失不阻塞诊股
            logger.warning("宏观数据装配失败：%s", e)
            return ("标的：大盘\n（宏观数据暂缺，请按中性环境判断）", 1.0)

        up = overview.get("up", 0)
        down = overview.get("down", 0)
        limit_up = overview.get("limit_up", 0)
        limit_down = overview.get("limit_down", 0)
        amount_yi = (overview.get("total_amount") or 0) / 1e8
        heuristic = _heuristic_timing(up, down, limit_up, limit_down)
        text = (
            "标的：A股大盘环境\n"
            f"{_index_trend_line('上证指数', sh)}\n{_index_trend_line('创业板指', gem)}\n"
            f"市场温度：涨{up}家/跌{down}家，涨停{limit_up}家、跌停{limit_down}家，"
            f"两市成交{amount_yi:.0f}亿\n"
            f"（系统启发式择时系数参考：{heuristic:.2f}，你需结合趋势独立给出 timing_coef）"
        )
        return text, heuristic

    def _quant_input(self, symbol: str, table: pd.DataFrame, row: pd.Series) -> str:
        """量化输入：该股在全市场的多因子分位与相对强度排名。"""
        if symbol not in table.index or table.empty:
            return f"标的：{symbol}\n（量化因子数据暂缺）"
        # 关键因子的全市场分位（pct rank，越高越突出）
        cols = {
            "chg_20d": "20日涨幅(RPS)",
            "chg_5d": "5日涨幅",
            "vol_ratio": "量比",
            "turnover": "换手率",
            "main_net_yi": "主力净流入",
            "rsi14": "RSI强度",
        }
        lines = []
        for col, label in cols.items():
            pct = _percentile(table, col, symbol)
            if pct is not None:
                lines.append(f"{label}：全市场分位 {pct:.0f}%")
        rps = _percentile(table, "chg_20d", symbol)
        rps_line = f"相对强度RPS≈{rps:.0f}（>87 为强势股池）" if rps is not None else "RPS 无数据"
        total = len(table.index)
        return (
            f"标的：{symbol}\n全市场样本数：{total}\n{rps_line}\n"
            "各因子全市场分位：\n" + ("\n".join(lines) if lines else "无数据")
        )

    def _sector_input(
        self, symbol: str, table: pd.DataFrame, row: pd.Series, name: str
    ) -> str:
        """板块同业输入：所属行业板块强弱 + 个股在同业中的龙头地位。"""
        try:
            boards = self._store.boards_of_symbol(symbol, "industry")
        except Exception as e:  # noqa: BLE001
            logger.warning("板块归属查询失败：%s", e)
            boards = []
        if not boards:
            return f"标的：{name}（{symbol}）\n（未查到行业板块归属，请按个股独立逻辑判断）"

        board = boards[0]
        lines = [f"标的：{name}（{symbol}）", f"所属行业板块：{board['name']}（{board['code']}）"]

        # 板块近期表现 + 在全部行业板块中的排名
        try:
            perf = self._store.board_recent_perf("industry", days=10)
            if not perf.empty:
                cum = (
                    perf.groupby("code")["pct_change"]
                    .apply(lambda s: (s / 100 + 1).prod() - 1)
                    .mul(100)
                    .sort_values(ascending=False)
                )
                if board["code"] in cum.index:
                    rank = int(cum.index.get_loc(board["code"])) + 1
                    lines.append(
                        f"板块近10日累计涨幅 {cum[board['code']]:.1f}%，"
                        f"在 {len(cum)} 个行业板块中排名第 {rank}"
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("板块表现计算失败：%s", e)

        # 同业龙头地位：用因子表在板块成分内对比 20 日涨幅与市值
        try:
            members = self._store.board_members_map([board["code"]]).get(board["code"], [])
            peers = table.index.intersection(members)
            if len(peers) >= 3 and symbol in peers:
                sub = table.loc[peers]
                for col, label in (("chg_20d", "20日涨幅"), ("total_mv_yi", "市值")):
                    if col in sub.columns and pd.notna(row.get(col)):
                        rank = int((sub[col] > row[col]).sum()) + 1
                        lines.append(f"同业{label}排名：第 {rank} / {len(peers)}")
                top = int((table.loc[peers, "chg_20d"] > row.get("chg_20d", -1e9)).sum()) + 1
                if top <= 2:
                    role = "板块龙头"
                elif top <= len(peers) * 0.4:
                    role = "二线跟风"
                else:
                    role = "板块跟风"
                lines.append(f"龙头地位判断：{role}（按20日涨幅同业第 {top}）")
        except Exception as e:  # noqa: BLE001
            logger.warning("同业对比计算失败：%s", e)

        return "\n".join(lines)

    @staticmethod
    def _risk_gate(row: pd.Series, bars: list[dict]) -> dict:
        """风险闸门（纯 Python 硬规则）：识别一票否决/降级类结构性风险。

        返回 {level: pass|warn|block, flags: [...], note: str}。
        说明：质押率/解禁等数据本系统未接入，note 会如实提示需自行核查，不臆造。
        """
        flags: list[str] = []
        level = "pass"

        if bool(row.get("is_st")):
            flags.append("ST/风险警示股，退市风险高")
            level = "block"
        if str(row.get("status", "L")) == "D":
            flags.append("已退市/退市整理期")
            level = "block"

        # 一字板：当日涨跌停且几乎无振幅（次日难以按意愿成交）
        if bars:
            last = bars[-1]
            pct = row.get("pct_change")
            high, low, close = last.get("high"), last.get("low"), last.get("close")
            if pct is not None and not pd.isna(pct) and abs(float(pct)) >= 9.5 and close:
                amp = (float(high) - float(low)) / float(close) * 100 if high and low else 0
                if amp < 1.0:
                    flags.append("一字板，次日流动性差、难按价成交")
                    level = "warn" if level == "pass" else level

        # 流动性：成交额过低（<3000万）冲击成本高
        amt_yi = row.get("amount_yi")
        if amt_yi is not None and not pd.isna(amt_yi) and float(amt_yi) < 0.3:
            flags.append(f"流动性偏弱（成交额 {float(amt_yi):.2f}亿）")
            level = "warn" if level == "pass" else level

        # 小市值操纵风险（提示级）
        mv = row.get("total_mv_yi")
        if mv is not None and not pd.isna(mv) and float(mv) < 30:
            flags.append(f"小市值（{float(mv):.0f}亿），波动大易被资金操纵")

        note = "；".join(flags) if flags else "未触发结构性硬风险"
        note += "（注：质押率/解禁/商誉等数据未接入，请自行核查）"
        return {"level": level, "flags": flags, "note": note}

    async def _news_text(self, symbol: str) -> str:
        """消息面分析师的输入：近期新闻列表（独立方法：网络 IO 不进 to_thread）。"""
        name = self._strategy.name_of(symbol)
        try:
            news = await self._news.stock_news(symbol)
        except Exception as e:  # noqa: BLE001 - 新闻源故障不阻塞诊股
            logger.warning("诊股拉取新闻失败：%s %s", symbol, e)
            news = []
        lines = [
            f"{i + 1}. [{n['publish_time'][:10]}] {n['title']}：{n['summary'][:100]}"
            for i, n in enumerate(news[:12])
        ]
        news_block = "\n".join(lines) if lines else "近期无新闻"
        return f"标的：{name}（{symbol}）\n近期新闻：\n{news_block}"


# ==================== 模块级量化工具函数 ====================


def _first_touch(bars: list[dict], price: float, col: str) -> int | None:
    """首次触及价位的交易日序号（1 起）：目标价看最高价上穿、止损价看最低价下破。

    col="high" → 最高价 ≥ price 视为触及（目标价）；
    col="low"  → 最低价 ≤ price 视为触及（止损价）。未触及返回 None。
    """
    for i, b in enumerate(bars, start=1):
        if col == "high" and b["high"] >= price:
            return i
        if col == "low" and b["low"] <= price:
            return i
    return None


def _index_trend_line(label: str, bars: list[dict]) -> str:
    """指数趋势一行：现价相对 MA20 的位置 + 近5/20日涨跌幅（宏观分析师输入用）。"""
    if not bars:
        return f"{label}：无数据"
    closes = [b["close"] for b in bars]
    last = closes[-1]
    ma20 = sum(closes[-20:]) / min(20, len(closes))
    chg5 = (last / closes[-6] - 1) * 100 if len(closes) > 5 else 0.0
    chg20 = (last / closes[-21] - 1) * 100 if len(closes) > 20 else 0.0
    pos = "上方" if last >= ma20 else "下方"
    return f"{label}：{last:.2f}，{pos}MA20，近5日{chg5:+.1f}%、近20日{chg20:+.1f}%"


def _clip_float(value: object, lo: float, hi: float, default: float) -> float:
    """安全转 float 并夹取到 [lo, hi]；非法值回退 default。"""
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if pd.isna(v):
        return default
    return max(lo, min(hi, v))


def _realized_vol(bars: list[dict]) -> float:
    """近 20 日已实现波动率（日收益率标准差，%）；样本不足返回温和默认 2.5。"""
    pcts = [b["pct_change"] for b in bars[-20:] if b.get("pct_change") is not None]
    if len(pcts) < 5:
        return 2.5
    try:
        return float(statistics.pstdev(pcts)) or 2.5
    except statistics.StatisticsError:
        return 2.5


def _heuristic_timing(up: int, down: int, limit_up: int, limit_down: int) -> float:
    """Python 启发式择时系数：用涨跌家数比与涨跌停温度粗估市场环境（0.3~1.2）。"""
    total = max(1, up + down)
    up_ratio = up / total
    coef = 0.6 + (up_ratio - 0.5) * 1.2  # 涨跌各半→0.6，全涨→1.2，全跌→0.0
    if limit_down > 30:
        coef -= 0.2  # 恐慌惩罚
    if limit_up > 80:
        coef += 0.1  # 亢奋（但过热也有风险，仅小幅加）
    return round(max(0.3, min(1.2, coef)), 2)


def _quant_sizing(realized_vol: float, timing_coef: float) -> dict:
    """量化仓位参考：目标日波动 ~2.5%，按个股波动率缩放，再乘择时系数。

    suggested = 100% × clip(目标波动/实际波动, 0.3, 1.0) × 择时系数。
    高波动股自动降仓，大盘差（择时系数低）整体收缩——简化版波动率平价 + 择时。
    """
    vol_factor = max(0.3, min(1.0, 2.5 / (realized_vol or 2.5)))
    suggested = round(100 * vol_factor * timing_coef)
    return {
        "realized_vol": round(realized_vol, 2),
        "vol_factor": round(vol_factor, 2),
        "suggested_pos": max(0, min(100, suggested)),
    }


def _percentile(table: pd.DataFrame, col: str, symbol: str) -> float | None:
    """某因子列在全市场的百分位（0~100），symbol 缺失/列缺失返回 None。"""
    if col not in table.columns or symbol not in table.index:
        return None
    series = table[col]
    val = series.get(symbol)
    if val is None or pd.isna(val):
        return None
    ranks = series.rank(pct=True)
    pct = ranks.get(symbol)
    return float(pct) * 100 if pct is not None and not pd.isna(pct) else None
