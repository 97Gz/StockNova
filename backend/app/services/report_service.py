"""定时 AI 研报服务：盘后对「自选 + 持仓」批量出诊，汇总成一份研报并推送。

触发时机（挂在盘后完整流水线的最后一步，数据齐备后才跑）：
    日线 → 分钟线 → 扩展数据 → 策略跑批 → 盘面摘要 →【本服务】定时研报

工作流程：
1. 读设置：未启用直接跳过（默认关闭，避免意外消耗 token）；
2. 汇集标的：自选股 ∪ 持仓（各自可开关），去重后按上限截断（控 token）；
3. 批量诊断：持仓注入「成本/浮亏」上下文（评级落到割/守/补），
   用信号量限制并发（默认 2），单只失败不影响整体；
4. 汇总研报：按综合评分排序，分「持仓 / 自选」两栏，生成 Markdown 摘要；
5. 推送：若开启「研报生成后推送」，调 notify_service 发到各 IM/邮箱。

诊断结果本身已落库（diagnosis_runs 表），研报库页面据此回看，无需另存。
"""

import asyncio
import logging
from datetime import datetime

from app.core.database import create_session
from app.services import holdings_service, notify_service, settings_service, watchlist_service

logger = logging.getLogger(__name__)

# 批量诊断的并发上限：兼顾速度与 AI 服务限频
_CONCURRENCY = 2


class ReportService:
    """定时研报编排：依赖诊股服务出诊、报价服务补持仓上下文、推送服务发通知。"""

    def __init__(self, diagnosis_service, quote_service=None) -> None:
        self._diag = diagnosis_service
        self._quote = quote_service
        # 最近一次定时研报的运行摘要（供 API/前端展示「上次研报」状态）
        self._last: dict | None = None

    @property
    def last_run(self) -> dict | None:
        return self._last

    def _read_cfg(self) -> dict:
        with create_session() as db:
            g = settings_service.get_value
            return {
                "enabled": bool(g(db, "report.enabled")),
                "include_watchlist": bool(g(db, "report.include_watchlist")),
                "include_holdings": bool(g(db, "report.include_holdings")),
                "mode": str(g(db, "report.mode")),
                "max_symbols": int(g(db, "report.max_symbols")),
                "push": bool(g(db, "notify.on_report")),
            }

    async def run_scheduled(self, *, reason: str = "schedule", force: bool = False) -> dict:
        """跑一次定时研报。force=True 时无视 enabled 开关（手动触发用）。

        返回运行摘要 {ran, count, reason, generated_at, push_results}。
        """
        cfg = self._read_cfg()
        if not cfg["enabled"] and not force:
            logger.info("定时研报未启用，跳过（%s）", reason)
            return {"ran": False, "reason": "disabled"}

        symbols = self._collect_symbols(cfg)
        if not symbols:
            logger.info("定时研报：无可诊断标的（自选/持仓均为空），跳过")
            return {"ran": False, "reason": "no_symbols"}

        mode = "quick" if cfg["mode"] != "deep" else "deep"
        logger.info("定时研报开始：%d 只标的，模式=%s（%s）", len(symbols), mode, reason)

        # 持仓报价快照（给持仓诊断注入成本/浮亏上下文）
        hold_set = set(holdings_service.list_symbols()) if cfg["include_holdings"] else set()
        quotes: dict[str, dict] = {}
        if hold_set and self._quote is not None:
            try:
                snap = await self._quote.snapshot(list(hold_set))
                quotes = {q["symbol"]: q for q in snap}
            except Exception:  # noqa: BLE001 - 报价失败不阻塞研报
                logger.warning("定时研报：持仓报价快照失败，持仓上下文降级", exc_info=True)

        sem = asyncio.Semaphore(_CONCURRENCY)
        results: list[dict] = []

        async def diagnose(symbol: str) -> None:
            async with sem:
                user_ctx = ""
                if symbol in hold_set:
                    user_ctx = holdings_service.position_context(symbol, quotes.get(symbol))
                try:
                    run = await self._diag.diagnose_now(symbol, mode=mode, user_context=user_ctx)
                    res = run.get("result") or {}
                    results.append(
                        {
                            "symbol": symbol,
                            "name": run.get("name", symbol),
                            "held": symbol in hold_set,
                            "rating": res.get("rating", "—"),
                            "action": res.get("action", ""),
                            "score": int(res.get("score", 0) or 0),
                            "position_pct": int(res.get("position_pct", 0) or 0),
                            "target_price": res.get("target_price") or 0,
                            "stop_loss_price": res.get("stop_loss_price") or 0,
                            "run_id": run.get("run_id"),
                        }
                    )
                except Exception as e:  # noqa: BLE001 - 单只容错
                    logger.warning("定时研报：%s 诊断失败：%s", symbol, e)

        await asyncio.gather(*[diagnose(s) for s in symbols])
        results.sort(key=lambda r: r["score"], reverse=True)

        title, markdown, summary = self._compose(results)
        push_results: list[dict] = []
        if cfg["push"] and results:
            push_results = await notify_service.push(title, markdown, summary)

        self._last = {
            "ran": True,
            "count": len(results),
            "reason": reason,
            "generated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "push_results": push_results,
        }
        logger.info("定时研报完成：成功 %d 只，推送 %d 通道", len(results), len(push_results))
        return self._last

    def _collect_symbols(self, cfg: dict) -> list[str]:
        """汇集待诊标的：自选 ∪ 持仓（各自可关），去重保序，按上限截断。"""
        symbols: list[str] = []
        if cfg["include_watchlist"]:
            symbols += watchlist_service.list_symbols()
        if cfg["include_holdings"]:
            symbols += holdings_service.list_symbols()
        # 去重保序（持仓优先级更高：先放持仓再放自选时持仓排前。这里用 dict 保序去重）
        seen: dict[str, None] = {}
        for s in symbols:
            seen.setdefault(s, None)
        return list(seen.keys())[: max(1, cfg["max_symbols"])]

    @staticmethod
    def _compose(results: list[dict]) -> tuple[str, str, str]:
        """把批量诊断结果汇编成（标题, Markdown 正文, 纯文本摘要）。"""
        day = datetime.now().strftime("%Y-%m-%d")
        title = f"星智股 AI 研报 · {day}"
        if not results:
            return title, "本次无成功诊断的标的。", "本次无成功诊断的标的。"

        def line(r: dict) -> str:
            tp = f"{r['target_price']:.2f}" if r["target_price"] else "—"
            sl = f"{r['stop_loss_price']:.2f}" if r["stop_loss_price"] else "—"
            act = f" · {r['action']}" if r["action"] else ""
            return (
                f"- **{r['rating']}**{act} {r['name']}（{r['symbol']}）"
                f"｜评分 {r['score']} 仓位 {r['position_pct']}% 目标 {tp} 止损 {sl}"
            )

        held = [r for r in results if r["held"]]
        watch = [r for r in results if not r["held"]]
        parts = [f"### {title}", ""]
        if held:
            parts += ["**持仓诊断**", *[line(r) for r in held], ""]
        if watch:
            parts += ["**自选研判**", *[line(r) for r in watch], ""]
        # 摘要：取评分最高的 3 只
        top = results[:3]
        summary = "；".join(
            f"{r['rating']} {r['name']}({r['score']})" for r in top
        )
        parts += ["> 本研报由多角色 AI 工作流自动生成，仅供参考，不构成投资建议。"]
        return title, "\n".join(parts), f"今日重点：{summary}"
