"""因子计算引擎：全市场日线面板 → 最新交易日"因子宽表"。

宽表每行一只股票（index=symbol），列是所有可供策略引用的因子：
- 数字因子：close / ma20 / rsi14 / vol_ratio / pe_ttm ...（条件树用 > < between 比较）
- 布尔因子：ma_bull_arrange / macd_water_gold_3d ...（条件树用 is_true 判断）

FACTOR_META 注册表描述每个因子的中文名与白话说明——
前端自定义策略构建器的下拉选项、选股结果"原因明细"的人话化都从这里取。
"""

import numpy as np
import pandas as pd

from app.strategy import indicators as ind
from app.strategy.intraday import INTRADAY_META

# ---------------------------------------------------------------- 因子元数据

FACTOR_META: dict[str, dict] = {
    # ---- 行情 ----
    "close": {"label": "收盘价", "unit": "元", "kind": "number"},
    "pct_change": {"label": "今日涨跌幅", "unit": "%", "kind": "number"},
    "turnover": {"label": "换手率", "unit": "%", "kind": "number"},
    "amount_yi": {"label": "成交额", "unit": "亿元", "kind": "number"},
    "vol_ratio": {
        "label": "量比(5日)",
        "unit": "",
        "kind": "number",
        "desc": "今日成交量相对前5日平均的放大倍数，>1.5 算明显放量",
    },
    # ---- 均线 ----
    "ma5": {"label": "5日均线", "unit": "元", "kind": "number"},
    "ma10": {"label": "10日均线", "unit": "元", "kind": "number"},
    "ma20": {"label": "20日均线", "unit": "元", "kind": "number"},
    "ma60": {"label": "60日均线", "unit": "元", "kind": "number"},
    "ma250": {"label": "250日年线", "unit": "元", "kind": "number"},
    "ma_bull_arrange": {
        "label": "均线多头排列",
        "kind": "bool",
        "desc": "MA5>MA10>MA20>MA60，短中长期成本依次抬高，趋势向上",
    },
    "above_ma20": {"label": "站上20日线", "kind": "bool"},
    "ma5_cross_ma20_3d": {"label": "5日线近3日金叉20日线", "kind": "bool"},
    "cross_ma250_3d": {
        "label": "近3日上穿年线",
        "kind": "bool",
        "desc": "收盘价从年线(MA250)下方首次穿越到上方，常被视为长期趋势转折",
    },
    "ma_converge_diverge": {
        "label": "均线粘合后发散",
        "kind": "bool",
        "desc": "MA5/10/20 收敛到极差<2%后向上张开，变盘启动信号",
    },
    "yang_cross_3ma": {
        "label": "一阳穿三线",
        "kind": "bool",
        "desc": "单日阳线同时上穿 MA5/MA10/MA20，多头力量集中爆发",
    },
    "pullback_ma20_dry": {
        "label": "缩量回踩20日线",
        "kind": "bool",
        "desc": "多头排列中缩量回踩 MA20 企稳不破，洗盘特征",
    },
    # ---- MACD ----
    "macd_dif": {"label": "MACD-DIF", "kind": "number"},
    "macd_hist": {"label": "MACD柱", "kind": "number"},
    "macd_water_gold_3d": {
        "label": "MACD水上金叉(3日内)",
        "kind": "bool",
        "desc": "DIF 在零轴上方上穿 DEA，多头趋势中的二次加速信号",
    },
    # ---- 摆动 ----
    "rsi14": {"label": "RSI(14)", "kind": "number", "desc": "相对强弱指标，<25 超卖、>75 超买"},
    "rsi_rebound_25": {
        "label": "RSI超卖回升",
        "kind": "bool",
        "desc": "近5日 RSI 曾跌破 25（超卖），且今日开始回升",
    },
    "kdj_j": {"label": "KDJ-J值", "kind": "number"},
    "kdj_oversold_gold": {
        "label": "KDJ超卖金叉",
        "kind": "bool",
        "desc": "J 值近5日内跌破 0 后，K 线上穿 D 线",
    },
    "boll_lower_rebound": {
        "label": "布林下轨反弹",
        "kind": "bool",
        "desc": "近3日触及布林下轨后收出阳线收回轨道内",
    },
    "bias20": {
        "label": "20日乖离率",
        "unit": "%",
        "kind": "number",
        "desc": "收盘价偏离20日均线的百分比，<-15% 表示超跌",
    },
    # ---- 区间涨幅 ----
    "chg_5d": {
        "label": "5日累计涨跌幅",
        "unit": "%",
        "kind": "number",
        "desc": "最近5个交易日的累计涨跌幅，衡量短线强弱",
    },
    "chg_10d": {
        "label": "10日累计涨跌幅",
        "unit": "%",
        "kind": "number",
        "desc": "最近10个交易日的累计涨跌幅",
    },
    "chg_20d": {
        "label": "20日累计涨跌幅",
        "unit": "%",
        "kind": "number",
        "desc": "最近20个交易日（约一个月）的累计涨跌幅",
    },
    # ---- 新高新低/波动 ----
    "high_60d_break": {"label": "创60日新高", "kind": "bool"},
    "drawdown_60d": {"label": "60日累计涨跌幅", "unit": "%", "kind": "number"},
    "vol_dry": {
        "label": "地量",
        "kind": "bool",
        "desc": "今日成交量不足60日均量的一半，抛压枯竭信号",
    },
    # ---- K线形态 ----
    "morning_star": {
        "label": "早晨之星",
        "kind": "bool",
        "desc": "大阴线+企稳小K线+大阳线的三日组合，经典见底形态",
    },
    "hammer_low": {
        "label": "低位锤子线",
        "kind": "bool",
        "desc": "60日低位出现长下影小实体K线，下探后被买盘拉回",
    },
    "bullish_engulf": {
        "label": "阳包阴",
        "kind": "bool",
        "desc": "今日阳线实体完全吞没昨日阴线实体，多头反攻",
    },
    "red_three": {
        "label": "红三兵",
        "kind": "bool",
        "desc": "连续三日小阳稳步推升，底部温和吸筹形态",
    },
    "lotus_breakout": {
        "label": "出水芙蓉",
        "kind": "bool",
        "desc": "一根放量大阳线（>4%）从均线下方一举上穿 MA5/10/20，如芙蓉出水，强启动信号",
    },
    "sunrise": {
        "label": "旭日东升",
        "kind": "bool",
        "desc": "昨日大阴线，今日高开大阳反包收复昨日开盘价，多头强势逆转",
    },
    "dawn_light": {
        "label": "曙光初现",
        "kind": "bool",
        "desc": "昨日大阴线，今日低开后强力收阳，收盘吃掉昨日阴线一半以上",
    },
    "double_cannon": {
        "label": "两阳夹一阴(多方炮)",
        "kind": "bool",
        "desc": "阳-阴-阳三日组合且今日收盘创三日新高，洗盘后再度进攻",
    },
    "three_big_yang": {
        "label": "三阳开泰",
        "kind": "bool",
        "desc": "连续三日中大阳线（每日>2%，累计>7%），多头连续发力",
    },
    "platform_break": {
        "label": "平台突破",
        "kind": "bool",
        "desc": "横盘整理约一个月（振幅<15%）后，今日放量阳线创20日新高，突破启动",
    },
    "gap_up_today": {
        "label": "向上跳空缺口",
        "kind": "bool",
        "desc": "今日最低价高于昨日最高价，留下未回补的向上缺口，多头不回头",
    },
    "w_bottom_break": {
        "label": "W底突破(双重底)",
        "kind": "bool",
        "desc": "60日内两次探底接近、中间显著反弹，今日放量突破颈线位",
    },
    "close_position": {
        "label": "收盘强弱位置",
        "unit": "%",
        "kind": "number",
        "desc": "收盘价在全天振幅中的位置（0=收最低 100=收最高），"
        ">80 即尾盘强势收盘（日线对'尾盘走强'的最近似刻画）",
    },
    # ---- 量价 ----
    "mild_volume_up": {
        "label": "温和放量吸筹",
        "kind": "bool",
        "desc": "近5日量能温和放大(1.2~2倍)且股价小幅上行，疑似资金悄悄建仓",
    },
    "limit_up_today": {"label": "今日涨停", "kind": "bool"},
    "first_limit_up": {
        "label": "首板涨停",
        "kind": "bool",
        "desc": "今日涨停且昨日未涨停（排除连板与ST），情绪启动信号",
    },
    "limit_count_60d": {
        "label": "60日涨停次数",
        "kind": "number",
        "desc": "活跃资金关照过的次数，≥2 说明有'涨停基因'",
    },
    "consolidating": {
        "label": "近期整理中",
        "kind": "bool",
        "desc": "近5日日均振幅小于4%，处于蓄势状态",
    },
    "stop_falling": {
        "label": "深跌止跌企稳",
        "kind": "bool",
        "desc": "60日跌超30%后出现地量阳线，下跌动能衰竭",
    },
    # ---- 基本面 ----
    "pe_ttm": {
        "label": "市盈率TTM",
        "kind": "number",
        "desc": "股价/每股收益，越低越便宜；负数=亏损",
    },
    "pb": {"label": "市净率", "kind": "number", "desc": "股价/每股净资产，<1 即'破净'"},
    "total_mv_yi": {"label": "总市值", "unit": "亿元", "kind": "number"},
    "pb_recover": {
        "label": "破净后回升",
        "kind": "bool",
        "desc": "市净率低于1（股价低于账面净资产）且股价站上20日线开始修复",
    },
    "is_st": {"label": "ST风险股", "kind": "bool"},
    # ---- 扩展数据因子（M5：从接入日起逐日积累，无完整历史 → 不支持回测）----
    "main_net_yi": {
        "label": "今日主力净流入",
        "unit": "亿元",
        "kind": "number",
        "ext": True,
        "desc": "大单+超大单口径的净买入额，正数代表主力资金净流入",
    },
    "main_net_3d_yi": {
        "label": "3日主力净流入",
        "unit": "亿元",
        "kind": "number",
        "ext": True,
        "desc": "近3个交易日主力净流入累计，持续为正说明有真实买方力量",
    },
    "main_pct": {
        "label": "主力净占比",
        "unit": "%",
        "kind": "number",
        "ext": True,
        "desc": "主力净流入占当日成交额的比例，>10% 算控盘力度明显",
    },
    "dv_ttm": {
        "label": "股息率TTM",
        "unit": "%",
        "kind": "number",
        "ext": True,
        "desc": "近12个月分红/股价，>4% 属于高股息'类债券'资产",
    },
    "dt_net_yi": {
        "label": "龙虎榜净买入(3日)",
        "unit": "亿元",
        "kind": "number",
        "ext": True,
        "desc": "近3日龙虎榜席位净买入合计（交易所披露的大额成交）",
    },
    "dt_inst_buy": {
        "label": "龙虎榜机构买入",
        "kind": "bool",
        "ext": True,
        "desc": "近3日龙虎榜上榜解读中出现'机构买入'字样",
    },
    "earn_amp_lower": {
        "label": "业绩预增幅下限",
        "unit": "%",
        "kind": "number",
        "ext": True,
        "desc": "最新业绩预告的净利润变动幅度下限（保守口径）",
    },
    "earn_is_up": {
        "label": "业绩预告向好",
        "kind": "bool",
        "ext": True,
        "desc": "预告类型为预增/略增/扭亏/续盈之一",
    },
    "pop_rank": {
        "label": "人气榜名次",
        "kind": "number",
        "ext": True,
        "desc": "股吧人气排名（1为最热，未上榜无值），短线情绪温度计",
    },
    "pop_jump": {
        "label": "人气名次跃升",
        "kind": "number",
        "ext": True,
        "desc": "较昨日上升的名次数，新上榜按大幅跃升(999)处理",
    },
    "senti_score": {
        "label": "AI情绪分",
        "kind": "number",
        "ext": True,
        "desc": "AI 阅读个股新闻打出的情绪分 0~100（仅已分析过的股票有值）",
    },
}

