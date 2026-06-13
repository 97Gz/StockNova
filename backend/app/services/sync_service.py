"""数据同步服务：历史全量初始化（断点续传）+ 每日增量同步。

这是 M1 的核心。两条数据通路：

【历史初始化】（首次使用 / 清库重建后，约 1~2 小时）
  主档 → 写进度表(init_progress) → 并发逐只拉 10 年日线（断点续传）
  → 指数日线 → 板块（列表/成分/板块日线）→ 完成
  支持：暂停 / 恢复 / 取消；进度实时推送（EventBus → WebSocket）。

【每日增量】（交易日收盘后定时 / 手动触发，约 1~2 分钟）
  全市场收盘快照（一组请求搞定全部股票）→ 当日日线 + 估值落库
  → 除权检测（昨收对不上的股票重拉复权因子）→ 指数/板块日线刷新。

状态机：idle → running ⇄ paused → (done | failed | cancelled) → idle
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from pypinyin import Style, lazy_pinyin
from sqlalchemy import func, select

from app.adapters.base import DailyBar
from app.adapters.eastmoney import INDEX_SECIDS, EastMoneyAdapter
from app.core.database import create_session
from app.core.events import event_bus
from app.core.exceptions import BizError
from app.models.orm import InitProgress, SyncLog
from app.services import settings_service
from app.services.calendar_service import CalendarService
from app.services.market_store import MarketStore

logger = logging.getLogger(__name__)


def make_pinyin_abbr(name: str) -> str:
    """股票名 → 拼音首字母缩写：贵州茅台 → gzmt（搜索功能的数据基础）。"""
    letters = lazy_pinyin(name, style=Style.FIRST_LETTER)
    return "".join(ch for ch in "".join(letters) if ch.isalnum()).lower()


class SyncService:
    """同步任务的状态机与执行器（应用内单例）。"""

    def __init__(self, store: MarketStore, calendar: CalendarService) -> None:
        self._store = store
        self._calendar = calendar
        self._task: asyncio.Task | None = None
        # 暂停控制：Event 置位 = 放行，清除 = 暂停（工作协程在每只股票之间检查）
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        self._cancelled = False

        # 进度快照：REST 轮询与 WebSocket 推送共用同一份数据
        self.state: str = "idle"
        self.progress: dict[str, Any] = {}

    # ---------------- 对外控制接口 ----------------

    def status(self) -> dict[str, Any]:
        return {"state": self.state, **self.progress}

    def synced_today(self) -> bool:
        """今天是否已成功完成每日增量 / 历史初始化（启动补偿的判据）。

        以 SyncLog 的 started_at 自然日匹配今天为准：只要当天有一条
        daily 或 init_history 的 success 记录，就认为今日数据已落库。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        with create_session() as db:
            rows = db.scalars(
                select(SyncLog).where(
                    SyncLog.task_type.in_(("daily", "init_history")),
                    SyncLog.status == "success",
                )
            ).all()
        return any(r.started_at.strftime("%Y-%m-%d") == today for r in rows)

    async def start_init_history(self, *, rebuild: bool = False) -> None:
        """启动历史初始化。rebuild=True 时先清空行情库和进度表（重头再来）。"""
        if self.state in ("running", "paused"):
            raise BizError(40901, "已有同步任务在进行中")
        if rebuild:
            await asyncio.to_thread(self._store.clear_market_data)
            await asyncio.to_thread(self._clear_init_progress)
        self._cancelled = False
        self._resume_event.set()
        self.state = "running"
        self._task = asyncio.create_task(self._run_init_history())

    async def start_daily_sync(self) -> None:
        """启动每日增量（定时器与手动按钮都走这里）。"""
        if self.state in ("running", "paused"):
            raise BizError(40901, "已有同步任务在进行中")
        self._cancelled = False
        self._resume_event.set()
        self.state = "running"
        self._task = asyncio.create_task(self._run_daily_sync())

    def pause(self) -> None:
        if self.state != "running":
            raise BizError(40902, "当前没有可暂停的任务")
        self._resume_event.clear()
        self.state = "paused"
        self._publish_progress()

    def resume(self) -> None:
        if self.state != "paused":
            raise BizError(40903, "当前没有可恢复的任务")
        self._resume_event.set()
        self.state = "running"
        self._publish_progress()

    def cancel(self) -> None:
        if self.state not in ("running", "paused"):
            raise BizError(40904, "当前没有可取消的任务")
        self._cancelled = True
        self._resume_event.set()  # 若在暂停中，先放行让协程跑到检查点退出

    # ---------------- 内部工具 ----------------

    def _publish_progress(self) -> None:
        event_bus.publish({"type": "sync_progress", "state": self.state, **self.progress})

    async def _checkpoint(self) -> None:
        """暂停/取消检查点：每个最小工作单元之间调用。"""
        if self._cancelled:
            raise asyncio.CancelledError
        await self._resume_event.wait()

    def _clear_init_progress(self) -> None:
        with create_session() as db:
            db.query(InitProgress).delete()
            db.commit()

    def _read_settings(self) -> dict[str, Any]:
        """读取本次任务相关的全部配置（在任务开始时一次性读取）。"""
        with create_session() as db:
            return {
                "years": settings_service.get_value(db, "data.history_years"),
                "concurrency": settings_service.get_value(db, "data.history_concurrency"),
                "delay_ms": settings_service.get_value(db, "data.request_delay_ms"),
            }

    # ---------------- 历史初始化 ----------------

    async def _run_init_history(self) -> None:
        cfg = await asyncio.to_thread(self._read_settings)
        adapter = EastMoneyAdapter(delay_ms=cfg["delay_ms"])
        log_id = await asyncio.to_thread(self._create_log, "init_history")
        beg = (datetime.now() - timedelta(days=365 * cfg["years"])).strftime("%Y%m%d")

        try:
            # ---- 阶段 1：交易日历 + 股票主档 ----
            self.progress = {"phase": "主档", "total": 0, "done": 0, "failed": 0, "message": ""}
            self._publish_progress()
            await self._calendar.ensure_loaded()

            basics = await adapter.fetch_stock_list()
            for b in basics:
                b.pinyin = make_pinyin_abbr(b.name)
            await asyncio.to_thread(self._store.upsert_stock_basics, basics)

            # ---- 阶段 2：构建/续传进度表 ----
            symbols = [b.symbol for b in basics]
            pending = await asyncio.to_thread(self._prepare_progress_rows, symbols)
            total_done = len(symbols) - len(pending)
            self.progress = {
                "phase": "历史日线",
                "total": len(symbols),
                "done": total_done,
                "failed": 0,
                "message": f"断点续传：跳过已完成 {total_done} 只" if total_done else "",
            }
            self._publish_progress()

            # ---- 阶段 3：并发拉历史日线（断点续传核心循环）----
            semaphore = asyncio.Semaphore(cfg["concurrency"])
            failed_count = 0
            done_count = total_done
            progress_lock = asyncio.Lock()

            async def pull_one(symbol: str) -> None:
                nonlocal failed_count, done_count
                async with semaphore:
                    await self._checkpoint()
                    try:
                        bars = await adapter.fetch_daily_bars(symbol, beg)
                        await asyncio.to_thread(self._store.replace_symbol_bars, symbol, bars)
                        last_date = bars[-1].trade_date if bars else ""
                        await asyncio.to_thread(self._mark_progress, symbol, "done", last_date, "")
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 - 单只失败不拖垮整体
                        logger.warning("拉取 %s 失败：%s(%s)", symbol, type(e).__name__, e)
                        await asyncio.to_thread(
                            self._mark_progress,
                            symbol,
                            "failed",
                            "",
                            f"{type(e).__name__}: {e}".rstrip(": "),
                        )
                        async with progress_lock:
                            failed_count += 1
                    else:
                        async with progress_lock:
                            done_count += 1
                    # 进度节流推送：每 10 只或收尾时推一次，避免事件风暴
                    if (done_count + failed_count) % 10 == 0 or (
                        done_count + failed_count >= len(symbols)
                    ):
                        self.progress.update(done=done_count, failed=failed_count, current=symbol)
                        self._publish_progress()

            await asyncio.gather(*[pull_one(s) for s in pending])

            # ---- 阶段 4：指数日线 ----
            # 分项容错：单个指数失败只记数，不让整个初始化失败
            # （K 线域被限流时，后面的板块成分走备用域仍能继续）
            self.progress.update(phase="指数日线", message="")
            self._publish_progress()
            stage_errors = 0
            for index_symbol in INDEX_SECIDS:
                await self._checkpoint()
                try:
                    bars = await adapter.fetch_index_daily(index_symbol, beg)
                    await asyncio.to_thread(self._store.replace_index_daily, index_symbol, bars)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    stage_errors += 1
                    logger.warning("指数 %s 拉取失败：%s(%s)", index_symbol, type(e).__name__, e)

            # ---- 阶段 5：板块（列表 + 成分 + 板块日线）----
            self.progress.update(phase="板块数据")
            self._publish_progress()
            boards = await adapter.fetch_board_list()
            await asyncio.to_thread(self._store.replace_boards, boards)

            # 断点续传：跳过已有成分数据的板块（中断/限流后重跑零浪费）
            existing_codes = await asyncio.to_thread(self._store.board_codes_with_data)
            pending_boards = [b for b in boards if b.code not in existing_codes]
            board_total = len(pending_boards)
            if existing_codes:
                self.progress.update(message=f"板块续传：跳过已完成 {len(existing_codes)} 个")
                self._publish_progress()

            board_done = 0

            async def pull_board(code: str) -> None:
                """成分与日线分开容错：限流时各自能成的先入库，
                断点判据（两类都有才算完成）保证缺的部分下次续传补齐。"""
                nonlocal board_done, stage_errors
                async with semaphore:
                    await self._checkpoint()
                    try:
                        members = await adapter.fetch_board_members(code)
                        await asyncio.to_thread(self._store.replace_board_members, code, members)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        stage_errors += 1
                        logger.warning("板块 %s 成分失败：%s(%s)", code, type(e).__name__, e)
                    try:
                        bars = await adapter.fetch_board_daily(code, beg)
                        await asyncio.to_thread(self._store.replace_board_daily, code, bars)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        stage_errors += 1
                        logger.warning("板块 %s 日线失败：%s(%s)", code, type(e).__name__, e)
                    board_done += 1
                    if board_done % 10 == 0:
                        self.progress.update(message=f"板块 {board_done}/{board_total}")
                        self._publish_progress()

            await asyncio.gather(*[pull_board(b.code) for b in pending_boards])

            # ---- 阶段 6：当日估值快照（让基本面数据从今天开始积累）----
            self.progress.update(phase="估值快照", message="")
            self._publish_progress()
            try:
                await self._snapshot_fundamentals(adapter)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 快照失败不否定整个初始化
                stage_errors += 1
                logger.warning("估值快照失败：%s(%s)", type(e).__name__, e)

            # ---- 收尾 ----
            self.state = "done"
            summary = f"失败 {failed_count} 只"
            if stage_errors:
                summary += f"；指数/板块/快照子项失败 {stage_errors} 个（重跑初始化可补齐）"
            self.progress.update(phase="完成", message=summary)
            await asyncio.to_thread(
                self._finish_log,
                log_id,
                "success",
                len(symbols),
                done_count,
                failed_count,
                summary if stage_errors else "",
            )
        except asyncio.CancelledError:
            self.state = "cancelled"
            self.progress.update(message="任务已取消（进度已保存，可断点续传）")
            await asyncio.to_thread(
                self._finish_log,
                log_id,
                "cancelled",
                self.progress.get("total", 0),
                self.progress.get("done", 0),
                self.progress.get("failed", 0),
                "用户取消",
            )
        except Exception as e:  # noqa: BLE001 - 顶层兜底：任何意外都要落日志并复位状态
            logger.exception("历史初始化任务异常")
            self.state = "failed"
            # 带异常类型名：TimeoutError 等异常 str() 为空，纯 {e} 会落出空白原因
            reason = f"{type(e).__name__}: {e}".rstrip(": ")
            self.progress.update(message=f"任务失败：{reason}")
            await asyncio.to_thread(
                self._finish_log,
                log_id,
                "failed",
                self.progress.get("total", 0),
                self.progress.get("done", 0),
                self.progress.get("failed", 0),
                reason,
            )
        finally:
            await adapter.close()
            self._publish_progress()

    def _prepare_progress_rows(self, symbols: list[str]) -> list[str]:
        """确保每只股票在进度表有记录；返回未完成（pending/failed）的代码列表。"""
        with create_session() as db:
            existing = {row.symbol: row.status for row in db.scalars(select(InitProgress)).all()}
            for symbol in symbols:
                if symbol not in existing:
                    db.add(InitProgress(symbol=symbol, status="pending"))
            db.commit()
            return [s for s in symbols if existing.get(s) != "done"]

    def _mark_progress(self, symbol: str, status: str, last_date: str, error: str) -> None:
        with create_session() as db:
            row = db.get(InitProgress, symbol)
            if row is None:
                row = InitProgress(symbol=symbol)
                db.add(row)
            row.status = status
            row.last_date = last_date
            row.error = error[:500]
            db.commit()

    # ---------------- 每日增量 ----------------

    async def _run_daily_sync(self) -> None:
        cfg = await asyncio.to_thread(self._read_settings)
        adapter = EastMoneyAdapter(delay_ms=cfg["delay_ms"])
        log_id = await asyncio.to_thread(self._create_log, "daily")

        try:
            await self._calendar.ensure_loaded()
            trade_date = self._calendar.latest_trade_date()
            if not trade_date:
                raise BizError(50001, "交易日历为空，无法判定交易日")

            # ---- 全市场收盘快照 ----
            self.progress = {"phase": "收盘快照", "total": 4, "done": 0, "message": trade_date}
            self._publish_progress()
            quotes = await adapter.fetch_spot_snapshot()
            # 停牌/无成交的股票当日无 K 线（价格为 0 或无成交量）
            active = [q for q in quotes if q.price > 0 and q.volume > 0]

            # ---- 除权检测 + 因子延续 ----
            self.progress.update(phase="除权检测", done=1)
            self._publish_progress()
            last_states = await asyncio.to_thread(self._store.get_last_bar_states)
            beg = (datetime.now() - timedelta(days=365 * cfg["years"])).strftime("%Y%m%d")

            bars: list[DailyBar] = []
            refetch: list[str] = []  # 需要整只重拉的股票（除权 / 新股）
            for q in active:
                state = last_states.get(q.symbol)
                if state is None:
                    refetch.append(q.symbol)  # 库里没有：新上市股票
                    continue
                last_date, last_close, last_factor = state
                if last_date >= trade_date:
                    continue  # 当日已同步过（幂等重跑）
                # 除权判定：今日快照的"昨收"是除权参考价，与库里最后收盘对不上
                # 说明发生了分红/送转，复权因子序列需要重建
                if abs(q.prev_close - last_close) > 0.005:
                    refetch.append(q.symbol)
                    continue
                bars.append(
                    DailyBar(
                        symbol=q.symbol,
                        trade_date=trade_date,
                        open=q.open,
                        high=q.high,
                        low=q.low,
                        close=q.price,
                        volume=q.volume,
                        amount=q.amount,
                        pct_change=q.pct_change,
                        turnover=q.turnover,
                        adj_factor=last_factor,  # 无除权 → 因子延续昨日
                    )
                )

            if bars:
                await asyncio.to_thread(self._store.append_eod_bars, bars, trade_date)
            await asyncio.to_thread(self._store.append_fundamentals, active, trade_date)

            # ---- 重拉除权/新股 ----
            self.progress.update(
                phase="除权/新股重拉", done=2, message=f"{len(refetch)} 只需要重拉"
            )
            self._publish_progress()
            semaphore = asyncio.Semaphore(cfg["concurrency"])

            async def refetch_one(symbol: str) -> None:
                async with semaphore:
                    await self._checkpoint()
                    try:
                        full = await adapter.fetch_daily_bars(symbol, beg)
                        await asyncio.to_thread(self._store.replace_symbol_bars, symbol, full)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("重拉 %s 失败：%s", symbol, e)

            await asyncio.gather(*[refetch_one(s) for s in refetch])

            # ---- 指数 + 板块日线刷新（数量少，直接整段重拉最稳）----
            # 分项容错：K 线域被限流时单项失败只记日志，
            # 不让已完成的快照/增量成果连带标失败
            self.progress.update(phase="指数与板块", done=3, message="")
            self._publish_progress()
            for index_symbol in INDEX_SECIDS:
                await self._checkpoint()
                try:
                    idx_bars = await adapter.fetch_index_daily(index_symbol, beg)
                    await asyncio.to_thread(self._store.replace_index_daily, index_symbol, idx_bars)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    logger.warning("指数 %s 刷新失败：%s(%s)", index_symbol, type(e).__name__, e)

            boards = await asyncio.to_thread(self._store.list_boards)

            async def refresh_board(code: str) -> None:
                async with semaphore:
                    await self._checkpoint()
                    try:
                        board_bars = await adapter.fetch_board_daily(code, beg)
                        await asyncio.to_thread(self._store.replace_board_daily, code, board_bars)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("板块 %s 日线刷新失败：%s", code, e)

            await asyncio.gather(*[refresh_board(b[0]) for b in boards])

            self.state = "done"
            self.progress.update(
                phase="完成",
                done=4,
                message=f"{trade_date}：{len(bars)} 只增量 + {len(refetch)} 只重拉",
            )
            await asyncio.to_thread(
                self._finish_log,
                log_id,
                "success",
                len(active),
                len(bars) + len(refetch),
                0,
                f"trade_date={trade_date}",
            )
        except asyncio.CancelledError:
            self.state = "cancelled"
            await asyncio.to_thread(self._finish_log, log_id, "cancelled", 0, 0, 0, "用户取消")
        except Exception as e:  # noqa: BLE001
            logger.exception("每日增量同步异常")
            self.state = "failed"
            reason = f"{type(e).__name__}: {e}".rstrip(": ")
            self.progress.update(message=f"任务失败：{reason}")
            await asyncio.to_thread(self._finish_log, log_id, "failed", 0, 0, 0, reason)
        finally:
            await adapter.close()
            self._publish_progress()

    async def _snapshot_fundamentals(self, adapter: EastMoneyAdapter) -> None:
        """拉一次全市场快照并落当日估值（初始化收尾时让估值从当天开始积累）。"""
        trade_date = self._calendar.latest_trade_date()
        if not trade_date:
            return
        quotes = await adapter.fetch_spot_snapshot()
        active = [q for q in quotes if q.price > 0]
        await asyncio.to_thread(self._store.append_fundamentals, active, trade_date)

    # ---------------- 同步日志 ----------------

    def _create_log(self, task_type: str) -> int:
        with create_session() as db:
            row = SyncLog(task_type=task_type, status="running")
            db.add(row)
            db.commit()
            return row.id

    def _finish_log(
        self, log_id: int, status: str, total: int, done: int, failed: int, message: str
    ) -> None:
        with create_session() as db:
            row = db.get(SyncLog, log_id)
            if row is None:
                return
            row.status = status
            row.finished_at = datetime.now()
            row.total = total
            row.done = done
            row.failed = failed
            row.message = message[:1000]
            db.commit()

    @staticmethod
    def _log_to_dict(r: SyncLog) -> dict[str, Any]:
        """SyncLog ORM → 前端展示字典（统一两个查询方法的输出结构）。"""
        return {
            "id": r.id,
            "task_type": r.task_type,
            "status": r.status,
            "started_at": r.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": (r.finished_at.strftime("%Y-%m-%d %H:%M:%S") if r.finished_at else ""),
            "total": r.total,
            "done": r.done,
            "failed": r.failed,
            "message": r.message,
        }

    def recent_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        """最近的同步记录（兼容旧调用；新前端走 logs_paged 分页）。"""
        with create_session() as db:
            rows = db.scalars(select(SyncLog).order_by(SyncLog.id.desc()).limit(limit)).all()
            return [self._log_to_dict(r) for r in rows]

    def logs_paged(self, page: int = 1, page_size: int = 10) -> dict[str, Any]:
        """分页查询同步历史（设置中心"同步历史"列表 + 分页器用）。

        返回 {items, total, page, page_size}；page 从 1 起，越界自动夹取。
        """
        page = max(1, page)
        page_size = max(1, min(page_size, 100))  # 限定单页上限，防止一次拉太多
        with create_session() as db:
            total = db.scalar(select(func.count()).select_from(SyncLog)) or 0
            rows = db.scalars(
                select(SyncLog)
                .order_by(SyncLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return {
                "items": [self._log_to_dict(r) for r in rows],
                "total": int(total),
                "page": page,
                "page_size": page_size,
            }
