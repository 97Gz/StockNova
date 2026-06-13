"""内置策略注册表（PRD 3.4 首批 30 个 + 日线形态 9 个 + 盘中走势 4 个，共 43 个）。

每个策略 = 元数据（白话名/讲解/风险/适用周期）+ 条件树（engine.py 语法）。
- 形态/技术/基本面类：基于日线 OHLCV 与每日基本面（有完整历史），支持回测。
- 扩展数据类（资金流/龙虎榜/股息率/业绩预告/AI情绪/人气榜）：数据从接入日
  逐日积累、无完整历史，带 no_backtest 标记（选股可用，回测被拒并说明原因）。
- 盘中走势类（尾盘抢筹/尾盘跳水/早强守势/重心上移）：基于每日盘后同步的
  5 分钟K线，对标同花顺"尾盘选股"等苛刻条件，同样从接入日积累。
"""

from typing import Any

STRATEGIES: list[dict[str, Any]] = [
    # ================= 趋势类 =================
    {
        "id": "trend_start",
        "name": "趋势启动",
        "tech_name": "均线多头排列",
        "category": "趋势",
        "period": "波段",
        "risk": 2,
        "summary": "短中长期均线依次向上排列，股价站稳5日线，趋势刚走顺",
        "explain": (
            "把 5/10/20/60 日均线想象成不同时间买入的人的平均成本。当它们从上到下"
            "依次是 MA5>MA10>MA20>MA60，说明越晚买的人成本越高还愿意买——典型的上升趋势。"
            "此时股价又站在 5 日线上方，代表短线买力仍在。"
            "\n适合：趋势行情中的波段操作。失效场景：震荡市会频繁假信号；"
            "高位末期的多头排列可能是最后一冲，建议结合量能与位置判断。"
        ),
        "condition": {
            "all": [
                {"factor": "ma_bull_arrange", "op": "is_true"},
                {"factor": "close", "op": ">", "ref": "ma5"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "volume_breakout",
        "name": "放量突破",
        "tech_name": "60日新高+量比放大",
        "category": "趋势",
        "period": "短线",
        "risk": 3,
        "summary": "放量创出60日新高，换手健康，主力进攻信号",
        "explain": (
            "股价突破近 60 日所有高点，意味着前期套牢盘全部解放，上方没有阻力。"
            "配合量比>1.5（成交量明显放大）说明是真金白银推上去的，而非缩量假突破。"
            "换手率限制在 3%~15%：太低没人气，太高可能是对倒出货。"
            "\n适合：强势市场追击龙头。失效场景：大盘暴跌日的个股突破多为诱多；"
            "突破后若快速缩量回落跌回平台，应止损。"
        ),
        "condition": {
            "all": [
                {"factor": "high_60d_break", "op": "is_true"},
                {"factor": "vol_ratio", "op": ">", "value": 1.5},
                {"factor": "turnover", "op": "between", "value": [3, 15]},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "cross_year_line",
        "name": "站上年线",
        "tech_name": "首次上穿MA250",
        "category": "趋势",
        "period": "中线",
        "risk": 2,
        "summary": "股价近3日从年线下方翻越到上方，长期趋势可能反转",
        "explain": (
            "250 日均线≈一年的平均成本，俗称'牛熊分界线'。股价长期在年线下方是熊市特征，"
            "首次有效站上年线，往往是中长期趋势由空转多的标志性事件。"
            "\n适合：左侧布局者寻找趋势反转标的。失效场景：弱市中可能多次假穿越（注意配合成交量），"
            "建议观察站稳 3~5 日再确认。"
        ),
        "condition": {
            "all": [
                {"factor": "cross_ma250_3d", "op": "is_true"},
                {"factor": "close", "op": ">", "ref": "ma250"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "pullback_ma20",
        "name": "缩量回踩",
        "tech_name": "多头排列回踩MA20",
        "category": "趋势",
        "period": "波段",
        "risk": 2,
        "summary": "上升趋势中缩量回调到20日线企稳，洗盘上车点",
        "explain": (
            "好趋势不会一口气涨完，中途的缩量回调（量能萎缩到 5 日均量的 8 成以下）"
            "说明没人恐慌抛售，只是获利盘正常休整。回踩到 20 日线（波段生命线）不破并企稳，"
            "通常是主力洗盘结束、第二波启动前的低吸位置。"
            "\n适合：踏空者等回调上车。失效场景：若放量跌破 20 日线则是趋势走坏，不是洗盘。"
        ),
        "condition": {
            "all": [
                {"factor": "pullback_ma20_dry", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "macd_water_gold",
        "name": "MACD水上金叉",
        "tech_name": "DIF上穿DEA且DIF>0",
        "category": "趋势",
        "period": "波段",
        "risk": 2,
        "summary": "MACD在零轴上方金叉，多头趋势中的二次加速信号",
        "explain": (
            "MACD 金叉分两种：零轴下方的金叉只是超跌反弹，零轴上方的金叉（水上金叉）"
            "则发生在多头趋势内部——相当于上涨途中歇脚后再次发力，可靠度显著更高。"
            "\n适合：趋势确认后的加仓/介入点。失效场景：高位横盘末端的水上金叉可能是"
            "出货前的最后拉升，需要结合股价位置。"
        ),
        "condition": {
            "all": [
                {"factor": "macd_water_gold_3d", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "ma_converge",
        "name": "均线粘合发散",
        "tech_name": "MA5/10/20收敛后向上张开",
        "category": "趋势",
        "period": "波段",
        "risk": 3,
        "summary": "三条均线挤成一团后向上张口，变盘启动的经典形态",
        "explain": (
            "均线粘合（5/10/20 日线极差<2%）说明各周期持仓成本趋同——多空分歧极小，"
            "像弹簧被压到极限。一旦向上发散（短期线带头上翘），积蓄的能量集中释放，"
            "往往走出流畅的主升段。"
            "\n适合：埋伏临界变盘股。失效场景：也可能向下发散，粘合本身不指方向，"
            "本策略已限定向上发散，但仍建议设好止损。"
        ),
        "condition": {
            "all": [
                {"factor": "ma_converge_diverge", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    # ================= 反弹类 =================
    {
        "id": "oversold_rsi",
        "name": "超跌反弹",
        "tech_name": "RSI14超卖回升",
        "category": "反弹",
        "period": "短线",
        "risk": 3,
        "summary": "RSI跌破25后开始回头，超卖区的反弹博弈",
        "explain": (
            "RSI 衡量近 14 天买卖力量对比，低于 25 表示卖方力量极端宣泄、接近枯竭。"
            "本策略等 RSI 开始回升再入场（而非左侧接飞刀），博取超跌后的均值回归。"
            "\n适合：急跌后的短线反抽。失效场景：基本面恶化的下跌（戴帽/暴雷）超卖可以更超卖，"
            "务必小仓位+快进快出。"
        ),
        "condition": {
            "all": [
                {"factor": "rsi_rebound_25", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "boll_rebound",
        "name": "布林下轨反弹",
        "tech_name": "触下轨+阳线收回",
        "category": "反弹",
        "period": "短线",
        "risk": 3,
        "summary": "股价砸到布林带下轨后收阳拉回，统计意义的低位",
        "explain": (
            "布林带下轨=20日均价减两倍标准差，统计上股价 95% 的时间在带内运行。"
            "砸穿下轨属于极端偏离，随后收出阳线回到带内，说明超卖修复启动。"
            "\n适合：震荡市的低吸。失效场景：单边暴跌时股价可以沿着下轨往下走（轨道扩张），"
            "需确认大盘没有系统性风险。"
        ),
        "condition": {
            "all": [
                {"factor": "boll_lower_rebound", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "deep_fall_stop",
        "name": "深跌止跌",
        "tech_name": "60日跌30%+地量企稳",
        "category": "反弹",
        "period": "波段",
        "risk": 3,
        "summary": "暴跌三成后地量收阳，抛压枯竭的左侧信号",
        "explain": (
            "60 天跌超 30% 的股票，想卖的基本卖完了。当成交量萎缩到 60 日均量一半以下（地量），"
            "代表抛压枯竭；此时收出阳线，是'没人卖了+有人开始买'的组合信号。"
            "\n适合：耐心的左侧布局者。失效场景：地量之后还有地量，止跌不等于马上涨，"
            "可能横很久；杜绝满仓抄底。"
        ),
        "condition": {
            "all": [
                {"factor": "stop_falling", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "bias_repair",
        "name": "乖离修复",
        "tech_name": "BIAS20 < -15%",
        "category": "反弹",
        "period": "短线",
        "risk": 3,
        "summary": "股价偏离20日均线超15%，橡皮筋拉过头要回弹",
        "explain": (
            "乖离率（BIAS）= 股价偏离均线的百分比。跌到比 20 日均线低 15% 以上，"
            "像橡皮筋被拉到极限——即使趋势向下，也常有向均线回归的技术性反抽。"
            "\n适合：超短线博反抽（目标位即 20 日线附近）。失效场景：连续跌停股"
            "乖离可以扩到-30%，本策略已排除 ST，但仍需避开基本面暴雷股。"
        ),
        "condition": {
            "all": [
                {"factor": "bias20", "op": "<", "value": -15},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "kdj_oversold_gold",
        "name": "KDJ超卖金叉",
        "tech_name": "J<0后K上穿D",
        "category": "反弹",
        "period": "短线",
        "risk": 3,
        "summary": "J值跌破0后KD金叉，短线超卖修复信号",
        "explain": (
            "KDJ 是最灵敏的短线摆动指标，J 值跌破 0 属于极端超卖（一年出现不了几次）。"
            "随后 K 线上穿 D 线形成金叉，代表短线动能由空翻多。"
            "\n适合：短线客抓超跌修复。失效场景：KDJ 在单边下跌中会钝化"
            "（超卖区反复金叉死叉），建议配合量能或更长周期指标过滤。"
        ),
        "condition": {
            "all": [
                {"factor": "kdj_oversold_gold", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    # ================= K线形态类 =================
    {
        "id": "morning_star",
        "name": "早晨之星",
        "tech_name": "大阴+星线+大阳三日组合",
        "category": "K线形态",
        "period": "波段",
        "risk": 2,
        "summary": "教科书级见底形态：恐慌、企稳、反攻三部曲",
        "explain": (
            "三根 K 线讲一个故事：第一天大阴线（恐慌抛售），第二天小实体星线"
            "（多空力量打平，跌不动了），第三天大阳线收复第一天一半以上失地（多头反攻成功）。"
            "出现在低位时是可靠度较高的反转形态。"
            "\n适合：寻找波段底部。失效场景：高位出现的'早晨之星'无意义，"
            "形态信号都要结合位置看。"
        ),
        "condition": {
            "all": [
                {"factor": "morning_star", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "hammer",
        "name": "锤子线探底",
        "tech_name": "低位长下影小实体",
        "category": "K线形态",
        "period": "短线",
        "risk": 3,
        "summary": "60日低位的长下影线，盘中砸坑被买盘拉回",
        "explain": (
            "锤子线 = 长下影 + 小实体，意味着盘中曾大幅杀跌，但收盘前被买盘强力拉回——"
            "下方有资金承接。出现在 60 日低位附近（而非高位，高位同形态叫'吊颈线'是危险信号）"
            "时是探底信号。"
            "\n适合：短线博底部反弹。失效场景：次日若跌破锤子线最低点，说明承接盘失守，"
            "应立即离场。"
        ),
        "condition": {
            "all": [
                {"factor": "hammer_low", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "bullish_engulf",
        "name": "阳包阴",
        "tech_name": "看涨吞没形态",
        "category": "K线形态",
        "period": "短线",
        "risk": 2,
        "summary": "今日阳线完全吞掉昨日阴线，多头一口气收复失地",
        "explain": (
            "昨天空头打下来的范围，今天多头一根阳线全部吃回来还创了新高——"
            "这就是'吞没'。它直观展示了多空力量的瞬间逆转，低位出现时参考价值更高。"
            "\n适合：确认短线拐点。失效场景：缩量的阳包阴力度存疑；"
            "连续阴跌途中的单日吞没可能只是一日游反抽。"
        ),
        "condition": {
            "all": [
                {"factor": "bullish_engulf", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "red_three",
        "name": "红三兵",
        "tech_name": "连续三日温和阳线",
        "category": "K线形态",
        "period": "波段",
        "risk": 2,
        "summary": "三连小阳步步抬高，资金温和坚定地吸筹",
        "explain": (
            "连续三天收阳、收盘价一天比一天高，而且每天涨幅都不夸张（<5%）——"
            "不是游资一日暴拉，更像有耐心的资金在持续买入。低位红三兵常是行情启动的前奏。"
            "\n适合：底部确认后的早期介入。失效场景：高位红三兵警惕'诱多三连阳'；"
            "若三连阳总涨幅过大则透支空间。"
        ),
        "condition": {
            "all": [
                {"factor": "red_three", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "yang_cross_3ma",
        "name": "一阳穿三线",
        "tech_name": "单日阳线上穿MA5/10/20",
        "category": "K线形态",
        "period": "短线",
        "risk": 3,
        "summary": "一根大阳线同时收复三条均线，多头集中爆发",
        "explain": (
            "开盘还在三条均线之下，收盘已站上全部——单日完成短中期成本的全面收复，"
            "说明买方力量爆发性入场。若伴随放量则可信度更高。"
            "\n适合：捕捉启动日。失效场景：低位横盘区的'一阳穿三线'含金量高，"
            "下跌途中的则可能只是超跌反抽，注意区分。"
        ),
        "condition": {
            "all": [
                {"factor": "yang_cross_3ma", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "lotus_breakout",
        "name": "出水芙蓉",
        "tech_name": "放量大阳一举穿越三均线",
        "category": "K线形态",
        "period": "波段",
        "risk": 3,
        "summary": "放量大阳线从均线之下一举站上三线之上，如芙蓉出水",
        "explain": (
            "'一阳穿三线'的加强版：不仅要单日穿越 MA5/10/20，还要求涨幅>4%、"
            "量比>1.3——量价齐升的强启动信号。芙蓉出水意味着股价从'被均线压制'"
            "切换到'被均线托举'，往往是新一轮波段的第一天。"
            "\n适合：右侧追启动。失效场景：高位出现的'出水芙蓉'可能是诱多的最后一冲，"
            "结合 60 日位置使用更稳。"
        ),
        "condition": {
            "all": [
                {"factor": "lotus_breakout", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "sunrise",
        "name": "旭日东升",
        "tech_name": "大阴后高开大阳反包",
        "category": "K线形态",
        "period": "短线",
        "risk": 3,
        "summary": "昨日大跌今日高开大阳完全反包，多头强势逆转宣言",
        "explain": (
            "昨天大阴线让市场恐慌，今天却直接高开并收出大阳、收盘价超过昨日开盘价——"
            "等于把昨天的恐慌盘全部解放。敢这样'打脸式'反包的通常是有备而来的资金。"
            "\n适合：抓恐慌后的 V 形反转。失效场景：高开太多（>5%）后冲高回落的"
            "假反包要回避，重点看收盘是否守住。"
        ),
        "condition": {
            "all": [
                {"factor": "sunrise", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "dawn_light",
        "name": "曙光初现",
        "tech_name": "大阴后低开收阳吃一半",
        "category": "K线形态",
        "period": "短线",
        "risk": 3,
        "summary": "昨日大阴今日低开强力收阳，收复阴线一半以上失地",
        "explain": (
            "比'旭日东升'温和一档的见底信号：今天先顺势低开（空头惯性），"
            "盘中却被买盘逐步推高，收盘吃掉昨日阴线实体一半以上——黎明前的第一缕光。"
            "多空力量正在易位，但还需次日确认。"
            "\n适合：左侧偏右的试仓点。失效场景：若次日不能继续收高，"
            "可能只是下跌中继的抵抗，轻仓试错。"
        ),
        "condition": {
            "all": [
                {"factor": "dawn_light", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "double_cannon",
        "name": "多方炮",
        "tech_name": "两阳夹一阴创新高",
        "category": "K线形态",
        "period": "短线",
        "risk": 3,
        "summary": "阳-阴-阳组合且收盘创三日新高，洗盘后二次进攻",
        "explain": (
            "第一天放炮（阳线）、第二天洗盘（阴线但不破首阳开盘价）、"
            "第三天再放炮（阳线创三日新高）——像架起的大炮连轰两响。"
            "中间的阴线把不坚定的筹码洗掉，第三天的阳线确认主力没走、继续进攻。"
            "\n适合：短线接力。失效场景：第三天若放巨量滞涨，可能炮是'哑炮'，"
            "用前一日阴线低点做止损。"
        ),
        "condition": {
            "all": [
                {"factor": "double_cannon", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "three_big_yang",
        "name": "三阳开泰",
        "tech_name": "连续三日中大阳累计>7%",
        "category": "K线形态",
        "period": "波段",
        "risk": 3,
        "summary": "连续三根中大阳线步步进逼，多头火力全开",
        "explain": (
            "与温和的'红三兵'不同，三阳开泰是三根实打实的中大阳（每日>2%、"
            "累计>7%）——资金不再遮掩，直接抢筹。常见于重大利好落地或主升浪启动。"
            "\n适合：确认强势后的顺势加仓。失效场景：三连阳后短线涨幅已大，"
            "追高需等回踩；若出现在高位则可能是加速赶顶。"
        ),
        "condition": {
            "all": [
                {"factor": "three_big_yang", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "platform_break",
        "name": "平台突破",
        "tech_name": "月线横盘+放量新高",
        "category": "K线形态",
        "period": "波段",
        "risk": 2,
        "summary": "横盘整理约一个月后放量突破平台上沿，蓄势完毕",
        "explain": (
            "股价在 15% 的箱体内横了近一个月——多空充分换手、筹码沉淀。"
            "今日放量（量比>1.5）阳线创出 20 日新高，说明蓄的势开始释放。"
            "横得越久、突破越有力（威科夫'因果定律'）。"
            "\n适合：喜欢'横有多长竖有多高'的波段客。失效场景：假突破"
            "（突破当日冲高回落收长上影）次日跌回平台要果断离场。"
        ),
        "condition": {
            "all": [
                {"factor": "platform_break", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "gap_up",
        "name": "跳空抢筹",
        "tech_name": "向上跳空缺口+放量阳线",
        "category": "K线形态",
        "period": "短线",
        "risk": 3,
        "summary": "今日最低价都高于昨日最高价，留下未回补的上行缺口",
        "explain": (
            "跳空缺口=资金等不及在昨日价格区间内买入，直接高开抢筹且全天没有回补——"
            "情绪极强的信号。配合放量阳线，说明高开后仍有承接。"
            "A 股谚语'缺口三日不补必有新高'即源于此。"
            "\n适合：强势股短线跟进。失效场景：消耗性缺口（连续大涨后的跳空）"
            "可能是行情末端；若三日内回补缺口则信号失效。"
        ),
        "condition": {
            "all": [
                {"factor": "gap_up_today", "op": "is_true"},
                {"factor": "pct_change", "op": ">", "value": 2},
                {"factor": "vol_ratio", "op": ">", "value": 1.2},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "w_bottom",
        "name": "W底突破",
        "tech_name": "双重底放量过颈线",
        "category": "K线形态",
        "period": "波段",
        "risk": 2,
        "summary": "两次探底不创新低，今日放量突破颈线位，底部构筑完成",
        "explain": (
            "W 底是最经典的底部形态：第一次探底反弹（部分资金试探建仓），"
            "第二次回踩不创新低（抛压枯竭+有人守护），今日放量突破两底之间的高点"
            "（颈线）——底部正式确认。理论涨幅=底到颈线的高度。"
            "\n适合：稳健型波段布局。失效场景：放量突破颈线后又跌回，"
            "可能演变成横盘或三重底，跌破第二个低点止损。"
        ),
        "condition": {
            "all": [
                {"factor": "w_bottom_break", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "strong_close",
        "name": "尾盘强势",
        "tech_name": "收盘位于全天高位+放量",
        "category": "K线形态",
        "period": "短线",
        "risk": 3,
        "summary": "收盘收在全天振幅的最高 20% 区域，资金敢于过夜",
        "explain": (
            "全天走势千变万化，收盘价最诚实：收在全天最高 20% 区域（收盘强弱位置>80）"
            "说明尾盘没人砸盘、买方愿意带着筹码过夜——通常是对次日有信心的表现。"
            "叠加涨幅>2% 和放量过滤掉无人问津的小阳线。"
            "这是日线数据对'尾盘最后半小时走强'的最近似刻画（分钟级精确版见盘中策略）。"
            "\n适合：次日惯性交易者。失效场景：尾盘拉升也可能是做收盘价吸引跟风，"
            "次日低开则信号失效。"
        ),
        "condition": {
            "all": [
                {"factor": "close_position", "op": ">", "value": 80},
                {"factor": "pct_change", "op": ">", "value": 2},
                {"factor": "vol_ratio", "op": ">", "value": 1.2},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    # ================= 量价资金类 =================
    {
        "id": "main_inflow",
        "name": "主力进场",
        "tech_name": "主力资金持续净流入",
        "category": "量价资金",
        "period": "波段",
        "risk": 2,
        "summary": "今日主力净流入为正且3日累计超5000万",
        "explain": (
            "'主力资金'按大单+超大单口径估算（散户下不出的单子）。今日净流入为正"
            "且近 3 日累计超 5000 万，说明不是一日游的脉冲，而是有真实买方力量持续进场。"
            "再叠加'主力净占比'>3% 过滤掉巨量股里的杯水车薪。"
            "\n适合：跟庄波段客。失效场景：大单口径会被主力拆单/对倒干扰，"
            "高位放量净流入反而可能是诱多出货，建议结合位置看。"
        ),
        "no_backtest": "资金流数据从接入日起逐日积累，无完整历史，暂不支持回测",
        "condition": {
            "all": [
                {"factor": "main_net_yi", "op": ">", "value": 0},
                {"factor": "main_net_3d_yi", "op": ">", "value": 0.5},
                {"factor": "main_pct", "op": ">", "value": 3},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "mild_volume",
        "name": "温和放量吸筹",
        "tech_name": "5日量能1.2~2倍放大",
        "category": "量价资金",
        "period": "波段",
        "risk": 2,
        "summary": "量能温和放大且股价小步上行，疑似有资金悄悄建仓",
        "explain": (
            "暴涨暴量人人看得见，聪明资金更喜欢'温水煮青蛙'：近 5 日平均成交量"
            "放大到前 5 日的 1.2~2 倍（明显但不夸张），股价同步小涨 0~8%——"
            "拉高不吓走筹码、又持续买入，是典型的吸筹画像。"
            "\n适合：埋伏潜在启动股。失效场景：吸筹周期可能很长，"
            "适合作为观察池而非立即重仓的依据。"
        ),
        "condition": {
            "all": [
                {"factor": "mild_volume_up", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "dragon_tiger",
        "name": "龙虎榜机构买入",
        "tech_name": "机构席位净买入",
        "category": "量价资金",
        "period": "短线",
        "risk": 3,
        "summary": "近3日登上龙虎榜且有机构买入、整榜净买入为正",
        "explain": (
            "龙虎榜是交易所每日披露的大额成交席位明细——A 股少有的'明牌'。"
            "'机构专用'席位买入代表公募/保险等专业资金真金白银进场，"
            "叠加整榜净买入为正（买方力量占优），次日往往有跟随效应。"
            "\n适合：短线情绪追踪。失效场景：机构也会买错；上榜后情绪透支"
            "高开低走很常见，重点看次日承接力度而非盲目追高。"
        ),
        "no_backtest": "龙虎榜数据从接入日起逐日积累，无完整历史，暂不支持回测",
        "condition": {
            "all": [
                {"factor": "dt_inst_buy", "op": "is_true"},
                {"factor": "dt_net_yi", "op": ">", "value": 0},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "limit_gene",
        "name": "涨停基因",
        "tech_name": "60日2次涨停+整理",
        "category": "量价资金",
        "period": "短线",
        "risk": 3,
        "summary": "近期有过涨停的活跃股，正在缩量整理蓄势",
        "explain": (
            "涨停需要资金集中发力，60 日内出现过 2 次以上涨停说明这只股票"
            "'有人玩、玩得动'——这就是涨停基因。等它进入低波动整理期（日均振幅<4%）再关注，"
            "往往能等到下一波启动。"
            "\n适合：游资风格的短线选手。失效场景：基因会过期——若整理变阴跌破位，"
            "说明资金已撤退。"
        ),
        "condition": {
            "all": [
                {"factor": "limit_count_60d", "op": ">=", "value": 2},
                {"factor": "consolidating", "op": "is_true"},
                {"factor": "limit_up_today", "op": "is_false"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "first_limit",
        "name": "首板战法",
        "tech_name": "当日首次涨停",
        "category": "量价资金",
        "period": "短线",
        "risk": 3,
        "summary": "今日首次涨停（非连板非ST），情绪点火信号",
        "explain": (
            "首板=一段时间内的第一个涨停，是资金点火的明确宣言。相比连板股，"
            "首板的位置低、风险相对小，若题材正确次日常有溢价。"
            "\n适合：打板/半路客。失效场景：尾盘偷袭板、缩量烂板次日容易开盘抢跑；"
            "大盘冰点期慎用。注意：本策略盘后跑出的是今天的首板，用于次日竞价观察。"
        ),
        "condition": {
            "all": [
                {"factor": "first_limit_up", "op": "is_true"},
            ]
        },
    },
    # ================= 基本面类 =================
    {
        "id": "value_white_horse",
        "name": "低估白马",
        "tech_name": "低PE+大市值",
        "category": "基本面",
        "period": "中线",
        "risk": 1,
        "summary": "市盈率0~15倍的百亿大白马，便宜的好公司",
        "explain": (
            "PE（市盈率）可以理解为'按当前盈利水平，几年回本'。0<PE<15 代表估值便宜"
            "且公司盈利（PE 为负=亏损）；市值>100 亿过滤掉小票，留下经营稳定的行业龙头。"
            "\n适合：长线底仓配置。失效场景：'低估值陷阱'——夕阳行业的 PE 永远便宜，"
            "建议结合行业景气度（后续 ROE 数据接入后会更严格）。"
        ),
        "condition": {
            "all": [
                {"factor": "pe_ttm", "op": "between", "value": [0, 15]},
                {"factor": "total_mv_yi", "op": ">", "value": 100},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "high_dividend",
        "name": "高股息防守",
        "tech_name": "股息率>4%+盈利",
        "category": "基本面",
        "period": "中线",
        "risk": 1,
        "summary": "股息率超4%的盈利公司，熊市里的'类债券'避风港",
        "explain": (
            "股息率=近 12 个月每股分红 ÷ 股价。超过 4% 意味着即使股价不涨，"
            "光分红就跑赢绝大多数理财——这是弱市中大资金的避风港。"
            "限定 PE>0（公司盈利）排除'借钱分红'的透支型公司。"
            "\n适合：求稳的底仓配置。失效场景：'高股息陷阱'——周期股顶部利润暴增"
            "推高股息率，次年利润坍塌分红跟着缩水；注意区分盈利的可持续性。"
        ),
        "no_backtest": "股息率快照从接入日起逐日积累，无完整历史，暂不支持回测",
        "condition": {
            "all": [
                {"factor": "dv_ttm", "op": ">", "value": 4},
                {"factor": "pe_ttm", "op": ">", "value": 0},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "earnings_surge",
        "name": "业绩预增",
        "tech_name": "净利预增下限>50%",
        "category": "基本面",
        "period": "波段",
        "risk": 2,
        "summary": "最新预告净利润增幅下限超50%的业绩黑马",
        "explain": (
            "业绩超预期是股价上涨最硬的逻辑之一。本策略盯最新报告期的业绩预告，"
            "且用'增幅下限'这个保守口径（公司说'预增 50%~80%'就按 50% 算），"
            "并要求预告类型为预增/略增/扭亏/续盈之一。"
            "\n适合：财报季的波段埋伏。失效场景：预告 ≠ 落地，正式财报不及预告"
            "会双杀；低基数造成的'增长'（去年亏得多）要看绝对利润规模。"
        ),
        "no_backtest": "业绩预告数据按最新报告期同步，无历史归档，暂不支持回测",
        "condition": {
            "all": [
                {"factor": "earn_is_up", "op": "is_true"},
                {"factor": "earn_amp_lower", "op": ">", "value": 50},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "small_cap_gem",
        "name": "小市值掘金",
        "tech_name": "市值20~60亿+盈利",
        "category": "基本面",
        "period": "波段",
        "risk": 3,
        "summary": "20~60亿市值的盈利小盘股，弹性大的'小快灵'",
        "explain": (
            "小市值意味着拉升所需资金少、想象空间大（历史上 A 股小市值因子长期有超额收益）。"
            "限定 PE>0（公司盈利）排除纯讲故事的垃圾股，再排除 ST 风险股。"
            "\n适合：风险偏好高的弹性仓位。失效场景：注册制下壳价值消亡，"
            "小票流动性风险上升，分散持有比单吊更稳妥。"
        ),
        "condition": {
            "all": [
                {"factor": "total_mv_yi", "op": "between", "value": [20, 60]},
                {"factor": "pe_ttm", "op": ">", "value": 0},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "pb_recover",
        "name": "破净修复",
        "tech_name": "PB<1且站上20日线",
        "category": "基本面",
        "period": "中线",
        "risk": 2,
        "summary": "股价低于每股净资产且开始走强，价值修复行情",
        "explain": (
            "PB<1（破净）= 把公司清算卖掉都比股价值钱，是极端低估的标志。"
            "但破净股可以一直破净，所以加一个'站上 20 日线'的右侧条件——"
            "等市场开始纠错再上车，而不是单纯赌便宜。"
            "\n适合：低风险偏好的价值派。失效场景：银行地产等行业整体性破净是常态，"
            "修复需要行业层面的催化。"
        ),
        "condition": {
            "all": [
                {"factor": "pb_recover", "op": "is_true"},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    # ================= 情绪/题材类 =================
    {
        "id": "hot_board_leader",
        "name": "热门板块龙头",
        "tech_name": "领涨板块内涨幅前3",
        "category": "情绪题材",
        "period": "短线",
        "risk": 3,
        "summary": "今日最强板块里的最强个股，干就完了的龙头思维",
        "explain": (
            "A 股的超额收益高度集中在'板块效应'：资金抱团攻击某个题材时，"
            "买板块里最强的龙头（而非补涨的杂毛）是胜率最高的打法。"
            "本策略先找出今日涨幅前 3 的行业板块，再取每个板块内涨幅前 3 的成分股。"
            "\n适合：情绪周期玩家。失效场景：板块一日游时龙头也会闷杀；"
            "需要观察题材的持续性（是否有政策/产业催化）。"
        ),
        "condition": None,  # 特殊实现：需要板块数据，见 strategy_service.run_board_leader
        "special": "board_leader",
    },
    {
        "id": "board_rotation",
        "name": "板块轮动接力",
        "tech_name": "板块5日涨幅排名跃升",
        "category": "情绪题材",
        "period": "波段",
        "risk": 3,
        "summary": "排名提升最快的板块，资金正在切换的方向",
        "explain": (
            "A 股资金总在板块间轮动：今天炒完 AI 明天炒医药。比起追已经涨高的板块，"
            "找'5 日涨幅排名跃升最快'的板块——资金刚开始切入、位置还低，"
            "再从中选量价配合的个股接力。"
            "\n适合：擅长跟踪资金流向的投资者。失效场景：轮动太快的市场里容易两头挨打，"
            "确认板块有量能持续放大再介入。"
        ),
        "condition": None,
        "special": "board_rotation",
    },
    {
        "id": "ai_sentiment",
        "name": "AI情绪利好共振",
        "tech_name": "情绪分>70+技术面健康",
        "category": "情绪题材",
        "period": "波段",
        "risk": 2,
        "summary": "AI 阅读新闻打出高情绪分，且技术面未超买",
        "explain": (
            "让 AI 通读个股近期新闻并打情绪分（0~100）：>70 说明消息面有实质利好"
            "（业绩/订单/政策催化），再要求 RSI<70（技术面未超买、利好还没被透支），"
            "组合起来=基本面催化的起点而非尾声。"
            "\n使用提示：情绪分只覆盖'已分析过'的股票——在个股页或自选页点"
            "'AI 情绪诊断'积累当日分数后，本策略才有候选池。"
            "\n失效场景：利好兑现日（高分+高位放量）往往是出货点，注意位置。"
        ),
        "no_backtest": "AI 情绪分按日生成且仅覆盖已分析股票，无历史数据，暂不支持回测",
        "condition": {
            "all": [
                {"factor": "senti_score", "op": ">", "value": 70},
                {"factor": "rsi14", "op": "<", "value": 70},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "popularity_surge",
        "name": "人气飙升",
        "tech_name": "股吧人气排名跃升",
        "category": "情绪题材",
        "period": "短线",
        "risk": 3,
        "summary": "人气榜排名单日大幅跃升的话题股（榜单前100名）",
        "explain": (
            "股吧人气榜是散户关注度的温度计（数据源只公布前 100 名）。"
            "一只股票的名次单日跃升 30 名以上、或从榜外新冲进前 100，"
            "说明有话题正在发酵——题材股的启动常先于股价反映在人气上。"
            "\n适合：题材情绪玩家做观察池。失效场景：人气巅峰常对应短期股价巅峰"
            "（散户全知道了还能卖给谁），冲高回落风险大，严格止损。"
        ),
        "no_backtest": "人气榜为每日快照，无完整历史，暂不支持回测",
        "condition": {
            "all": [
                {"factor": "pop_jump", "op": ">=", "value": 30},
                {"factor": "pop_rank", "op": "<=", "value": 100},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    # ============ 盘中走势类（5分钟线，对标同花顺"尾盘选股"等苛刻条件） ============
    {
        "id": "late_rally",
        "name": "尾盘抢筹",
        "tech_name": "最后30分钟放量拉升",
        "category": "盘中走势",
        "period": "短线",
        "risk": 4,
        "summary": "尾盘最后半小时涨超1.5%且量能集中，资金抢在收盘前进场",
        "explain": (
            "14:30 后的拉升最'诚实'——临近收盘没有时间做 T 洗盘，敢在尾盘"
            "集中买入的资金通常看的是次日。本策略要求尾盘 30 分钟涨幅>1.5%、"
            "尾盘量能占全天>20%（正常匀速是 12.5%）、且全天收红，"
            "过滤掉暴跌后的尾盘反抽。"
            "\n适合：次日开盘竞价观察池。失效场景：尾盘拉升也可能是做收盘价"
            "（出货前的画图），次日低开不补就要走。"
        ),
        "no_backtest": "5分钟线从接入日起逐日积累，满60个交易日后开放回测",
        "condition": {
            "all": [
                {"factor": "late30_pct", "op": ">", "value": 1.5},
                {"factor": "late_vol_pct", "op": ">", "value": 20},
                {"factor": "pct_change", "op": ">", "value": 0},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "late_dive",
        "name": "尾盘跳水",
        "tech_name": "最后30分钟急跌",
        "category": "盘中走势",
        "period": "短线",
        "risk": 5,
        "summary": "尾盘最后半小时急跌超2%——避雷预警与次日低吸观察两用",
        "explain": (
            "尾盘跳水有两种解读：一是有资金提前知道利空、抢跑出逃（避雷信号）；"
            "二是市场恐慌的错杀、次日常有修复（低吸机会）。区分关键看消息面："
            "选出后先查个股新闻，无利空的跳水才考虑接。"
            "\n适合：持仓避雷自查 + 激进者的次日低吸观察池。"
            "失效场景：阴跌途中的尾盘跳水是常态而非机会，先看趋势再看日内。"
        ),
        "no_backtest": "5分钟线从接入日起逐日积累，满60个交易日后开放回测",
        "condition": {
            "all": [
                {"factor": "late30_pct", "op": "<", "value": -2},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "am_strong_hold",
        "name": "早强守势",
        "tech_name": "早盘冲高全天站稳均价线",
        "category": "盘中走势",
        "period": "短线",
        "risk": 3,
        "summary": "早盘1小时涨超2%且全天站稳分时均价、收在高位",
        "explain": (
            "早盘冲高不稀奇，难的是冲高后'不回头'：全天收盘价站在分时均价上方、"
            "收盘位置在全天振幅的上半区，说明买方接力充足、没有冲高出货。"
            "这种'强势整固'的分时形态，次日惯性冲高的概率较大。"
            "\n适合：短线接力客。失效场景：大盘尾盘跳水会破坏个股分时结构，"
            "看信号时记得看一眼指数。"
        ),
        "no_backtest": "5分钟线从接入日起逐日积累，满60个交易日后开放回测",
        "condition": {
            "all": [
                {"factor": "am60_pct", "op": ">", "value": 2},
                {"factor": "above_vwap", "op": "is_true"},
                {"factor": "close_position", "op": ">", "value": 60},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
    {
        "id": "vwap_climbing",
        "name": "重心上移",
        "tech_name": "下午均价高于上午均价",
        "category": "盘中走势",
        "period": "波段",
        "risk": 2,
        "summary": "分时重心全天稳步上移且收盘站上均价，资金温和持续吸筹",
        "explain": (
            "比起急拉，'重心上移'更像耐心资金的手笔：下午成交均价高于上午、"
            "收盘站上全天均价，全天买方一直占着主动但不急不躁。"
            "这种分时形态配合温和放量，常出现在波段启动的初期。"
            "\n适合：左侧潜伏型波段客。失效场景：缩量的重心上移可能只是"
            "无人交易的漂移，要求量比>0.8 过滤死水盘。"
        ),
        "no_backtest": "5分钟线从接入日起逐日积累，满60个交易日后开放回测",
        "condition": {
            "all": [
                {"factor": "vwap_climb", "op": "is_true"},
                {"factor": "above_vwap", "op": "is_true"},
                {"factor": "pct_change", "op": ">", "value": 0},
                {"factor": "vol_ratio", "op": ">", "value": 0.8},
                {"factor": "is_st", "op": "is_false"},
            ]
        },
    },
]


def get_strategy(strategy_id: str) -> dict | None:
    return next((s for s in STRATEGIES if s["id"] == strategy_id), None)


def available_strategies() -> list[dict]:
    return [s for s in STRATEGIES if s.get("available", True)]
