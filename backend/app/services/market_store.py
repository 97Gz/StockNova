"""行情数据仓库：DuckDB 的唯一读写入口（服务层不直接写 SQL 到处飞）。

线程模型说明：
- DuckDB 连接对象不是线程安全的，本仓库用一把互斥锁串行化全部操作；
- 同步服务在 asyncio 里通过 asyncio.to_thread 调用这里的同步方法，
  既不阻塞事件循环，又保证对 DuckDB 的访问串行。
- 写入用 pandas DataFrame 注册成临时视图后 INSERT ... SELECT，
  这是 DuckDB 官方推荐的批量写入方式（比逐行 INSERT 快几个量级）。
"""

import logging
import threading
from datetime import datetime

import duckdb
import pandas as pd

from app.adapters.base import BoardInfo, DailyBar, Quote, StockBasic
from app.core.database import get_duckdb

logger = logging.getLogger(__name__)

# K 线类数据帧的统一列顺序（与 daily_bars/index_daily/board_daily 表结构对应）
_BAR_COLUMNS = [
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "pct_change",
    "turnover",
]


def _bars_to_df(bars: list[DailyBar], *, with_factor: bool) -> pd.DataFrame:
    """DailyBar 列表 → DataFrame（trade_date 转为日期类型）。"""
    df = pd.DataFrame(
        {
            "symbol": [b.symbol for b in bars],
            "trade_date": pd.to_datetime([b.trade_date for b in bars]),
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            "amount": [b.amount for b in bars],
            "pct_change": [b.pct_change for b in bars],
            "turnover": [b.turnover for b in bars],
        }
    )
    if with_factor:
        df["adj_factor"] = [b.adj_factor for b in bars]
    return df