# 盘中因子（5分钟线计算，intraday.py 注册）也并入总表——
# 同属"从接入日积累、无完整历史"的扩展因子
FACTOR_META.update(INTRADAY_META)

# 扩展因子集合（回测服务用：引用这些因子的条件树不支持历史回测）
EXT_FACTORS: set[str] = {k for k, v in FACTOR_META.items() if v.get("ext")}


def limit_threshold(symbols: pd.Series, is_st: pd.Series) -> pd.Series:
    """每只股票的涨停判定阈值（%）：主板10 / 创业科创20 / 北交所30 / ST减半。"""
    pfx2 = symbols.str[:2]
    base = pd.Series(9.8, index=symbols.index)
    base[pfx2.isin(["30", "68"])] = 19.8
    base[pfx2.isin(["43", "83", "87", "88", "92"])] = 29.8
    # ST 在主板是 5cm；创业/科创/北交 ST 涨跌幅不变
    main_st = is_st.astype(bool) & ~pfx2.isin(["30", "68", "43", "83", "87", "88", "92"])
    base[main_st] = 4.8
    return base


def compute_factor_table(
    panel: pd.DataFrame,
    basics: pd.DataFrame,
    fundamentals: pd.DataFrame,
    *,
    all_dates: bool = False,
) -> pd.DataFrame:
    """面板 → 因子宽表。

    panel：全市场近 N 日 QFQ 日线（symbol, date, open..pct_change），已按 (symbol,date) 排序
    basics：股票主档（symbol, name, is_st）
    fundamentals：最新估值快照（symbol, pe_ttm, pb, total_mv）

    all_dates=False（默认，选股场景）：每股取最新一行，index=symbol。
    all_dates=True（回测场景）：保留全部日期的长表，含 symbol/trade_date 列，
    由回测引擎按调仓日切片。注意：基本面因子（pe/pb/市值）用"最新快照"近似
    回填到所有历史日期——库内估值快照从 M1 上线日才开始积累，更早的历史没有。
    """
    df = panel
    pos = ind.group_pos(df)
    sym = df["symbol"]
    o, h, lo, c = df["open"], df["high"], df["low"], df["close"]
    vol, pct = df["volume"], df["pct_change"]

    out = pd.DataFrame(index=df.index)

    # ---- 均线 ----
    ma5 = ind.sma(c, pos, 5)
    ma10 = ind.sma(c, pos, 10)
    ma20 = ind.sma(c, pos, 20)
    ma60 = ind.sma(c, pos, 60)
    ma250 = ind.sma(c, pos, 250)
    out["ma5"], out["ma10"], out["ma20"], out["ma60"], out["ma250"] = ma5, ma10, ma20, ma60, ma250
    out["ma_bull_arrange"] = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)
    out["above_ma20"] = c > ma20
    out["ma5_cross_ma20_3d"] = ind.within_last(ind.cross_above(ma5, ma20, pos), pos, 3)
    out["cross_ma250_3d"] = ind.within_last(ind.cross_above(c, ma250, pos), pos, 3)

    # 均线粘合发散：5日前三线极差<2%，今日 ma5>ma10>ma20 且 ma5 抬头
    ma_max = pd.concat([ma5, ma10, ma20], axis=1).max(axis=1)
    ma_min = pd.concat([ma5, ma10, ma20], axis=1).min(axis=1)
    converged = (ma_max / ma_min - 1) < 0.02
    converged_5d_ago = ind.shift(converged.astype(float), pos, 5) > 0
    diverging = (ma5 > ma10) & (ma10 > ma20) & (ma5 > ind.shift(ma5, pos, 1))
    out["ma_converge_diverge"] = converged_5d_ago & diverging

    # 一阳穿三线
    three_min = pd.concat([ma5, ma10, ma20], axis=1).min(axis=1)
    three_max = pd.concat([ma5, ma10, ma20], axis=1).max(axis=1)
    out["yang_cross_3ma"] = (c > o) & (o < three_min) & (c > three_max)

    # 缩量回踩20日线：多头排列 + 近3日低点触过 MA20(±2%) + 收盘守住 + 今日缩量
    vol_ma5 = ind.sma(vol, pos, 5)
    touched = ind.within_last((lo <= ma20 * 1.02) & (c >= ma20 * 0.99), pos, 3)
    out["pullback_ma20_dry"] = (
        out["ma_bull_arrange"] & touched & (c >= ma20) & (vol < vol_ma5 * 0.8)
    )

    # ---- MACD ----
    dif, dea, hist = ind.macd(c, sym, pos)
    out["macd_dif"], out["macd_hist"] = dif, hist
    water_gold = ind.cross_above(dif, dea, pos) & (dif > 0)
    out["macd_water_gold_3d"] = ind.within_last(water_gold, pos, 3)

    # ---- RSI ----
    rsi14 = ind.rsi(c, sym, pos, 14)
    out["rsi14"] = rsi14
    was_oversold = ind.within_last(rsi14 < 25, pos, 5)
    out["rsi_rebound_25"] = was_oversold & (rsi14 > ind.shift(rsi14, pos, 1))

    # ---- KDJ ----
    k, d, j = ind.kdj(h, lo, c, sym, pos)
    out["kdj_j"] = j
    j_was_neg = ind.within_last(j < 0, pos, 5)
    out["kdj_oversold_gold"] = j_was_neg & ind.within_last(ind.cross_above(k, d, pos), pos, 2)

    # ---- BOLL ----
    upper, mid, lower = ind.boll(c, pos, 20, 2.0)
    touched_lower = ind.within_last(lo <= lower, pos, 3)
    out["boll_lower_rebound"] = touched_lower & (c > o) & (c > lower)

    # ---- BIAS ----
    out["bias20"] = ind.bias(c, pos, 20)

    # ---- 新高/累计涨跌/地量 ----
    high60 = ind.rolling_max(h, pos, 60)
    out["high_60d_break"] = h >= high60  # 今日触及60日最高
    close_60d_ago = ind.shift(c, pos, 60)
    out["drawdown_60d"] = (c / close_60d_ago - 1) * 100

    # ---- 区间涨幅（同花顺条件选股的高频条件） ----
    out["chg_5d"] = (c / ind.shift(c, pos, 5) - 1) * 100
    out["chg_10d"] = (c / ind.shift(c, pos, 10) - 1) * 100
    out["chg_20d"] = (c / ind.shift(c, pos, 20) - 1) * 100
    vol_ma60 = ind.sma(vol, pos, 60)
    out["vol_dry"] = vol < vol_ma60 * 0.5

    # ---- K线形态 ----
    body = (c - o).abs()
    body_pct = body / ind.shift(c, pos, 1) * 100  # 实体占昨收百分比
    o1, c1 = ind.shift(o, pos, 1), ind.shift(c, pos, 1)
    o2, c2 = ind.shift(o, pos, 2), ind.shift(c, pos, 2)
    pct2 = ind.shift(pct, pos, 2)

    # 早晨之星：T-2 大阴(<-3%)，T-1 小实体(<1.5%)，T0 大阳(>3%)收复 T-2 实体一半
    small_body_1 = ind.shift(body_pct, pos, 1) < 1.5
    out["morning_star"] = (pct2 < -3) & small_body_1 & (pct > 3) & (c > (o2 + c2) / 2)

    # 低位锤子线：收盘位于60日低点 15% 范围内 + 下影 > 2×实体 + 实体小
    low60 = ind.rolling_min(lo, pos, 60)
    lower_shadow = pd.concat([o, c], axis=1).min(axis=1) - lo
    out["hammer_low"] = (
        (c <= low60 * 1.15)
        & (lower_shadow > body * 2)
        & (body_pct < 2)
        & (lower_shadow / ind.shift(c, pos, 1) > 0.02)
    )

    # 阳包阴：昨日阴线，今日阳线实体完全吞没昨日实体
    out["bullish_engulf"] = (c1 < o1) & (c > o) & (o <= c1) & (c >= o1)

    # 红三兵：连续3日阳线且收盘逐日抬高，单日涨幅都不过 5%（温和）
    yang = c > o
    rising = c > c1
    mild = pct.abs() < 5
    rt = yang & rising & mild
    out["red_three"] = ind.streak_true(rt, pos, 3)

    # ---- 量价 ----
    out["vol_ratio"] = ind.volume_ratio(vol, pos, 5)

    # ---- 经典日线形态（同花顺"形态选股"对标，全部有完整历史可回测）----
    pct1 = ind.shift(pct, pos, 1)
    vratio = out["vol_ratio"]

    # 出水芙蓉：放量大阳，开在三线（5/10/20）之下、收在三线之上，一举突破
    out["lotus_breakout"] = (
        (pct > 4) & (c > o) & (o < three_min) & (c > three_max) & (vratio > 1.3)
    )

    # 旭日东升：昨日大阴(<-3%)，今日高开大阳(>3%)收复昨日开盘价
    out["sunrise"] = (pct1 < -3) & (o > c1) & (c > o) & (pct > 3) & (c > o1)

    # 曙光初现：昨日大阴，今日低开强力收阳，收盘吃掉昨日阴线实体一半以上（未完全反包）
    out["dawn_light"] = (
        (pct1 < -3) & (o < c1) & (c > o) & (c > (o1 + c1) / 2) & (c < o1)
    )

    # 两阳夹一阴（多方炮）：阳-阴-阳，阴线不破首阳开盘，今日收盘创三日新高
    out["double_cannon"] = (
        (c2 > o2) & (c1 < o1) & (c > o) & (c1 > o2) & (c > c2) & (c > c1)
    )

    # 三阳开泰：连续3日中大阳（每日>2%），3日累计涨幅>7%
    big_yang = (c > o) & (pct > 2)
    chg3 = (c / ind.shift(c, pos, 3) - 1) * 100
    out["three_big_yang"] = ind.streak_true(big_yang, pos, 3) & (chg3 > 7)

    # 平台突破：前20日（不含今日）振幅<15% 的横盘，今日放量阳线创20日新高
    high20_prev = ind.shift(ind.rolling_max(h, pos, 20), pos, 1)
    low20_prev = ind.shift(ind.rolling_min(lo, pos, 20), pos, 1)
    platform = (high20_prev / low20_prev - 1) < 0.15
    out["platform_break"] = platform & (c > o) & (h >= high20_prev) & (vratio > 1.5)

    # 向上跳空缺口：今日最低价高于昨日最高价（缺口未回补）
    h1 = ind.shift(h, pos, 1)
    out["gap_up_today"] = lo > h1

    # W底突破（简化双重底）：60日内两次探底接近（差<4%）、
    # 颈线（两底间高点）较首底反弹>6%，今日放量站上颈线且距二底涨幅<25%（没涨飞）
    low_old = ind.shift(ind.rolling_min(lo, pos, 30), pos, 31)  # T-60..T-31 最低
    low_new = ind.shift(ind.rolling_min(lo, pos, 27), pos, 4)  # T-30..T-4 最低
    neck = ind.shift(ind.rolling_max(c, pos, 40), pos, 4)  # 近段（剔除最近3日）收盘高点
    out["w_bottom_break"] = (
        ((low_new / low_old - 1).abs() < 0.04)
        & (neck / low_old > 1.06)
        & (c > neck)
        & (vratio > 1.2)
        & (c / low_new < 1.25)
    )

    # 收盘强弱位置：收盘价在全天振幅中的位置（一字板按涨跌方向记 100/0）
    span_hl = h - lo
    out["close_position"] = pd.Series(
        np.where(
            span_hl > 0,
            (c - lo) / span_hl.replace(0, np.nan) * 100,
            np.where(pct > 0, 100.0, 0.0),
        ),
        index=df.index,
    )
    # 温和放量：近5日均量/再前5日均量 ∈ [1.2, 2]，且近5日累计涨幅 0~8%
    vol_ma5_prev = ind.shift(vol_ma5, pos, 5)
    vol_amp = vol_ma5 / vol_ma5_prev.replace(0, np.nan)
    close_5d_ago = ind.shift(c, pos, 5)
    chg5 = (c / close_5d_ago - 1) * 100
    out["mild_volume_up"] = vol_amp.between(1.2, 2.0) & chg5.between(0, 8)

    # ---- 涨停（按板块规则阈值）----
    sym_series = sym
    st_map = basics.set_index("symbol")["is_st"] if len(basics) else pd.Series(dtype=bool)
    is_st_row = sym_series.map(st_map).fillna(False).astype(bool)
    thresh = limit_threshold(sym_series, is_st_row)
    limit_up = pct >= thresh.to_numpy()
    out["limit_up_today"] = limit_up
    prev_limit = ind.shift(limit_up.astype(float), pos, 1) > 0
    out["first_limit_up"] = limit_up & ~prev_limit & ~is_st_row.to_numpy()
    out["limit_count_60d"] = ind.count_last(limit_up, pos, 60)

    # 整理中：近5日平均振幅 < 4%
    amplitude = (h - lo) / ind.shift(c, pos, 1) * 100
    out["consolidating"] = ind.sma(amplitude, pos, 5) < 4

    # 深跌止跌：60日跌超30% + 地量 + 收阳
    out["stop_falling"] = (out["drawdown_60d"] < -30) & out["vol_dry"] & (c > o)

    # ---- 行情快照列 ----
    out["close"] = c
    out["pct_change"] = pct
    out["turnover"] = df["turnover"]
    out["amount_yi"] = df["amount"] / 1e8

    out["symbol"] = sym_series.to_numpy()

    if all_dates:
        # 回测模式：保留全部日期，基本面按最新快照近似回填
        out["trade_date"] = df["trade_date"].to_numpy()
        if len(fundamentals):
            fund = fundamentals.set_index("symbol")
            out["pe_ttm"] = sym_series.map(fund["pe_ttm"]).to_numpy()
            out["pb"] = sym_series.map(fund["pb"]).to_numpy()
            out["total_mv_yi"] = sym_series.map(fund["total_mv"] / 1e8).to_numpy()
        else:
            out["pe_ttm"] = np.nan
            out["pb"] = np.nan
            out["total_mv_yi"] = np.nan
        out["is_st"] = is_st_row.to_numpy()
        out["pb_recover"] = (out["pb"] < 1) & out["above_ma20"].astype(bool)
        return out

    # ---- 选股模式：取每只股票最后一行（最新交易日）----
    last_idx = df.groupby("symbol", sort=False).tail(1).index
    wide = out.loc[last_idx].set_index("symbol")

    # ---- 基本面与主档 ----
    if len(fundamentals):
        fund = fundamentals.set_index("symbol")
        wide["pe_ttm"] = fund["pe_ttm"].reindex(wide.index)
        wide["pb"] = fund["pb"].reindex(wide.index)
        wide["total_mv_yi"] = (fund["total_mv"] / 1e8).reindex(wide.index)
    else:
        wide["pe_ttm"] = np.nan
        wide["pb"] = np.nan
        wide["total_mv_yi"] = np.nan
    wide["is_st"] = wide.index.to_series().map(st_map).fillna(False).astype(bool)

    # 破净修复：PB<1 且站上20日线
    wide["pb_recover"] = (wide["pb"] < 1) & wide["above_ma20"].astype(bool)

    return wide


