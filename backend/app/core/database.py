"""数据库连接管理：SQLite（业务库）+ DuckDB（行情分析库）。

双库分工（架构文档 02 第 3 节）：
- SQLite：自选股、持仓、策略配置、AI 报告等"业务记录"，读写频繁但数据量小。
- DuckDB：全市场日线、快照等"分析数据"，量大（千万行级），列存结构让
  全市场扫描/回测比 SQLite 快一个量级。

两者都是嵌入式文件数据库：不需要安装任何数据库服务，应用打开文件即用。
"""

import duckdb
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import config

# ---------------- SQLite（业务库，经 SQLAlchemy ORM 访问） ----------------

# check_same_thread=False：允许 FastAPI 的多个工作线程共用连接池中的连接
_engine = create_engine(
    f"sqlite:///{config.sqlite_path}",
    connect_args={"check_same_thread": False},
)

# Session 工厂：每个请求创建一个独立会话，用完即关（见 get_db_session 依赖）
_session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def get_db_session():
    """FastAPI 依赖：为单个请求提供 SQLite 会话，请求结束自动关闭。

    用法：在路由函数参数里声明 `db: Session = Depends(get_db_session)`。
    yield 之前的代码在请求开始时执行，之后的代码在请求结束时执行。
    """
    session: Session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def create_session() -> Session:
    """给服务层（非请求上下文，如后台任务）创建独立会话。

    用法：`with create_session() as db: ...`（with 块结束自动关闭）。
    """
    return _session_factory()


def init_sqlite() -> None:
    """应用启动时调用：确保数据目录存在并创建所有 ORM 表（已存在则跳过）。"""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    # 延迟导入避免循环依赖：orm 模块里定义了所有表结构
    from app.models.orm import OrmBase

    OrmBase.metadata.create_all(_engine)


# ---------------- DuckDB（行情分析库） ----------------

# 行情库表结构版本：结构变更时 +1，init_duckdb 会自动清库重建。
# 行情库是"可再生数据"（随时能从数据源重新同步），所以允许破坏式迁移；
# 业务库（SQLite）绝不这么做。
DUCKDB_SCHEMA_VERSION = "2"