class MarketStore:
    """DuckDB 行情仓库。应用内全局单例（main.py 生命周期里创建/关闭）。"""

    def __init__(self) -> None:
        self._conn: duckdb.DuckDBPyConnection = get_duckdb()
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- 内部工具 ----------------

    def _insert_df(self, table: str, df: pd.DataFrame) -> None:
        """DataFrame 批量写入表（调用方负责先清理旧数据避免重复）。"""
        if df.empty:
            return
        with self._lock:
            self._conn.register("_incoming", df)
            cols = ", ".join(df.columns)
            self._conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _incoming")
            self._conn.unregister("_incoming")

    # ---------------- 股票主档 ----------------

    def upsert_stock_basics(self, basics: list[StockBasic]) -> None:
        """全量刷新主档：保留已退市股票（status 置 D），新股票插入。

        防幸存者偏差的关键：退市股票不能删，否则回测时"买过它"的事实会消失。
        做法：本次名单里没有、但库里有的股票 → status 标记为 D（退市）。
        """
        df = pd.DataFrame(
            {
                "symbol": [b.symbol for b in basics],
                "name": [b.name for b in basics],
                "exchange": [b.exchange for b in basics],
                "market": [b.market for b in basics],
                "pinyin": [b.pinyin for b in basics],
                "is_st": ["ST" in b.name.upper() for b in basics],
                "status": ["L"] * len(basics),
                "updated_at": [datetime.now()] * len(basics),
            }
        )
        with self._lock:
            self._conn.register("_basics", df)
            # 老股票標退市：在库但不在本次名单 → D
            self._conn.execute(
                """
                UPDATE stock_basics SET status = 'D'
                WHERE symbol NOT IN (SELECT symbol FROM _basics)
                """
            )
            # 删掉名单内旧记录后整批插入（等效 upsert）
            self._conn.execute(
                "DELETE FROM stock_basics WHERE symbol IN (SELECT symbol FROM _basics)"
            )
            self._conn.execute("INSERT INTO stock_basics SELECT * FROM _basics")
            self._conn.unregister("_basics")

    def get_symbols(self, *, listed_only: bool = True) -> list[str]:
        """全部股票代码（默认只取在市的）。"""
        sql = "SELECT symbol FROM stock_basics"
        if listed_only:
            sql += " WHERE status = 'L'"
        with self._lock:
            return [r[0] for r in self._conn.execute(sql + " ORDER BY symbol").fetchall()]

    # ---------------- 日线 ----------------

    def replace_symbol_bars(self, symbol: str, bars: list[DailyBar]) -> None:
        """重写一只股票的全部日线（初始化/除权修复用）：先删后插保证无重复。"""
        with self._lock:
            self._conn.execute("DELETE FROM daily_bars WHERE symbol = ?", [symbol])
        self._insert_df("daily_bars", _bars_to_df(bars, with_factor=True))

    def append_eod_bars(self, bars: list[DailyBar], trade_date: str) -> None:
        """追加一个交易日的全市场日线（每日增量用）：先删当日再插，幂等可重跑。"""
        with self._lock:
            self._conn.execute("DELETE FROM daily_bars WHERE trade_date = ?", [trade_date])
        self._insert_df("daily_bars", _bars_to_df(bars, with_factor=True))

    def get_last_bar_states(self) -> dict[str, tuple[str, float, float]]:
        """每只股票最后一根 K 线的（日期, 收盘价, 复权因子）。

        每日增量的两件事都靠它：
        1. 判断当天数据是否已同步过；
        2. 检测除权：快照昨收 ≠ 库里最后收盘 → 该股发生除权，需重拉因子。
        """
        sql = """
            SELECT b.symbol, strftime(b.trade_date, '%Y-%m-%d'), b.close, b.adj_factor
            FROM daily_bars b
            JOIN (
                SELECT symbol, max(trade_date) AS md FROM daily_bars GROUP BY symbol
            ) last ON b.symbol = last.symbol AND b.trade_date = last.md
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return {r[0]: (r[1], r[2], r[3]) for r in rows}

    # ---------------- 指数 / 板块 ----------------

    def replace_index_daily(self, symbol: str, bars: list[DailyBar]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM index_daily WHERE symbol = ?", [symbol])
        self._insert_df("index_daily", _bars_to_df(bars, with_factor=False))

    def replace_board_daily(self, board_code: str, bars: list[DailyBar]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM board_daily WHERE symbol = ?", [board_code])
        self._insert_df("board_daily", _bars_to_df(bars, with_factor=False))

    def replace_boards(self, boards: list[BoardInfo]) -> None:
        df = pd.DataFrame(
            {
                "code": [b.code for b in boards],
                "name": [b.name for b in boards],
                "type": [b.type for b in boards],
                "updated_at": [datetime.now()] * len(boards),
            }
        )
        with self._lock:
            self._conn.execute("DELETE FROM boards")
        self._insert_df("boards", df)

    def replace_board_members(self, board_code: str, symbols: list[str]) -> None:
        df = pd.DataFrame({"board_code": [board_code] * len(symbols), "symbol": symbols})
        with self._lock:
            self._conn.execute("DELETE FROM board_members WHERE board_code = ?", [board_code])
        self._insert_df("board_members", df)

    def list_boards(self) -> list[tuple[str, str, str]]:
        """全部板块（code, name, type）。"""
        with self._lock:
            return self._conn.execute("SELECT code, name, type FROM boards").fetchall()

    def board_codes_with_data(self) -> set[str]:
        """成分与日线都已落库的板块代码集合——板块阶段断点续传的依据。

        初始化被中断后重跑时跳过这些板块（与日线的 init_progress 表
        同思想）。判据必须是"两类数据都有"：限流时可能成分成功而
        日线失败，只查成分会把日线缺口永久掩盖。
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT DISTINCT board_code FROM board_members
                INTERSECT
                SELECT DISTINCT symbol FROM board_daily
                """
            ).fetchall()
        return {r[0] for r in rows}

    # ---------------- 估值快照 ----------------

    def append_fundamentals(self, quotes: list[Quote], trade_date: str) -> None:
        """落一个交易日的全市场估值（PE/PB/市值），幂等：先删当日。"""
        df = pd.DataFrame(
            {
                "symbol": [q.symbol for q in quotes],
                "trade_date": pd.to_datetime([trade_date] * len(quotes)),
                "pe_ttm": [q.pe_ttm for q in quotes],
                "pb": [q.pb for q in quotes],
                "total_mv": [q.total_mv for q in quotes],
                "circ_mv": [q.circ_mv for q in quotes],
                "turnover": [q.turnover for q in quotes],
            }
        )
        with self._lock:
            self._conn.execute("DELETE FROM fundamentals_daily WHERE trade_date = ?", [trade_date])
        self._insert_df("fundamentals_daily", df)

    # ---------------- 交易日历 ----------------

    def replace_trade_calendar(self, dates: list[str]) -> None:
        df = pd.DataFrame({"trade_date": pd.to_datetime(dates)})
        with self._lock:
            self._conn.execute("DELETE FROM trade_calendar")
        self._insert_df("trade_calendar", df)

    def load_trade_dates(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT strftime(trade_date, '%Y-%m-%d') FROM trade_calendar ORDER BY trade_date"
            ).fetchall()
        return [r[0] for r in rows]

    # ---------------- 行情查询（M2 行情中心） ----------------

    def get_stock_basic(self, symbol: str) -> dict | None:
        """单只股票主档（个股页头部信息）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT symbol, name, exchange, market, pinyin, is_st, status"
                " FROM stock_basics WHERE symbol = ?",
                [symbol],
            ).fetchone()
        if row is None:
            return None
        keys = ["symbol", "name", "exchange", "market", "pinyin", "is_st", "status"]
        return dict(zip(keys, row, strict=True))

    def search_stocks(self, keyword: str, limit: int = 20) -> list[dict]:
        """代码 / 名称 / 拼音首字母模糊搜索（全局 Ctrl+K 搜索框）。

        匹配优先级：代码前缀 > 拼音前缀 > 名称包含，用 CASE 排序实现，
        让"600"优先出 600 开头的代码、"gzmt"直达贵州茅台。
        """
        kw = keyword.strip().lower()
        if not kw:
            return []
        like = f"%{kw}%"
        prefix = f"{kw}%"
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT symbol, name, market, pinyin FROM stock_basics
                WHERE status = 'L'
                  AND (symbol LIKE ? OR lower(pinyin) LIKE ? OR name LIKE ?)
                ORDER BY CASE
                    WHEN symbol LIKE ? THEN 0
                    WHEN lower(pinyin) LIKE ? THEN 1
                    ELSE 2 END,
                    symbol
                LIMIT ?
                """,
                [like, like, like, prefix, prefix, limit],
            ).fetchall()
        return [{"symbol": r[0], "name": r[1], "market": r[2], "pinyin": r[3]} for r in rows]

    def query_daily_bars(
        self, symbol: str, limit: int = 500, as_of: str | None = None
    ) -> list[dict]:
        """一只股票最近 N 根日线（日期升序），含前复权价。

        前复权公式：qfq_price = raw_price × adj_factor ÷ 锚定日 adj_factor。
        这样锚定日 K 线价格 = 真实市价，历史价格按除权比例缩放——
        与主流行情软件（同花顺/东财）的"前复权"显示一致。

        as_of（YYYY-MM-DD）非空 = 回溯模式：只取该日（含）之前的 K 线，
        且复权锚定到 as_of 当日因子（让 as_of 那根 = 当时真实市价），
        供"指定历史节点诊股"使用——严格不含未来数据。
        """
        # 锚定日与截断条件：回溯模式锚定 as_of，否则锚定全历史最新
        cutoff = "AND trade_date <= ?" if as_of else ""
        latest_params = [symbol] + ([as_of] if as_of else [])
        recent_params = [symbol] + ([as_of] if as_of else []) + [limit]
        with self._lock:
            rows = self._conn.execute(
                f"""
                WITH latest AS (
                    SELECT adj_factor AS lf FROM daily_bars
                    WHERE symbol = ? {cutoff} ORDER BY trade_date DESC LIMIT 1
                ),
                recent AS (
                    SELECT * FROM daily_bars WHERE symbol = ? {cutoff}
                    ORDER BY trade_date DESC LIMIT ?
                )
                SELECT strftime(r.trade_date, '%Y-%m-%d'),
                       round(r.open * r.adj_factor / latest.lf, 3),
                       round(r.high * r.adj_factor / latest.lf, 3),
                       round(r.low * r.adj_factor / latest.lf, 3),
                       round(r.close * r.adj_factor / latest.lf, 3),
                       r.volume, r.amount, r.pct_change, r.turnover
                FROM recent r, latest
                ORDER BY r.trade_date
                """,
                latest_params + recent_params,
            ).fetchall()
        keys = [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "pct_change",
            "turnover",
        ]
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def query_symbol_panel_asof(
        self, symbol: str, as_of: str, days: int = 300
    ) -> pd.DataFrame:
        """单只股票截至 as_of（含）的前复权日线面板（回溯诊股的因子原料）。

        列与 query_market_panel 对齐（symbol/trade_date/OHLC/volume/amount/
        pct_change/turnover），可直接喂给 compute_factor_table 复算 as_of 当日
        的全部技术因子——因为面板止于 as_of，所有滚动/平滑指标天然无未来泄露。
        复权锚定到 as_of 当日因子，使 as_of 收盘价 = 当时真实市价。
        """
        with self._lock:
            df = self._conn.execute(
                """
                WITH anchor AS (
                    SELECT adj_factor AS lf FROM daily_bars
                    WHERE symbol = ? AND trade_date <= ?
                    ORDER BY trade_date DESC LIMIT 1
                ),
                recent AS (
                    SELECT * FROM daily_bars WHERE symbol = ? AND trade_date <= ?
                    ORDER BY trade_date DESC LIMIT ?
                )
                SELECT r.symbol,
                       r.trade_date,
                       r.open  * r.adj_factor / anchor.lf AS open,
                       r.high  * r.adj_factor / anchor.lf AS high,
                       r.low   * r.adj_factor / anchor.lf AS low,
                       r.close * r.adj_factor / anchor.lf AS close,
                       r.volume, r.amount, r.pct_change, r.turnover
                FROM recent r, anchor
                ORDER BY r.trade_date
                """,
                [symbol, as_of, symbol, as_of, days],
            ).df()
        return df

    def fundamentals_asof_df(self, symbol: str, as_of: str) -> pd.DataFrame:
        """某股截至 as_of 的最近一期估值快照（symbol, pe_ttm, pb, total_mv）。

        估值快照自系统上线日起逐日积累，as_of 早于上线则返回空表
        （compute_factor_table 对空基本面按 NaN 处理，如实呈现"无数据"）。
        """
        with self._lock:
            return self._conn.execute(
                """
                SELECT symbol, pe_ttm, pb, total_mv
                FROM fundamentals_daily
                WHERE symbol = ? AND trade_date <= ?
                ORDER BY trade_date DESC LIMIT 1
                """,
                [symbol, as_of],
            ).df()

    def basics_one_df(self, symbol: str) -> pd.DataFrame:
        """单股主档（symbol, name, is_st）——回溯诊股给 compute_factor_table 用。"""
        with self._lock:
            return self._conn.execute(
                "SELECT symbol, name, is_st FROM stock_basics WHERE symbol = ?",
                [symbol],
            ).df()

    def forward_path(self, symbol: str, as_of: str, limit: int = 60) -> list[dict]:
        """as_of 之后的真实日线走势（回测校验用，日期升序）。

        复权锚定到 as_of 当日因子，使价格与 as_of 诊断时的价位（目标价/止损价）
        处于同一口径，可直接比较"AI 当时的预测 vs 后续真实涨跌"。
        返回 date/open/high/low/close/pct_change。
        """
        with self._lock:
            rows = self._conn.execute(
                """
                WITH anchor AS (
                    SELECT adj_factor AS lf FROM daily_bars
                    WHERE symbol = ? AND trade_date <= ?
                    ORDER BY trade_date DESC LIMIT 1
                )
                SELECT strftime(d.trade_date, '%Y-%m-%d'),
                       round(d.open  * d.adj_factor / anchor.lf, 3),
                       round(d.high  * d.adj_factor / anchor.lf, 3),
                       round(d.low   * d.adj_factor / anchor.lf, 3),
                       round(d.close * d.adj_factor / anchor.lf, 3),
                       d.pct_change
                FROM daily_bars d, anchor
                WHERE d.symbol = ? AND d.trade_date > ?
                ORDER BY d.trade_date LIMIT ?
                """,
                [symbol, as_of, symbol, as_of, limit],
            ).fetchall()
        keys = ["date", "open", "high", "low", "close", "pct_change"]
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def latest_bar_date(self, symbol: str) -> str | None:
        """某股最新一根日线的日期（YYYY-MM-DD），无数据返回 None。回溯日期校验用。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT strftime(max(trade_date), '%Y-%m-%d') FROM daily_bars WHERE symbol = ?",
                [symbol],
            ).fetchone()
        return row[0] if row and row[0] else None

    def query_index_daily(
        self, symbol: str, limit: int = 500, as_of: str | None = None
    ) -> list[dict]:
        """指数最近 N 根日线（日期升序，无复权概念）。as_of 非空则截断到该日（回溯）。"""
        cutoff = "AND trade_date <= ?" if as_of else ""
        params = [symbol] + ([as_of] if as_of else []) + [limit]
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM (
                    SELECT strftime(trade_date, '%Y-%m-%d') AS d,
                           open, high, low, close, volume, amount, pct_change
                    FROM index_daily WHERE symbol = ? {cutoff}
                    ORDER BY trade_date DESC LIMIT ?
                ) ORDER BY d
                """,
                params,
            ).fetchall()
        keys = ["date", "open", "high", "low", "close", "volume", "amount", "pct_change"]
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def eod_market_overview(self) -> dict:
        """最新交易日的全市场盘面统计（涨跌分布 + 涨跌停 + 总成交额）。

        分布桶（A 股习惯）：跌停 / <-7 / -7~-5 / -5~-2 / -2~0 / 平 /
        0~2 / 2~5 / 5~7 / >7 / 涨停。涨跌停按 ±9.8% 近似（不细分板块规则）。
        """
        with self._lock:
            day_row = self._conn.execute("SELECT max(trade_date) FROM daily_bars").fetchone()
            if not day_row or day_row[0] is None:
                return {"trade_date": None, "buckets": [], "up": 0, "down": 0, "flat": 0}
            day = day_row[0]
            rows = self._conn.execute(
                """
                SELECT
                  count(*) FILTER (pct_change <= -9.8)                          AS limit_down,
                  count(*) FILTER (pct_change > -9.8 AND pct_change <= -7)      AS d7,
                  count(*) FILTER (pct_change > -7 AND pct_change <= -5)        AS d5,
                  count(*) FILTER (pct_change > -5 AND pct_change <= -2)        AS d2,
                  count(*) FILTER (pct_change > -2 AND pct_change < 0)          AS d0,
                  count(*) FILTER (pct_change = 0)                              AS flat,
                  count(*) FILTER (pct_change > 0 AND pct_change < 2)           AS u0,
                  count(*) FILTER (pct_change >= 2 AND pct_change < 5)          AS u2,
                  count(*) FILTER (pct_change >= 5 AND pct_change < 7)          AS u5,
                  count(*) FILTER (pct_change >= 7 AND pct_change < 9.8)        AS u7,
                  count(*) FILTER (pct_change >= 9.8)                           AS limit_up,
                  count(*) FILTER (pct_change > 0)                              AS up_total,
                  count(*) FILTER (pct_change < 0)                              AS down_total,
                  sum(amount)                                                   AS total_amount
                FROM daily_bars WHERE trade_date = ?
                """,
                [day],
            ).fetchone()
        labels = [
            "跌停",
            "-9~-7%",
            "-7~-5%",
            "-5~-2%",
            "-2~0%",
            "平盘",
            "0~2%",
            "2~5%",
            "5~7%",
            "7~9%",
            "涨停",
        ]
        counts = list(rows[0:11])
        return {
            "trade_date": str(day)[:10],
            "buckets": [
                {"label": label, "count": cnt} for label, cnt in zip(labels, counts, strict=True)
            ],
            "up": rows[11],
            "down": rows[12],
            "flat": rows[5],
            "limit_up": rows[10],
            "limit_down": rows[0],
            "total_amount": rows[13] or 0,
        }

    def eod_board_heat(self, board_type: str = "industry", limit: int = 40) -> list[dict]:
        """最新交易日板块涨跌排行（热力图数据源）。

        amount 用于 treemap 面积权重，pct_change 决定颜色深浅。
        """
        with self._lock:
            rows = self._conn.execute(
                """
                WITH latest AS (SELECT max(trade_date) AS d FROM board_daily)
                SELECT b.code, b.name, bd.pct_change, bd.amount
                FROM board_daily bd
                JOIN boards b ON b.code = bd.symbol
                JOIN latest ON bd.trade_date = latest.d
                WHERE b.type = ?
                ORDER BY bd.amount DESC
                LIMIT ?
                """,
                [board_type, limit],
            ).fetchall()
        return [{"code": r[0], "name": r[1], "pct_change": r[2], "amount": r[3]} for r in rows]

    def amount_trend(self, days: int = 30) -> list[dict]:
        """近 N 个交易日两市总成交额（亿元），用于成交额趋势小图。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM (
                    SELECT strftime(trade_date, '%Y-%m-%d') AS d,
                           round(sum(amount) / 1e8, 0) AS yi
                    FROM daily_bars GROUP BY trade_date
                    ORDER BY trade_date DESC LIMIT ?
                ) ORDER BY d
                """,
                [days],
            ).fetchall()
        return [{"date": r[0], "amount_yi": r[1]} for r in rows]

    def stock_board_names(self, symbol: str) -> list[dict]:
        """个股所属板块（行业 + 概念，个股页展示用）。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT b.code, b.name, b.type
                FROM board_members m JOIN boards b ON b.code = m.board_code
                WHERE m.symbol = ?
                ORDER BY b.type, b.code
                """,
                [symbol],
            ).fetchall()
        return [{"code": r[0], "name": r[1], "type": r[2]} for r in rows]

    def latest_fundamentals(self, symbol: str) -> dict | None:
        """个股最新估值快照（PE/PB/市值）。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT strftime(trade_date, '%Y-%m-%d'), pe_ttm, pb, total_mv, circ_mv
                FROM fundamentals_daily WHERE symbol = ?
                ORDER BY trade_date DESC LIMIT 1
                """,
                [symbol],
            ).fetchone()
        if row is None:
            return None
        keys = ["trade_date", "pe_ttm", "pb", "total_mv", "circ_mv"]
        return dict(zip(keys, row, strict=True))

    # ---------------- 策略引擎查询（M3） ----------------

    def query_market_panel(self, days: int = 300) -> pd.DataFrame:
        """全市场近 N 个交易日的前复权日线面板（策略引擎的原料）。

        返回按 (symbol, trade_date) 排序的 DataFrame，
        价格列已按"最新因子锚定"做前复权——均线/形态计算必须用复权价，
        否则除权日的跳空会污染所有指标。
        """
        with self._lock:
            df = self._conn.execute(
                """
                WITH anchor AS (
                    SELECT symbol, arg_max(adj_factor, trade_date) AS lf
                    FROM daily_bars GROUP BY symbol
                ),
                cutoff AS (
                    SELECT min(d) AS start FROM (
                        SELECT DISTINCT trade_date AS d FROM daily_bars
                        ORDER BY trade_date DESC LIMIT ?
                    )
                )
                SELECT d.symbol,
                       d.trade_date,
                       d.open  * d.adj_factor / a.lf AS open,
                       d.high  * d.adj_factor / a.lf AS high,
                       d.low   * d.adj_factor / a.lf AS low,
                       d.close * d.adj_factor / a.lf AS close,
                       d.volume, d.amount, d.pct_change, d.turnover
                FROM daily_bars d
                JOIN anchor a USING (symbol), cutoff
                WHERE d.trade_date >= cutoff.start
                ORDER BY d.symbol, d.trade_date
                """,
                [days],
            ).df()
        return df

    def basics_df(self) -> pd.DataFrame:
        """在市股票主档 DataFrame（symbol, name, is_st）。"""
        with self._lock:
            return self._conn.execute(
                "SELECT symbol, name, is_st FROM stock_basics WHERE status = 'L'"
            ).df()

    def fundamentals_df(self) -> pd.DataFrame:
        """最新一期估值快照 DataFrame（symbol, pe_ttm, pb, total_mv）。"""
        with self._lock:
            return self._conn.execute(
                """
                SELECT symbol, pe_ttm, pb, total_mv
                FROM fundamentals_daily
                WHERE trade_date = (SELECT max(trade_date) FROM fundamentals_daily)
                """
            ).df()

    def board_recent_perf(self, board_type: str = "industry", days: int = 12) -> pd.DataFrame:
        """板块近 N 日涨跌面板（code, name, trade_date, pct_change）——轮动/龙头策略用。"""
        with self._lock:
            return self._conn.execute(
                """
                WITH cutoff AS (
                    SELECT min(d) AS start FROM (
                        SELECT DISTINCT trade_date AS d FROM board_daily
                        ORDER BY trade_date DESC LIMIT ?
                    )
                )
                SELECT b.code, b.name, bd.trade_date, bd.pct_change
                FROM board_daily bd
                JOIN boards b ON b.code = bd.symbol, cutoff
                WHERE b.type = ? AND bd.trade_date >= cutoff.start
                ORDER BY b.code, bd.trade_date
                """,
                [days, board_type],
            ).df()

    def boards_of_symbol(self, symbol: str, board_type: str = "industry") -> list[dict]:
        """某股票所属板块列表（默认只取行业板块）。

        诊股「板块/同业」分析师用：先定位个股的行业归属，再据此取板块
        近期表现与同业成分做龙头地位对比。返回 [{code, name}, ...]。
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT b.code, b.name
                FROM board_members m
                JOIN boards b ON b.code = m.board_code
                WHERE m.symbol = ? AND b.type = ?
                """,
                [symbol, board_type],
            ).fetchall()
        return [{"code": c, "name": n} for c, n in rows]

    def board_members_map(self, board_codes: list[str]) -> dict[str, list[str]]:
        """若干板块的成分股映射 {board_code: [symbol, ...]}。"""
        if not board_codes:
            return {}
        placeholders = ", ".join("?" for _ in board_codes)
        sql = f"SELECT board_code, symbol FROM board_members WHERE board_code IN ({placeholders})"
        with self._lock:
            rows = self._conn.execute(sql, board_codes).fetchall()
        out: dict[str, list[str]] = {}
        for code, symbol in rows:
            out.setdefault(code, []).append(symbol)
        return out

    def board_detail(self, code: str) -> dict | None:
        """板块详情：板块概览 + 近10日走势 + 成分股最新行情（含资金流）。

        供「板块详情」专页使用：一次拿齐板块当日表现、近期走势曲线，
        以及成分股的现价/涨跌/成交额/换手/主力净额，前端按涨幅排序展示龙头。
        返回 None 表示板块不存在。
        """
        with self._lock:
            board = self._conn.execute(
                "SELECT code, name, type FROM boards WHERE code = ?", [code]
            ).fetchone()
            if board is None:
                return None

            # 板块当日表现（最新一根 board_daily）
            latest = self._conn.execute(
                """
                SELECT strftime(trade_date, '%Y-%m-%d'), pct_change, amount
                FROM board_daily WHERE symbol = ?
                ORDER BY trade_date DESC LIMIT 1
                """,
                [code],
            ).fetchone()

            # 近 10 日板块走势（涨跌幅曲线）
            trend_rows = self._conn.execute(
                """
                SELECT strftime(trade_date, '%Y-%m-%d') AS d, pct_change
                FROM board_daily WHERE symbol = ?
                ORDER BY trade_date DESC LIMIT 10
                """,
                [code],
            ).fetchall()

            # 成分股最新行情 + 当日主力资金净额（左连资金流表，缺失为 NULL）
            members = self._conn.execute(
                """
                WITH latest AS (SELECT max(trade_date) AS d FROM daily_bars)
                SELECT s.symbol, s.name, s.is_st,
                       d.close, d.pct_change, d.amount, d.turnover,
                       ff.main_net
                FROM board_members m
                JOIN stock_basics s ON s.symbol = m.symbol
                JOIN latest ON TRUE
                JOIN daily_bars d ON d.symbol = m.symbol AND d.trade_date = latest.d
                LEFT JOIN fund_flow_daily ff
                       ON ff.symbol = m.symbol AND ff.trade_date = latest.d
                WHERE m.board_code = ?
                ORDER BY d.pct_change DESC
                """,
                [code],
            ).fetchall()

        member_list = [
            {
                "symbol": r[0],
                "name": r[1],
                "is_st": bool(r[2]),
                "close": r[3],
                "pct_change": r[4],
                "amount": r[5],
                "turnover": r[6],
                "main_net": r[7],
            }
            for r in members
        ]
        return {
            "code": board[0],
            "name": board[1],
            "type": board[2],
            "trade_date": latest[0] if latest else None,
            "pct_change": latest[1] if latest else None,
            "amount": latest[2] if latest else None,
            "trend": [{"date": d, "pct_change": p} for d, p in reversed(trend_rows)],
            "members": member_list,
        }

    # ---------------- 回测引擎查询（M4） ----------------

    def trade_dates_between(self, start: str, end: str) -> list[str]:
        """区间内实际有行情的交易日列表（升序）。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT DISTINCT strftime(trade_date, '%Y-%m-%d') AS d FROM daily_bars
                WHERE trade_date BETWEEN ? AND ? ORDER BY d
                """,
                [start, end],
            ).fetchall()
        return [r[0] for r in rows]

    def warmup_start_date(self, start: str, warmup_days: int = 370) -> str:
        """给定起始日，向前回退 N 个交易日的日期（指标 warmup 用）。

        库里最早数据不足 N 日时返回最早交易日。
        """
        with self._lock:
            row = self._conn.execute(
                """
                SELECT strftime(min(d), '%Y-%m-%d') FROM (
                    SELECT DISTINCT trade_date AS d FROM daily_bars
                    WHERE trade_date <= ? ORDER BY d DESC LIMIT ?
                )
                """,
                [start, warmup_days],
            ).fetchone()
        return row[0] or start

    def query_market_panel_between(self, start: str, end: str) -> pd.DataFrame:
        """任意日期区间的全市场 QFQ 日线面板（回测引擎的原料）。

        与 query_market_panel 相同的前复权口径（最新因子锚定），
        区别是按日期区间取数而非"最近 N 日"。
        """
        with self._lock:
            df = self._conn.execute(
                """
                WITH anchor AS (
                    SELECT symbol, arg_max(adj_factor, trade_date) AS lf
                    FROM daily_bars GROUP BY symbol
                )
                SELECT d.symbol,
                       d.trade_date,
                       d.open  * d.adj_factor / a.lf AS open,
                       d.high  * d.adj_factor / a.lf AS high,
                       d.low   * d.adj_factor / a.lf AS low,
                       d.close * d.adj_factor / a.lf AS close,
                       d.volume, d.amount, d.pct_change, d.turnover
                FROM daily_bars d
                JOIN anchor a USING (symbol)
                WHERE d.trade_date BETWEEN ? AND ?
                ORDER BY d.symbol, d.trade_date
                """,
                [start, end],
            ).df()
        return df

    def index_close_series(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """指数收盘序列（基准曲线用），列：date / close。"""
        with self._lock:
            df = self._conn.execute(
                """
                SELECT strftime(trade_date, '%Y-%m-%d') AS date, close
                FROM index_daily
                WHERE symbol = ? AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
                """,
                [symbol, start, end],
            ).df()
        return df

    # ---------------- 扩展数据（M5：资金流/龙虎榜/业绩预告/人气榜） ----------------

    def replace_fund_flow(self, trade_date: str, rows: list[dict]) -> None:
        """覆盖写入一个交易日的全市场资金流快照（先删后插，幂等可重跑）。"""
        if not rows:
            return
        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(trade_date)
        with self._lock:
            self._conn.execute("DELETE FROM fund_flow_daily WHERE trade_date = ?", [trade_date])
        self._insert_df("fund_flow_daily", df)

    def replace_dragon_tiger(self, start: str, end: str, rows: list[dict]) -> None:
        """覆盖写入一个日期区间的龙虎榜明细。"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM dragon_tiger WHERE trade_date BETWEEN ? AND ?", [start, end]
            )
        if not rows:
            return
        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        self._insert_df("dragon_tiger", df)

    def replace_earnings_forecast(self, report_date: str, rows: list[dict]) -> None:
        """覆盖写入一个报告期的业绩预告。"""
        with self._lock:
            self._conn.execute("DELETE FROM earnings_forecast WHERE report_date = ?", [report_date])
        if not rows:
            return
        df = pd.DataFrame(rows)
        df["report_date"] = pd.to_datetime(df["report_date"])
        df["notice_date"] = pd.to_datetime(df["notice_date"])
        self._insert_df("earnings_forecast", df)

    def replace_popularity(self, rank_date: str, rows: list[dict]) -> None:
        """覆盖写入一天的人气榜快照。"""
        if not rows:
            return
        df = pd.DataFrame(rows)
        df["rank_date"] = pd.to_datetime(rank_date)
        with self._lock:
            self._conn.execute("DELETE FROM popularity_rank WHERE rank_date = ?", [rank_date])
        self._insert_df("popularity_rank", df)

    def fund_flow_df(self) -> pd.DataFrame:
        """最新一日资金流快照（策略因子原料）。"""
        with self._lock:
            return self._conn.execute(
                """
                SELECT symbol, main_net, main_pct, net_3d, net_5d, net_10d, dv_ttm
                FROM fund_flow_daily
                WHERE trade_date = (SELECT max(trade_date) FROM fund_flow_daily)
                """
            ).df()

    def dragon_tiger_agg_df(self, days: int = 3) -> pd.DataFrame:
        """近 N 个自然日的龙虎榜聚合：每股净买额合计 + 是否有机构买入。"""
        with self._lock:
            return self._conn.execute(
                """
                WITH recent AS (
                    SELECT max(trade_date) AS md FROM dragon_tiger
                )
                SELECT symbol,
                       sum(net_amt)       AS dt_net_amt,
                       max(has_inst::INT) AS dt_has_inst
                FROM dragon_tiger, recent
                WHERE trade_date > recent.md - INTERVAL (?) DAY
                GROUP BY symbol
                """,
                [days],
            ).df()

    def earnings_df(self) -> pd.DataFrame:
        """最新报告期业绩预告（每股取公告日最新一条）。"""
        with self._lock:
            return self._conn.execute(
                """
                WITH latest AS (
                    SELECT max(report_date) AS rd FROM earnings_forecast
                )
                SELECT symbol,
                       arg_max(predict_type, notice_date) AS predict_type,
                       arg_max(amp_lower, notice_date)    AS earn_amp_lower
                FROM earnings_forecast, latest
                WHERE report_date = latest.rd
                GROUP BY symbol
                """
            ).df()

    def popularity_df(self) -> pd.DataFrame:
        """最新一日人气榜（symbol, rank, rank_chg）。"""
        with self._lock:
            return self._conn.execute(
                """
                SELECT symbol, rank, rank_chg
                FROM popularity_rank
                WHERE rank_date = (SELECT max(rank_date) FROM popularity_rank)
                """
            ).df()

    def stock_fund_flow(
        self, symbol: str, days: int = 30, as_of: str | None = None
    ) -> list[dict]:
        """个股近 N 日资金流（个股详情页展示）。as_of 非空则只取该日之前（回溯诊股）。"""
        cutoff = "AND trade_date <= ?" if as_of else ""
        params = [symbol] + ([as_of] if as_of else []) + [days]
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT strftime(trade_date, '%Y-%m-%d') AS d,
                       main_net, main_pct, net_3d, net_5d, net_10d
                FROM fund_flow_daily WHERE symbol = ? {cutoff}
                ORDER BY trade_date DESC LIMIT ?
                """,
                params,
            ).fetchall()
        keys = ["date", "main_net", "main_pct", "net_3d", "net_5d", "net_10d"]
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def stock_dragon_tiger(
        self, symbol: str, limit: int = 10, as_of: str | None = None
    ) -> list[dict]:
        """个股最近上榜记录（个股详情页展示）。as_of 非空则只取该日之前（回溯诊股）。"""
        cutoff = "AND trade_date <= ?" if as_of else ""
        params = [symbol] + ([as_of] if as_of else []) + [limit]
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT strftime(trade_date, '%Y-%m-%d'), net_amt, reason
                FROM dragon_tiger WHERE symbol = ? {cutoff}
                ORDER BY trade_date DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [{"date": r[0], "net_amt": r[1], "reason": r[2]} for r in rows]

    def ext_stats(self) -> dict:
        """扩展数据各表的行数与最新数据日（同步状态展示用）。"""
        with self._lock:

            def table_stat(table: str, date_col: str) -> dict:
                row = self._conn.execute(
                    f"SELECT count(*), strftime(max({date_col}), '%Y-%m-%d') FROM {table}"
                ).fetchone()
                return {"rows": row[0], "latest": row[1]}

            return {
                "fund_flow": table_stat("fund_flow_daily", "trade_date"),
                "dragon_tiger": table_stat("dragon_tiger", "trade_date"),
                "earnings": table_stat("earnings_forecast", "notice_date"),
                "popularity": table_stat("popularity_rank", "rank_date"),
                "minute_bars": table_stat("minute_bars", "trade_date"),
            }

    # ---------------- 5 分钟K线（盘中形态因子原料） ----------------

    def replace_minute_bars(self, trade_date: str, df: pd.DataFrame) -> None:
        """覆盖写入一个交易日的全市场 5 分钟线（先删后插，幂等可重跑）。

        df 列：symbol, dt, trade_date, open, high, low, close, volume, amount
        （由同步服务从 MinuteBar 列表组装，分批调用以控制单次写入量）。
        """
        if df.empty:
            return
        with self._lock:
            self._conn.execute("DELETE FROM minute_bars WHERE trade_date = ?", [trade_date])
        self._insert_df("minute_bars", df)

    def append_minute_bars(self, df: pd.DataFrame) -> None:
        """追加分钟线（同步服务分批入库用；当日去重由调用方先调 delete 保证）。"""
        if df.empty:
            return
        self._insert_df("minute_bars", df)

    def delete_minute_bars_of(self, trade_date: str) -> None:
        """删除一个交易日的全部分钟线（分批入库前先清当日，保证幂等）。"""
        with self._lock:
            self._conn.execute("DELETE FROM minute_bars WHERE trade_date = ?", [trade_date])

    def minute_day_df(self, trade_date: str) -> pd.DataFrame:
        """一个交易日的全市场分钟线面板（盘中因子计算的原料）。"""
        with self._lock:
            return self._conn.execute(
                """
                SELECT symbol, dt, open, high, low, close, volume, amount
                FROM minute_bars WHERE trade_date = ?
                ORDER BY symbol, dt
                """,
                [trade_date],
            ).df()

    def minute_coverage_days(self) -> int:
        """库内分钟线覆盖的交易日数量（回测解锁判定：满 60 日开放盘中策略回测）。"""
        with self._lock:
            return self._conn.execute(
                "SELECT count(DISTINCT trade_date) FROM minute_bars"
            ).fetchone()[0]

    def stock_minute_bars(self, symbol: str, trade_date: str) -> list[dict]:
        """个股某交易日的分钟线（个股详情页分时图可用）。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT strftime(dt, '%H:%M') AS t, open, high, low, close, volume, amount
                FROM minute_bars WHERE symbol = ? AND trade_date = ?
                ORDER BY dt
                """,
                [symbol, trade_date],
            ).fetchall()
        keys = ["time", "open", "high", "low", "close", "volume", "amount"]
        return [dict(zip(keys, r, strict=True)) for r in rows]

    # ---------------- 统计与维护 ----------------

    def stats(self) -> dict:
        """数据管理页的库存统计。

        注意：所有查询必须在锁内完成后再组装结果。曾经的事故：count 调用
        写在 return 的字典构造里（锁已释放），与同步任务的写入并发操作
        同一个 DuckDB 连接，触发连接内部死锁，整个后端假死。
        """
        with self._lock:

            def count(table: str) -> int:
                return self._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

            bar_range = self._conn.execute(
                "SELECT strftime(min(trade_date), '%Y-%m-%d'),"
                " strftime(max(trade_date), '%Y-%m-%d') FROM daily_bars"
            ).fetchone()
            result = {
                "stocks": count("stock_basics"),
                "symbols_with_bars": self._conn.execute(
                    "SELECT count(DISTINCT symbol) FROM daily_bars"
                ).fetchone()[0],
                "daily_bars": count("daily_bars"),
                "index_daily": count("index_daily"),
                "boards": count("boards"),
                "board_members": count("board_members"),
                "board_daily": count("board_daily"),
                "fundamentals_daily": count("fundamentals_daily"),
                "trade_calendar": count("trade_calendar"),
                "bar_date_min": bar_range[0],
                "bar_date_max": bar_range[1],
            }
        return result

    def clear_market_data(self) -> None:
        """清空全部行情数据（设置中心"清库重建"按钮）。表结构保留。"""
        tables = [
            "stock_basics",
            "daily_bars",
            "index_daily",
            "boards",
            "board_members",
            "board_daily",
            "fundamentals_daily",
        ]
        with self._lock:
            for table in tables:
                self._conn.execute(f"DELETE FROM {table}")
        logger.warning("行情库已清空（清库重建）")