def attach_ext_factors(
    wide: pd.DataFrame,
    *,
    fund_flow: pd.DataFrame | None = None,
    dragon_tiger: pd.DataFrame | None = None,
    earnings: pd.DataFrame | None = None,
    popularity: pd.DataFrame | None = None,
    senti_scores: dict[str, int] | None = None,
) -> pd.DataFrame:
    """把扩展数据因子（M5）挂到选股宽表上（原地修改并返回）。

    语义约定：没有数据的股票因子为 NaN/False —— 条件树对 NaN 的数值比较
    恒为 False，自然实现"没有数据就不命中"，无需特判。
    """
    idx = wide.index

    if fund_flow is not None and len(fund_flow):
        ff = fund_flow.set_index("symbol")
        wide["main_net_yi"] = (ff["main_net"] / 1e8).reindex(idx)
        wide["main_net_3d_yi"] = (ff["net_3d"] / 1e8).reindex(idx)
        wide["main_pct"] = ff["main_pct"].reindex(idx)
        # 股息率：东财对无分红股票给 0 或 '-'（已转0），统一保留数值
        wide["dv_ttm"] = ff["dv_ttm"].reindex(idx)
    else:
        wide["main_net_yi"] = np.nan
        wide["main_net_3d_yi"] = np.nan
        wide["main_pct"] = np.nan
        wide["dv_ttm"] = np.nan

    if dragon_tiger is not None and len(dragon_tiger):
        dt = dragon_tiger.set_index("symbol")
        wide["dt_net_yi"] = (dt["dt_net_amt"] / 1e8).reindex(idx)
        wide["dt_inst_buy"] = dt["dt_has_inst"].reindex(idx).fillna(0).astype(bool)
    else:
        wide["dt_net_yi"] = np.nan
        wide["dt_inst_buy"] = False

    if earnings is not None and len(earnings):
        ef = earnings.set_index("symbol")
        wide["earn_amp_lower"] = ef["earn_amp_lower"].reindex(idx)
        up_types = {"预增", "略增", "扭亏", "续盈"}
        wide["earn_is_up"] = (
            (ef["predict_type"].reindex(idx).map(lambda t: t in up_types, na_action="ignore"))
            .fillna(False)
            .astype(bool)
        )
    else:
        wide["earn_amp_lower"] = np.nan
        wide["earn_is_up"] = False

    if popularity is not None and len(popularity):
        pop = popularity.set_index("symbol")
        wide["pop_rank"] = pop["rank"].reindex(idx)
        wide["pop_jump"] = pop["rank_chg"].reindex(idx)
    else:
        wide["pop_rank"] = np.nan
        wide["pop_jump"] = np.nan

    if senti_scores:
        wide["senti_score"] = pd.Series(senti_scores).reindex(idx)
    else:
        wide["senti_score"] = np.nan

    return wide