# 全部行情表的建表语句（每张表的用途见行内注释）
_DUCKDB_TABLES: dict[str, str] = {
    # 股票主档：全 A 股基础信息，搜索与列表展示的数据源
    "stock_basics": """
        CREATE TABLE stock_basics (
            symbol     VARCHAR,   -- 股票代码，如 600519
            name       VARCHAR,   -- 股票名称，如 贵州茅台
            exchange   VARCHAR,   -- 交易所：SH / SZ / BJ
            market     VARCHAR,   -- 板块：主板 / 创业板 / 科创板 / 北交所
            pinyin     VARCHAR,   -- 拼音首字母，如 gzmt（搜索用）
            is_st      BOOLEAN,   -- 是否 ST（按名称判断）
            status     VARCHAR,   -- L=在市 / D=退市（防幸存者偏差）
            updated_at TIMESTAMP
        )
    """,
    # 日线行情：回测与策略计算的核心数据，10 年全 A 约 1300 万行。
    # 注：大表不建主键（DuckDB 的主键索引在千万行级会拖慢批量写入），
    # 唯一性由同步服务"先删后插"保证；查询性能靠列存 + min/max 剪枝。
    "daily_bars": """
        CREATE TABLE daily_bars (
            symbol     VARCHAR,   -- 股票代码
            trade_date DATE,      -- 交易日
            open       DOUBLE,    -- 开盘价（不复权）
            high       DOUBLE,
            low        DOUBLE,
            close      DOUBLE,
            volume     DOUBLE,    -- 成交量（手）
            amount     DOUBLE,    -- 成交额（元）
            pct_change DOUBLE,    -- 涨跌幅（%）
            turnover   DOUBLE,    -- 换手率（%）
            adj_factor DOUBLE     -- 后复权因子：后复权价 = 不复权价 × 因子
        )
    """,
    # 指数日线：上证指数/沪深300 等核心指数（无复权概念）
    "index_daily": """
        CREATE TABLE index_daily (
            symbol     VARCHAR,   -- 指数代码，如 000001（上证指数）
            trade_date DATE,
            open       DOUBLE,
            high       DOUBLE,
            low        DOUBLE,
            close      DOUBLE,
            volume     DOUBLE,
            amount     DOUBLE,
            pct_change DOUBLE,
            turnover   DOUBLE
        )
    """,
    # 板块列表：东财行业板块 + 概念板块
    "boards": """
        CREATE TABLE boards (
            code       VARCHAR,   -- 板块代码，如 BK0475
            name       VARCHAR,   -- 板块名，如 银行
            type       VARCHAR,   -- industry / concept
            updated_at TIMESTAMP
        )
    """,
    # 板块成分：板块 ↔ 股票多对多关系
    "board_members": """
        CREATE TABLE board_members (
            board_code VARCHAR,
            symbol     VARCHAR
        )
    """,
    # 板块日线：行业/概念板块指数的日 K（热力图、板块轮动分析用）
    "board_daily": """
        CREATE TABLE board_daily (
            symbol     VARCHAR,   -- 板块代码
            trade_date DATE,
            open       DOUBLE,
            high       DOUBLE,
            low        DOUBLE,
            close      DOUBLE,
            volume     DOUBLE,
            amount     DOUBLE,
            pct_change DOUBLE,
            turnover   DOUBLE
        )
    """,
    # 每日估值快照：PE/PB/市值（基本面策略与个股详情用）
    "fundamentals_daily": """
        CREATE TABLE fundamentals_daily (
            symbol     VARCHAR,
            trade_date DATE,
            pe_ttm     DOUBLE,    -- 市盈率 TTM（亏损为负/停牌为 0）
            pb         DOUBLE,    -- 市净率
            total_mv   DOUBLE,    -- 总市值（元）
            circ_mv    DOUBLE,    -- 流通市值（元）
            turnover   DOUBLE     -- 换手率（%）
        )
    """,
    # 交易日历：开市判定、回测日期轴（来自新浪，含未来已排期日期）
    "trade_calendar": """
        CREATE TABLE trade_calendar (
            trade_date DATE
        )
    """,
    # 元信息表：schema 版本等
    "_meta": """
        CREATE TABLE _meta (
            key   VARCHAR,
            value VARCHAR
        )
    """,
}

