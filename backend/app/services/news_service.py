"""消息中心服务（M5）：快讯流 / 个股新闻 / AI 情绪分析。

缓存策略（新闻是高频读、低时效要求的数据）：
- 快讯首页（无游标）：60 秒内存缓存 —— 自动刷新不会打爆数据源；
- 个股新闻：10 分钟内存缓存；
- AI 情绪分：SQLite 按 (symbol, 日期) 缓存，一天一只股票最多分析一次。
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import date

from app.adapters.eastmoney_ext import EastMoneyExtAdapter
from app.core.database import create_session
from app.models.orm import NewsSentiment
from app.services import prompt_service
from app.services.ai_client import AIClient

logger = logging.getLogger(__name__)


class NewsService:
    def __init__(self) -> None:
        self._adapter = EastMoneyExtAdapter(delay_ms=120)
        self._ai = AIClient()
        # 内存缓存：{key: (存入时刻, 数据)}
        self._cache: dict[str, tuple[float, object]] = {}
        # 同一只股票的并发分析请求合并成一次（防双击/多端同时触发）
        self._inflight: dict[str, asyncio.Task] = {}

    async def close(self) -> None:
        await self._adapter.close()

    def _cache_get(self, key: str, ttl: float) -> object | None:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        return None

    def _cache_put(self, key: str, value: object) -> None:
        self._cache[key] = (time.monotonic(), value)
        if len(self._cache) > 200:  # 粗暴防膨胀：超限清最旧的一半
            items = sorted(self._cache.items(), key=lambda kv: kv[1][0])
            self._cache = dict(items[len(items) // 2 :])

    # ---------------- 快讯流 ----------------

    async def feed(self, cursor: str = "", column: str = "102") -> dict:
        """7×24 快讯。无游标的首页带 60s 缓存；翻页请求直通数据源。"""
        cache_key = f"feed:{column}"
        if not cursor:
            cached = self._cache_get(cache_key, ttl=60)
            if cached is not None:
                return cached  # type: ignore[return-value]
        items, next_cursor = await self._adapter.fetch_fast_news(cursor=cursor, column=column)
        result = {"items": [asdict(n) for n in items], "next_cursor": next_cursor}
        if not cursor:
            self._cache_put(cache_key, result)
        return result

    # ---------------- 个股新闻 ----------------

    async def stock_news(self, symbol: str) -> list[dict]:
        cache_key = f"stock:{symbol}"
        cached = self._cache_get(cache_key, ttl=600)
        if cached is not None:
            return cached  # type: ignore[return-value]
        items = await self._adapter.fetch_stock_news(symbol)
        result = [asdict(n) for n in items]
        self._cache_put(cache_key, result)
        return result

    # ---------------- AI 情绪分析 ----------------

    @staticmethod
    def _row_to_dict(row: NewsSentiment) -> dict:
        points = json.loads(row.points_json or "{}")
        return {
            "symbol": row.symbol,
            "date": row.trade_date,
            "score": row.score,
            "label": row.label,
            "summary": row.summary,
            "positive": points.get("positive", []),
            "negative": points.get("negative", []),
            "news_titles": json.loads(row.news_json or "[]"),
            "analyzed_at": row.created_at.isoformat(sep=" ", timespec="seconds"),
        }

    def get_cached_sentiment(self, symbol: str) -> dict | None:
        """读当日情绪缓存（没有则返回 None，不触发分析）。"""
        today = date.today().isoformat()
        with create_session() as db:
            row = (
                db.query(NewsSentiment)
                .filter(NewsSentiment.symbol == symbol, NewsSentiment.trade_date == today)
                .order_by(NewsSentiment.id.desc())
                .first()
            )
            return self._row_to_dict(row) if row else None

    def batch_cached_sentiment(self, symbols: list[str]) -> dict[str, dict]:
        """批量读当日缓存（自选页/策略因子用），不存在的票不在结果里。"""
        today = date.today().isoformat()
        with create_session() as db:
            rows = (
                db.query(NewsSentiment)
                .filter(NewsSentiment.trade_date == today, NewsSentiment.symbol.in_(symbols))
                .all()
            )
            return {r.symbol: self._row_to_dict(r) for r in rows}

    def all_today_scores(self) -> dict[str, int]:
        """今日全部已分析股票的分数（策略因子 senti_score 的数据源）。"""
        today = date.today().isoformat()
        with create_session() as db:
            rows = db.query(NewsSentiment).filter(NewsSentiment.trade_date == today).all()
            return {r.symbol: r.score for r in rows}

    # ---------------- AI 盘面摘要 ----------------
    # 复用 news_sentiment 表存储，symbol 固定为 "MARKET"（一天一条）

    MARKET_KEY = "MARKET"

    def get_cached_market_summary(self) -> dict | None:
        """读当日盘面摘要缓存（没有返回 None，前端显示"生成"按钮）。"""
        return self.get_cached_sentiment(self.MARKET_KEY)

    async def analyze_market(self, market_text: str, news_titles: list[str]) -> dict:
        """生成当日 AI 盘面摘要（已有缓存直接返回，不重复花 token）。

        market_text：API 层组装好的盘面统计文本（涨跌家数/成交额/板块表现/快讯）。
        """
        cached = self.get_cached_market_summary()
        if cached is not None:
            return {**cached, "from_cache": True}

        data = await self._ai.chat_json(prompt_service.render("market_summary"), market_text)
        score = max(0, min(100, int(data.get("score", 50))))
        label = str(data.get("label", "分化"))
        if label not in ("普涨", "偏暖", "分化", "偏冷", "普跌"):
            label = "偏暖" if score > 60 else ("偏冷" if score < 40 else "分化")

        with create_session() as db:
            row = NewsSentiment(
                symbol=self.MARKET_KEY,
                trade_date=date.today().isoformat(),
                score=score,
                label=label,
                summary=str(data.get("summary", ""))[:300],
                points_json=json.dumps(
                    {
                        "positive": list(data.get("positive", []))[:5],
                        "negative": list(data.get("negative", []))[:5],
                    },
                    ensure_ascii=False,
                ),
                news_json=json.dumps(news_titles[:12], ensure_ascii=False),
            )
            db.add(row)
            db.commit()
            result = self._row_to_dict(row)
        logger.info("AI 盘面摘要生成完成：score=%d label=%s", score, label)
        return {**result, "from_cache": False}

    async def analyze_sentiment(self, symbol: str, name: str = "") -> dict:
        """分析一只股票的消息面情绪（当日已分析直接回缓存）。

        并发合并：同一只股票同时来多个请求只跑一次 LLM。
        """
        cached = self.get_cached_sentiment(symbol)
        if cached is not None:
            return {**cached, "from_cache": True}

        running = self._inflight.get(symbol)
        if running is None or running.done():
            running = asyncio.create_task(self._do_analyze(symbol, name))
            self._inflight[symbol] = running
        try:
            result = await running
        finally:
            self._inflight.pop(symbol, None)
        return result

    async def _do_analyze(self, symbol: str, name: str) -> dict:
        news = await self.stock_news(symbol)
        if not news:
            raise ValueError(f"未找到 {symbol} 的近期新闻，无法分析")
        # 最多取 12 条、每条标题+摘要前 120 字（控制 token 用量）
        lines = [
            f"{i + 1}. [{n['publish_time'][:10]}] {n['title']}：{n['summary'][:120]}"
            for i, n in enumerate(news[:12])
        ]
        user_prompt = f"股票：{name or symbol}（{symbol}）\n近期新闻：\n" + "\n".join(lines)

        # 提示词从 prompt_service 取（用户可在设置中心自定义）
        data = await self._ai.chat_json(prompt_service.render("sentiment"), user_prompt)
        score = max(0, min(100, int(data.get("score", 50))))
        label = str(data.get("label", "中性"))
        if label not in ("利好", "利空", "中性"):
            label = "利好" if score > 70 else ("利空" if score < 30 else "中性")

        today = date.today().isoformat()
        titles = [n["title"] for n in news[:12]]
        with create_session() as db:
            row = NewsSentiment(
                symbol=symbol,
                trade_date=today,
                score=score,
                label=label,
                summary=str(data.get("summary", ""))[:200],
                points_json=json.dumps(
                    {
                        "positive": list(data.get("positive", []))[:5],
                        "negative": list(data.get("negative", []))[:5],
                    },
                    ensure_ascii=False,
                ),
                news_json=json.dumps(titles, ensure_ascii=False),
            )
            db.add(row)
            db.commit()
            result = self._row_to_dict(row)
        logger.info("AI 情绪分析完成：%s score=%d label=%s", symbol, score, label)
        return {**result, "from_cache": False}


def build_market_text(store, news_titles: list[str]) -> str:
    """组装盘面摘要的 LLM 输入文本：涨跌统计 + 板块两端 + 量能水位 + 重点快讯。

    放在服务层供 API 与定时任务共用；全部数值由库内统计算好喂给
    LLM（与诊股工作流同一设计原则：模型只解读、不算术）。
    store 为 MarketStore 实例（鸭子类型，避免循环导入）。
    """
    ov = store.eod_market_overview()
    boards = store.eod_board_heat("industry", 60)

    # 板块按涨跌幅排序取两端（领涨/领跌各 5 个）
    sorted_boards = sorted(boards, key=lambda b: b.get("pct_change", 0), reverse=True)
    top = "；".join(f"{b['name']} {b['pct_change']:+.2f}%" for b in sorted_boards[:5])
    bottom = "；".join(f"{b['name']} {b['pct_change']:+.2f}%" for b in sorted_boards[-5:])

    # 30 日成交趋势的尾部对比（今日量能相对近 5 日均值的水位）
    trend = store.amount_trend(30)
    amounts = [t["amount_yi"] for t in trend] if trend else []
    recent_avg = sum(amounts[-6:-1]) / 5 if len(amounts) >= 6 else 0
    vol_note = ""
    if recent_avg > 0 and amounts:
        ratio = amounts[-1] / recent_avg
        level = "放量" if ratio > 1.05 else "缩量" if ratio < 0.95 else "持平"
        vol_note = f"（较近5日均值 {level} {abs(ratio - 1) * 100:.0f}%）"

    lines = [
        f"交易日：{ov['trade_date']}",
        f"涨跌家数：上涨 {ov['up']} 家 / 下跌 {ov['down']} 家 / 平盘 {ov['flat']} 家",
        f"涨停 {ov['limit_up']} 家，跌停 {ov['limit_down']} 家",
        f"两市成交额：{ov['total_amount'] / 1e12:.2f} 万亿元{vol_note}",
        f"领涨行业：{top}",
        f"领跌行业：{bottom}",
    ]
    if news_titles:
        lines.append("今日重点快讯：")
        lines.extend(f"- {t}" for t in news_titles[:10])
    return "\n".join(lines)
