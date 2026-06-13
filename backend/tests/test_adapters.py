"""适配器纯解析逻辑的单元测试（不发真实网络请求）。"""

from app.adapters.eastmoney import classify_market, to_secid
from app.adapters.sina_quote import _parse_line as parse_sina
from app.adapters.sina_quote import to_sina_code
from app.adapters.tencent_quote import _parse_line as parse_tencent
from app.adapters.tencent_quote import to_tencent_code

# 腾讯接口真实返回样例（2026-06-12 实测抓取，截断到必要字段数）
TENCENT_LINE = (
    'v_sh600519="1~贵州茅台~600519~1291.91~1279.00~1271.18~50495~24976~25519'
    + "~" * 21  # 买卖五档等占位字段
    + "~20260612150500~12.91~1.01~1295.00~1265.01~1291.91/50495/6477910214~50495~647791"
    "~0.40~19.52~~1295.00~1265.01~2.34~16149.93~16149.93~6.03~1406.90~1151.10~1.63"
    '~"'
)

# 新浪接口真实返回样例（字段为今开/昨收/最新价/最高/最低/.../日期/时间）
SINA_LINE = (
    'var hq_str_sh600519="贵州茅台,1271.180,1279.000,1291.910,1295.000,1265.010,'
    "1291.900,1291.910,5049500,6477910214.000,100,1291.900,200,1291.890,300,1291.880,"
    "400,1291.750,500,1291.700,100,1291.910,200,1292.000,300,1292.320,400,1292.330,"
    '500,1292.380,2026-06-12,15:00:00,00,";'
)


class TestTencentParse:
    def test_parse_basic_fields(self):
        quote = parse_tencent(TENCENT_LINE)
        assert quote is not None
        assert quote.symbol == "600519"
        assert quote.name == "贵州茅台"
        assert quote.price == 1291.91
        assert quote.prev_close == 1279.00
        assert quote.open == 1271.18
        assert quote.pct_change == 1.01
        assert quote.ts == "2026-06-12 15:05:00"

    def test_unit_conversion(self):
        """成交额万元→元、市值亿→元。"""
        quote = parse_tencent(TENCENT_LINE)
        assert quote.amount == 647791 * 1e4
        assert quote.total_mv == 16149.93 * 1e8
        assert quote.pb == 6.03

    def test_garbage_line_returns_none(self):
        assert parse_tencent("") is None
        assert parse_tencent("v_sh600519=") is None
        assert parse_tencent('v_x="a~b~c"') is None


class TestSinaParse:
    def test_parse_basic_fields(self):
        quote = parse_sina(SINA_LINE)
        assert quote is not None
        assert quote.symbol == "600519"
        assert quote.name == "贵州茅台"
        assert quote.price == 1291.91
        assert quote.prev_close == 1279.0
        assert quote.ts == "2026-06-12 15:00:00"

    def test_derived_change(self):
        """新浪不直接给涨跌幅，应由价格推算。"""
        quote = parse_sina(SINA_LINE)
        assert abs(quote.change - 12.91) < 0.001
        assert abs(quote.pct_change - 1.01) < 0.01

    def test_volume_unit(self):
        """新浪成交量单位是股，应转为手（÷100）与其他源统一。"""
        quote = parse_sina(SINA_LINE)
        assert quote.volume == 50495.0


class TestCodeMapping:
    def test_secid(self):
        assert to_secid("600519") == "1.600519"  # 沪市
        assert to_secid("000001") == "0.000001"  # 深市
        assert to_secid("832000") == "0.832000"  # 北交所

    def test_market_classify(self):
        assert classify_market("600519") == "主板"
        assert classify_market("300750") == "创业板"
        assert classify_market("688981") == "科创板"
        assert classify_market("832000") == "北交所"

    def test_quote_code_prefix(self):
        assert to_tencent_code("600519") == "sh600519"
        assert to_tencent_code("000001") == "sz000001"
        assert to_tencent_code("832000") == "bj832000"
        assert to_sina_code("600519") == "sh600519"
        assert to_sina_code("430047") == "bj430047"


class TestPinyin:
    def test_abbr(self):
        from app.services.sync_service import make_pinyin_abbr

        assert make_pinyin_abbr("贵州茅台") == "gzmt"
        assert make_pinyin_abbr("中国平安") == "zgpa"

    def test_abbr_with_symbols(self):
        """带 ST/星号/字母的名称只保留字母数字。"""
        from app.services.sync_service import make_pinyin_abbr

        assert make_pinyin_abbr("*ST中基") == "stzj"
        assert make_pinyin_abbr("TCL科技") == "tclkj"
