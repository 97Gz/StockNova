"""定时任务调度：APScheduler 封装。

当前注册的任务（交易日盘后流水线，时间错开保证依赖顺序）：
- daily_sync：15:35（可配）日线增量同步
- minute_sync：15:40 全市场 5 分钟线同步（腾讯源，2~4 分钟跑完）
- ext_sync：15:45 扩展数据同步（资金流/龙虎榜/业绩预告/人气榜，东财源）
- strategy_batch：15:50 全策略跑批存档（依赖上面三步的当日数据）
- market_summary：16:00 AI 盘面摘要（每日复盘，依赖当日日线与板块数据）

设计：调度器只负责"到点触发"，具体执行与状态管理全在各服务。
非交易日的跳过逻辑在触发函数里判断（调度器本身不感知节假日）。
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.database import create_session
from app.services import settings_service

logger = logging.getLogger(__name__)


class AppScheduler:
    """应用级调度器：随 FastAPI 生命周期启动/停止。"""

    def __init__(self) -> None:
        # job_defaults：misfire_grace_time 给"短暂错过"留 1 小时补救窗口
        # （如 15:35 恰逢重启，进程稍后起来仍会补触发一次）；coalesce 合并堆积触发。
        self._scheduler = AsyncIOScheduler(
            timezone="Asia/Shanghai",
            job_defaults={"misfire_grace_time": 3600, "coalesce": True},
        )
        self._sync_service = None  # 延迟注入（main.py 装配时 set）
        self._strategy_service = None
        self._ext_sync_service = None
        self._minute_sync_service = None
        self._news_service = None
        self._market_store = None
        self._report_service = None  # 定时 AI 研报服务（阶段B 注入）

    def setup(
        self,
        sync_service,
        strategy_service=None,
        ext_sync_service=None,
        minute_sync_service=None,
        news_service=None,
        market_store=None,
        report_service=None,
    ) -> None:
        """注入依赖并注册任务（应用启动时调用一次）。"""
        self._sync_service = sync_service
        self._strategy_service = strategy_service
        self._ext_sync_service = ext_sync_service
        self._minute_sync_service = minute_sync_service
        self._news_service = news_service
        self._market_store = market_store
        self._report_service = report_service
        self.reschedule_daily_sync()
        if minute_sync_service is not None:
            # 15:40：5分钟线同步（腾讯源，与 15:45 东财扩展数据不同域可重叠）
            self._scheduler.add_job(
                self._trigger_minute_sync,
                CronTrigger(hour=15, minute=40),
                id="minute_sync",
                replace_existing=True,
            )
        if ext_sync_service is not None:
            # 15:45：扩展数据同步（在增量同步默认 15:35 之后、策略跑批之前——
            # 资金流/龙虎榜因子要先落库，跑批才能扫到当日数据）
            self._scheduler.add_job(
                self._trigger_ext_sync,
                CronTrigger(hour=15, minute=45),
                id="ext_sync",
                replace_existing=True,
            )
        if strategy_service is not None:
            # 15:50：每日增量同步（默认 15:35）落库后跑批，时间错开留足余量
            self._scheduler.add_job(
                self._trigger_strategy_batch,
                CronTrigger(hour=15, minute=50),
                id="strategy_batch",
                replace_existing=True,
            )
        if news_service is not None and market_store is not None:
            # 16:00：每日 AI 复盘（盘后流水线收尾后自动生成，首页盘面摘要卡直接读缓存）
            self._scheduler.add_job(
                self._trigger_market_summary,
                CronTrigger(hour=16, minute=0),
                id="market_summary",
                replace_existing=True,
            )
        self._scheduler.start()

    async def _trigger_ext_sync(self) -> None:
        if self._ext_sync_service is None or self._sync_service is None:
            return
        if not self._sync_service._calendar.is_trading_day():
            logger.info("今日非交易日，跳过扩展数据同步")
            return
        logger.info("定时扩展数据同步开始")
        await self._ext_sync_service.run()

    async def _trigger_minute_sync(self) -> None:
        if self._minute_sync_service is None or self._sync_service is None:
            return
        if not self._sync_service._calendar.is_trading_day():
            logger.info("今日非交易日，跳过分钟线同步")
            return
        logger.info("定时分钟线同步开始")
        await self._minute_sync_service.run()

    async def _trigger_strategy_batch(self) -> None:
        if self._strategy_service is None or self._sync_service is None:
            return
        if not self._sync_service._calendar.is_trading_day():
            logger.info("今日非交易日，跳过策略跑批")
            return
        logger.info("定时策略跑批开始")
        await asyncio.to_thread(self._strategy_service.run_daily_batch)

    async def _trigger_market_summary(self) -> None:
        """每日 AI 复盘：当日已生成则幂等跳过；AI 未配置/失败只记日志不报错。"""
        if self._news_service is None or self._market_store is None or self._sync_service is None:
            return
        if not self._sync_service._calendar.is_trading_day():
            logger.info("今日非交易日，跳过 AI 盘面摘要")
            return
        # analyze_market 自带"当日已有缓存直接返回"的幂等保护，这里再前置判断省一次组装
        if self._news_service.get_cached_market_summary() is not None:
            logger.info("今日盘面摘要已存在，跳过定时生成")
            return
        # 延迟导入避免 core → services 在模块加载期的环
        from app.services.news_service import build_market_text

        try:
            titles: list[str] = []
            try:
                feed = await self._news_service.feed(column="101")
                titles = [it["title"] for it in feed["items"][:10]]
            except Exception:  # noqa: BLE001 - 快讯拉取失败仅用盘面统计生成
                logger.warning("定时盘面摘要：快讯拉取失败，仅用盘面统计生成")
            text = build_market_text(self._market_store, titles)
            await self._news_service.analyze_market(text, titles)
            logger.info("定时 AI 盘面摘要生成完成")
        except Exception:  # noqa: BLE001 - AI 未配置/调用失败不影响其它任务
            logger.warning("定时 AI 盘面摘要生成失败（可在首页手动生成）", exc_info=True)

    # ---------------- 完整盘后流水线 / 启动补偿 ----------------

    async def run_full_pipeline(self, *, reason: str = "manual", run_reports: bool = True) -> None:
        """按依赖顺序串行跑完整盘后流水线：日线→分钟线→扩展数据→策略跑批→盘面摘要→定时研报。

        用途：①启动补偿（错过定时点时自动补今天）②设置中心「立即同步今日」按钮。
        非交易日直接跳过。各步骤独立容错，单步失败只记日志、不中断后续。
        """
        sync = self._sync_service
        if sync is None:
            return
        if not sync._calendar.is_trading_day():
            logger.info("今日非交易日，跳过完整流水线（%s）", reason)
            return
        logger.info("完整盘后流水线开始（原因：%s）", reason)

        # 1) 日线增量：复用同步状态机；已在跑就等它跑完，避免并发写库
        try:
            if sync.state not in ("running", "paused"):
                await sync.start_daily_sync()
            if sync._task is not None:
                await sync._task
        except Exception:  # noqa: BLE001
            logger.warning("流水线-日线增量失败", exc_info=True)

        # 2) 5 分钟线
        if self._minute_sync_service is not None:
            try:
                await self._minute_sync_service.run()
            except Exception:  # noqa: BLE001
                logger.warning("流水线-分钟线失败", exc_info=True)

        # 3) 扩展数据（资金流/龙虎榜/业绩预告/人气榜）
        if self._ext_sync_service is not None:
            try:
                await self._ext_sync_service.run()
            except Exception:  # noqa: BLE001
                logger.warning("流水线-扩展数据失败", exc_info=True)

        # 4) 策略跑批（依赖上面三步的当日数据）
        if self._strategy_service is not None:
            try:
                await asyncio.to_thread(self._strategy_service.run_daily_batch)
            except Exception:  # noqa: BLE001
                logger.warning("流水线-策略跑批失败", exc_info=True)

        # 5) AI 盘面摘要（已含当日幂等保护）
        try:
            await self._trigger_market_summary()
        except Exception:  # noqa: BLE001
            logger.warning("流水线-盘面摘要失败", exc_info=True)

        # 6) 定时 AI 研报（对自选+持仓批量出诊，依赖当日数据齐备）
        if run_reports and self._report_service is not None:
            try:
                await self._report_service.run_scheduled(reason=reason)
            except Exception:  # noqa: BLE001
                logger.warning("流水线-定时研报失败", exc_info=True)

        logger.info("完整盘后流水线结束（原因：%s）", reason)

    async def maybe_catchup_on_startup(self) -> None:
        """启动补偿：交易日 ∧ 已过每日同步点 ∧ 当天未成功同步 → 自动补跑完整流水线。

        解决"15:35 定时点没开机、16:00 才启动当天就同步不上"的问题。
        """
        from datetime import datetime

        sync = self._sync_service
        if sync is None or not sync._calendar.is_trading_day():
            return
        with create_session() as db:
            enabled = settings_service.get_value(db, "data.daily_sync_enabled")
            time_str = settings_service.get_value(db, "data.daily_sync_time")
        if not enabled:
            return
        hour, _, minute = time_str.partition(":")
        sync_point = datetime.now().replace(
            hour=int(hour), minute=int(minute), second=0, microsecond=0
        )
        if datetime.now() < sync_point:
            logger.info("启动补偿：未到今日同步点 %s，交由定时任务处理", time_str)
            return
        if sync.synced_today():
            logger.info("启动补偿：今日已成功同步，无需补偿")
            return
        logger.info("启动补偿：检测到今日过点(%s)未同步，自动补跑完整流水线", time_str)
        await self.run_full_pipeline(reason="startup-catchup")

    def trigger_full_pipeline_bg(self) -> None:
        """后台启动一次完整流水线（设置中心「立即同步今日」按钮调用，立即返回）。"""
        asyncio.create_task(self.run_full_pipeline(reason="manual-today"))

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def reschedule_daily_sync(self) -> None:
        """按设置中心的时间（重新）注册每日增量任务；设置保存后调用即生效。"""
        with create_session() as db:
            enabled = settings_service.get_value(db, "data.daily_sync_enabled")
            time_str = settings_service.get_value(db, "data.daily_sync_time")

        job_id = "daily_sync"
        existing = self._scheduler.get_job(job_id)
        if existing is not None:
            existing.remove()

        if not enabled:
            logger.info("每日自动同步已关闭")
            return

        hour, _, minute = time_str.partition(":")
        self._scheduler.add_job(
            self._trigger_daily_sync,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id=job_id,
            replace_existing=True,
        )
        logger.info("每日增量同步已排程：工作日 %s", time_str)

    async def _trigger_daily_sync(self) -> None:
        """定时触发入口：非交易日直接跳过；有任务在跑也跳过（不排队）。"""
        sync = self._sync_service
        if sync is None:
            return
        if not sync._calendar.is_trading_day():
            logger.info("今日非交易日，跳过增量同步")
            return
        if sync.state in ("running", "paused"):
            logger.warning("已有同步任务进行中，跳过本次定时触发")
            return
        await sync.start_daily_sync()


# 全局单例（main.py 生命周期里 setup/shutdown）
app_scheduler = AppScheduler()
