"""M5 扩展数据单元测试：适配器解析 / 报告期推导 / 扩展因子挂载。"""

from datetime import date

import numpy as np
import pandas as pd

from app.adapters.eastmoney_ext import _stock_codes, parse_fast_news, parse_stock_news
from app.services.ext_sync_service import current_report_dates
from app.strategy.factors import EXT_FACTORS, FACTOR_META, attach_ext_factors

# ---------------------------------------------------------------- 快讯解析


def test_parse_fast_news_basic():
    data = {
        "data": {
            "sortEnd": "1781279074058195",
            "fastNewsList": [
                {
                    "code": "20260612x",
                    "title": "标题A",
                    "summary": "摘要A",
                    "showTime": "2026-06-12 23:44:34",
                    "stockList": ["0.000032", "1.600519"],
                },
                {
                    "code": "20260612y",
                    "title": "标题B",
                    "summary": "摘要B",
                    "showTime": "2026-06-12 23:40:00",
                    "stockList": [],
                },
            ],
        }
    }
    items, cursor = parse_fast_news(data)
    assert cursor == "1781279074058195"
    assert len(items) == 2
    assert items[0].stocks == ["000032", "600519"]
    assert items[1].stocks is None
    assert items[0].publish_time == "2026-06-12 23:44:34"


def test_stock_codes_filters_non_a_share():
    """美股/港股/板块代码必须被过滤（港股 5 位代码截尾会误撞 A 股）。"""
    raw = ["105.SPCX", "116.00700", "90.BK0475", "0.300750", "1.688041", "garbage"]
    assert _stock_codes(raw) == ["300750", "688041"]


def test_parse_stock_news_strips_em_tags():
    body = {
        "result": {
            "cmsArticleWebOld": [
                {
                    "code": "c1",
                    "title": "贵州茅台(<em>600519</em>)公告",
                    "content": "正文<em>高亮</em>片段",
                    "date": "2026-06-12 10:27:00",
                    "mediaName": "南方财经",
                    "url": "http://example.com/a.html",
                }
            ]
        }
    }
    items = parse_stock_news(body)
    assert len(items) == 1
    assert items[0].title == "贵州茅台(600519)公告"
    assert items[0].summary == "正文高亮片段"


# ---------------------------------------------------------------- 报告期推导


def test_report_dates_mid_year():
    """6 月中：应同步一季报（已发完）+ 中报（正在发布）。"""
    assert current_report_dates(date(2026, 6, 12)) == ["2026-03-31", "2026-06-30"]


def test_report_dates_january():
    """1 月：应同步三季报 + 年报（去年 12-31 尚未到，年报预告窗口）。"""
    assert current_report_dates(date(2026, 1, 15)) == ["2025-12-31", "2026-03-31"]


def test_report_dates_exact_boundary():
    """报告期当天算入该期。"""
    assert current_report_dates(date(2026, 6, 30)) == ["2026-03-31", "2026-06-30"]
    assert current_report_dates(date(2026, 12, 31)) == ["2026-09-30", "2026-12-31"]


# ---------------------------------------------------------------- 扩展因子挂载


def _base_wide() -> pd.DataFrame:
    return pd.DataFrame(
        {"close": [10.0, 20.0, 30.0]},
        index=pd.Index(["000001", "600519", "300750"], name="symbol"),
    )


def test_attach_ext_factors_full():
    wide = _base_wide()
    fund_flow = pd.DataFrame(
        {
            "symbol": ["000001", "600519"],
            "main_net": [5e7, -2e8],
            "main_pct": [8.0, -3.0],
            "net_3d": [1.2e8, -5e8],
            "net_5d": [2e8, -6e8],
            "net_10d": [3e8, -7e8],
            "dv_ttm": [4.5, 1.2],
        }
    )
    dt = pd.DataFrame(
        {"symbol": ["000001"], "dt_net_amt": [3.4e8], "dt_has_inst": [1]}
    )
    earnings = pd.DataFrame(
        {
            "symbol": ["600519", "300750"],
            "predict_type": ["预增", "预减"],
            "earn_amp_lower": [60.0, -30.0],
        }
    )
    pop = pd.DataFrame({"symbol": ["300750"], "rank": [3], "rank_chg": [45]})

    attach_ext_factors(
        wide,
        fund_flow=fund_flow,
        dragon_tiger=dt,
        earnings=earnings,
        popularity=pop,
        senti_scores={"600519": 82},
    )

    # 资金流：单位换算成亿
    assert wide.at["000001", "main_net_yi"] == 0.5
    assert wide.at["000001", "main_net_3d_yi"] == 1.2
    assert wide.at["000001", "dv_ttm"] == 4.5
    assert np.isnan(wide.at["300750", "main_net_yi"])  # 没有该股数据 → NaN

    # 龙虎榜：bool 因子无数据必须是 False 而非 NaN
    assert bool(wide.at["000001", "dt_inst_buy"]) is True
    assert bool(wide.at["600519", "dt_inst_buy"]) is False
    assert wide.at["000001", "dt_net_yi"] == 3.4

    # 业绩预告：预增类型判定
    assert bool(wide.at["600519", "earn_is_up"]) is True
    assert bool(wide.at["300750", "earn_is_up"]) is False  # 预减不算向好
    assert wide.at["600519", "earn_amp_lower"] == 60.0

    # 人气榜与AI情绪
    assert wide.at["300750", "pop_rank"] == 3
    assert wide.at["300750", "pop_jump"] == 45
    assert wide.at["600519", "senti_score"] == 82
    assert np.isnan(wide.at["000001", "senti_score"])


def test_attach_ext_factors_all_empty():
    """扩展数据全缺失（首次启动未同步）：数值因子 NaN、布尔因子 False，不报错。"""
    wide = _base_wide()
    attach_ext_factors(wide)
    assert wide["main_net_yi"].isna().all()
    assert (~wide["dt_inst_buy"].astype(bool)).all()
    assert (~wide["earn_is_up"].astype(bool)).all()
    assert wide["senti_score"].isna().all()


def test_ext_factors_registered_in_meta():
    """EXT_FACTORS 与 FACTOR_META 的 ext 标记一致，且条件树可引用。"""
    assert "main_net_yi" in EXT_FACTORS
    assert "senti_score" in EXT_FACTORS
    for f in EXT_FACTORS:
        assert f in FACTOR_META
        assert FACTOR_META[f].get("ext") is True


def test_nan_comparison_never_hits():
    """NaN 因子参与数值比较恒为 False —— "没有数据就不命中"的契约。"""
    from app.strategy import engine

    wide = _base_wide()
    attach_ext_factors(wide)  # 全 NaN
    mask, _ = engine.evaluate({"factor": "main_net_yi", "op": ">", "value": 0}, wide)
    assert not mask.any()
