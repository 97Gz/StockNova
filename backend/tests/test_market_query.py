"""M2 行情查询的单元测试：搜索 / K 线前复权 / 盘面统计（用内存数据构造）。"""

from app.adapters.base import DailyBar, StockBasic
from app.core.database import init_duckdb
from app.services.market_store import MarketStore


def make_store() -> MarketStore:
    """每个测试一个独立 store（conftest 已把数据目录指到临时位置）。"""
    init_duckdb()  # 建表幂等：单跑本文件时也能就绪
    return MarketStore()


def seed_basics(store: MarketStore) -> None:
    rows = [
        ("600519", "贵州茅台", "SH", "主板", "gzmt"),
        ("000001", "平安银行", "SZ", "主板", "payh"),
        ("300750", "宁德时代", "SZ", "创业板", "ndsd"),
    ]
    store.upsert_stock_basics(
        [StockBasic(symbol=s, name=n, exchange=e, market=m, pinyin=p) for s, n, e, m, p in rows]
    )


def bar(symbol: str, date: str, close: float, factor: float = 1.0) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=date,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=1000,
        amount=close * 1000,
        pct_change=1.0,
        turnover=0.5,
        adj_factor=factor,
    )


def test_search_priority() -> None:
    """搜索优先级：代码前缀 > 拼音前缀 > 名称包含。"""
    store = make_store()
    seed_basics(store)

    by_code = store.search_stocks("600")
    assert by_code[0]["symbol"] == "600519"

    by_pinyin = store.search_stocks("gzmt")
    assert by_pinyin[0]["name"] == "贵州茅台"

    by_name = store.search_stocks("宁德")
    assert by_name[0]["symbol"] == "300750"

    assert store.search_stocks("zzzz") == []


def test_kline_qfq() -> None:
    """前复权：最新一根价格 = 真实市价，历史按因子比例缩放。

    构造：除权前因子 1.0、收盘 10 元；除权后因子 2.0、收盘 6 元。
    前复权后历史收盘 = 10 × 1.0 ÷ 2.0 = 5 元（最新一根保持 6 元）。
    """
    store = make_store()
    store.replace_symbol_bars(
        "600519",
        [
            bar("600519", "2026-06-10", 10.0, factor=1.0),
            bar("600519", "2026-06-11", 6.0, factor=2.0),
        ],
    )
    bars = store.query_daily_bars("600519")
    assert len(bars) == 2
    assert bars[0]["close"] == 5.0  # 历史价被缩放
    assert bars[1]["close"] == 6.0  # 最新价 = 市价


def test_eod_overview_buckets() -> None:
    """涨跌分布桶：涨停/跌停/普通涨跌的归桶正确。"""
    store = make_store()
    samples = [
        ("000001", 10.01),  # 涨停（≥9.8）
        ("000002", 5.5),  # 5~7%
        ("000003", 0.0),  # 平盘
        ("000004", -3.0),  # -5~-2%
        ("000005", -9.9),  # 跌停
    ]
    store.append_eod_bars(
        [
            DailyBar(
                symbol=s,
                trade_date="2026-06-12",
                open=10,
                high=11,
                low=9,
                close=10,
                volume=100,
                amount=1e6,
                pct_change=pct,
                turnover=1.0,
                adj_factor=1.0,
            )
            for s, pct in samples
        ],
        "2026-06-12",
    )
    ov = store.eod_market_overview()
    assert ov["trade_date"] == "2026-06-12"
    assert ov["limit_up"] == 1
    assert ov["limit_down"] == 1
    assert ov["up"] == 2
    assert ov["down"] == 2
    assert ov["flat"] == 1
    buckets = {b["label"]: b["count"] for b in ov["buckets"]}
    assert buckets["涨停"] == 1
    assert buckets["5~7%"] == 1
    assert buckets["-5~-2%"] == 1


def test_watchlist_crud() -> None:
    """自选股增删改查 + 重复添加报错。"""
    import pytest

    from app.core.database import init_sqlite
    from app.core.exceptions import BizError
    from app.services import watchlist_service

    init_sqlite()
    # 清场（同一测试会话内可能多次运行）
    for s in watchlist_service.list_symbols():
        watchlist_service.remove(s)

    watchlist_service.add("600519")
    watchlist_service.add("300750", note="新能源")
    assert watchlist_service.list_symbols() == ["600519", "300750"]

    with pytest.raises(BizError):
        watchlist_service.add("600519")  # 重复

    watchlist_service.update_note("600519", "白酒龙头")
    items = watchlist_service.list_items()
    assert items[0]["note"] == "白酒龙头"

    watchlist_service.remove("600519")
    assert watchlist_service.list_symbols() == ["300750"]