# M5 扩展数据表：独立于核心行情表做"增量补建"（见 init_duckdb 注释）。
# 这些数据从上线日起逐日积累，不做历史回填（数据源不提供完整历史）。
_DUCKDB_EXT_TABLES: dict[str, str] = {
    # 每日资金流快照：来自东财全市场资金流排行（含多周期累计与股息率）
    "fund_flow_daily": """
        CREATE TABLE IF NOT EXISTS fund_flow_daily (
            trade_date  DATE,
            symbol      VARCHAR,
            main_net    DOUBLE,   -- 今日主力净流入（元，大单+超大单口径）
            main_pct    DOUBLE,   -- 今日主力净占比（%）
            net_3d      DOUBLE,   -- 3 日主力净流入累计（元）
            net_5d      DOUBLE,   -- 5 日主力净流入累计（元）
            net_10d     DOUBLE,   -- 10 日主力净流入累计（元）
            dv_ttm      DOUBLE    -- 股息率 TTM（%，与资金流同请求取得的快照）
        )
    """,
    # 龙虎榜明细：交易所披露的大额成交席位（一只股票一天可能因多个原因上榜多行）
    "dragon_tiger": """
        CREATE TABLE IF NOT EXISTS dragon_tiger (
            trade_date  DATE,
            symbol      VARCHAR,
            name        VARCHAR,
            close       DOUBLE,
            pct_change  DOUBLE,
            net_amt     DOUBLE,   -- 龙虎榜净买入额（元）
            buy_amt     DOUBLE,
            sell_amt    DOUBLE,
            turnover    DOUBLE,   -- 换手率（%）
            reason      VARCHAR,  -- 上榜解读（东财 EXPLAIN，如"2家机构买入"）
            has_inst    BOOLEAN   -- 解读中是否出现机构买入
        )
    """,
    # 业绩预告：东财数据中心（按报告期全量刷新，量小）
    "earnings_forecast": """
        CREATE TABLE IF NOT EXISTS earnings_forecast (
            symbol       VARCHAR,
            name         VARCHAR,
            report_date  DATE,     -- 报告期（如 2026-06-30）
            notice_date  DATE,     -- 公告日
            predict_type VARCHAR,  -- 预增/略增/扭亏/预减/略减/首亏/续亏/续盈
            amp_lower    DOUBLE,   -- 变动幅度下限（%）
            amp_upper    DOUBLE,   -- 变动幅度上限（%）
            content      VARCHAR   -- 预告原文摘要
        )
    """,
    # 股吧人气榜：每日快照（排名 + 较昨日变化），短线情绪温度计
    "popularity_rank": """
        CREATE TABLE IF NOT EXISTS popularity_rank (
            rank_date  DATE,
            symbol     VARCHAR,
            rank       INTEGER,   -- 当前人气名次（1 为最热）
            rank_chg   INTEGER    -- 较昨日名次变化（正=上升）
        )
    """,
    # 5 分钟K线：每个交易日盘后同步当天 48 根，从接入日起逐日积累。
    # 用途：盘中形态因子（尾盘拉升/跳水、早盘强势、分时重心等）。
    # 全市场一天约 28 万行、压缩后 5~8MB，一年约 2GB——DuckDB 列存毫无压力。
    "minute_bars": """
        CREATE TABLE IF NOT EXISTS minute_bars (
            symbol     VARCHAR,    -- 股票代码
            dt         TIMESTAMP,  -- K线结束时间（如 09:35 表示 09:30~09:35）
            trade_date DATE,       -- 所属交易日（冗余列，便于按日剪枝查询）
            open       DOUBLE,     -- 不复权价（盘中因子只看当日相对走势，无需复权）
            high       DOUBLE,
            low        DOUBLE,
            close      DOUBLE,
            volume     DOUBLE,     -- 成交量（手）
            amount     DOUBLE      -- 成交额（元）
        )
    """,
}


def get_duckdb() -> duckdb.DuckDBPyConnection:
    """获取 DuckDB 连接。

    DuckDB 同一时刻只允许一个进程写库；本应用是单进程，
    服务层共享由 market_store 管理的连接即可。
    """
    return duckdb.connect(str(config.duckdb_path))


def init_duckdb() -> None:
    """应用启动时调用：建表；若表结构版本不符则清空重建（行情数据可再生）。

    扩展表（_DUCKDB_EXT_TABLES）单独走 CREATE IF NOT EXISTS 增量补建：
    它们是 M5 后加的、从上线日起逐日积累的数据，不能因核心表版本号
    没变就不建，更不能为了建它们而升版本号清掉几小时才同步完的行情库。
    """
    config.data_dir.mkdir(parents=True, exist_ok=True)
    with get_duckdb() as conn:
        # 读取已存在库的 schema 版本（_meta 表不存在说明是旧版/全新库）
        version = ""
        try:
            row = conn.execute("SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()
            version = row[0] if row else ""
        except duckdb.CatalogException:
            pass

        if version != DUCKDB_SCHEMA_VERSION:
            # 破坏式迁移：删掉全部已知表后按当前结构重建
            for table in _DUCKDB_TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            for ddl in _DUCKDB_TABLES.values():
                conn.execute(ddl)
            conn.execute("INSERT INTO _meta VALUES ('schema_version', ?)", [DUCKDB_SCHEMA_VERSION])

        # 扩展表：已存在则跳过（幂等）
        for ddl in _DUCKDB_EXT_TABLES.values():
            conn.execute(ddl)
