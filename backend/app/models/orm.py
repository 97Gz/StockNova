"""SQLite 业务库的 ORM 表定义。

M0 阶段只建 settings 表（验证建库流程跑通）；
后续里程碑按架构文档 02 第 3.1 节逐步补充：
watchlist / holdings / strategies / backtest / news / ai_reports / terms 等。

所有业务表统一带 user_id 字段（默认 0）：当前单用户恒为 0，
这是访谈确认的"预留多用户演进空间"决策，未来加登录时无需改表。
"""

from datetime import datetime

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class OrmBase(DeclarativeBase):
    """所有 ORM 表的基类：database.init_sqlite() 据此统一建表。"""


class Setting(OrmBase):
    """应用配置表（KV 结构）。

    "零配置文件"原则的落地：AI 接入、数据源、定时任务时间、主题偏好等
    全部以 JSON 字符串存在这里，由设置中心页面读写。
    key 形如 "ai.base_url"、"schedule.daily_sync_time"，用点号分区。
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    # 统一存 JSON 字符串：字符串/数字/布尔/对象都序列化后存放，读取时反序列化
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 多用户演进预留位：当前恒为 0
    user_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.now, onupdate=datetime.now)


class SyncLog(OrmBase):
    """数据同步日志：每次"历史初始化 / 每日增量"任务一条记录。

    设置中心的"同步历史"列表、以及验收标准里"增量同步有记录可查"都靠它。
    """

    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 任务类型：init_history（全量初始化）/ daily（每日增量）/ rebuild（清库重建）
    task_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # 任务状态：running / success / failed / cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # 任务量统计：total=计划处理数，done=成功数，failed=失败数
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    # 摘要信息：失败原因 / 本次写入行数等
    message: Mapped[str] = mapped_column(Text, default="")


class WatchlistItem(OrmBase):
    """自选股清单（M2）。

    sort_order 决定页面展示顺序（新加的排最后，未来支持拖拽排序）；
    note 留给用户写备注（如"30 元以下补仓"）。
    """

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    note: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # 多用户演进预留位：当前恒为 0
    user_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


class StrategySignal(OrmBase):
    """策略跑批信号存档（M3）。

    每日盘后跑批（或手动运行）把每个策略的命中结果落一批记录，
    供"今日策略信号"卡、历史追溯、以及未来的胜率统计使用。
    """

    __tablename__ = "strategy_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 信号所属交易日（yyyy-MM-dd）
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    # 信号当日收盘价（胜率统计的成本基准）
    close: Mapped[float] = mapped_column(default=0.0)
    # 命中原因明细（JSON 数组字符串，人话化文案）
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    # 多用户演进预留位：当前恒为 0
    user_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


class CustomStrategy(OrmBase):
    """用户自定义策略（M3 可视化构建器保存的条件树）。"""

    __tablename__ = "custom_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    # engine.py 语法的条件树（JSON 字符串）
    condition_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 多用户演进预留位：当前恒为 0
    user_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.now, onupdate=datetime.now)


class BacktestRun(OrmBase):
    """回测运行记录（M4）：参数与完整结果以 JSON 文本归档，便于回看与对比。"""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # snapshot（策略时光机）/ rebalance（定期调仓）
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


class NewsSentiment(OrmBase):
    """AI 个股情绪分析缓存（M5）。

    一只股票一天最多分析一次（新闻盘中变化不大、省 token）；
    再次请求直接读缓存，次日自动失效（按 trade_date 比对）。
    """

    __tablename__ = "news_sentiment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    # 分析基准日（yyyy-MM-dd，自然日：新闻流不分交易日）
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    # 情绪分 0~100（50 中性，>70 偏多，<30 偏空）
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    # 利好 / 利空 / 中性
    label: Mapped[str] = mapped_column(String(10), nullable=False)
    # 一句话结论
    summary: Mapped[str] = mapped_column(Text, default="")
    # 利好/利空要点（JSON：{"positive": [...], "negative": [...]}）
    points_json: Mapped[str] = mapped_column(Text, default="{}")
    # 参与分析的新闻标题（JSON 数组，结果可追溯）
    news_json: Mapped[str] = mapped_column(Text, default="[]")
    user_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


class DiagnosisRun(OrmBase):
    """多角色 AI 诊股记录（M6）。

    一次诊股 = 四位分析师并行 → 多空辩论 → 首席决策的完整工作流。
    stages_json 存每个阶段的输出（前端工作流页可回放），
    result_json 存首席的最终结论（评级/仓位/止损/理由/风险）。
    """

    __tablename__ = "diagnosis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(30), default="")
    # running / done / failed
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="running")
    # 各阶段输出：{"tech": {...}, "fund": {...}, "news": {...}, "fundamental": {...},
    #             "bull": {...}, "bear": {...}}
    stages_json: Mapped[str] = mapped_column(Text, default="{}")
    # 首席最终结论 JSON
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    # 失败原因（status=failed 时）
    error: Mapped[str] = mapped_column(Text, default="")
    # 总耗时（秒）与使用的模型（结果可追溯）
    cost_seconds: Mapped[float] = mapped_column(default=0.0)
    model: Mapped[str] = mapped_column(String(50), default="")
    user_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


class Holding(OrmBase):
    """持仓记录（M6）。

    用户手工录入的真实持仓（股数+成本价），持仓诊断页据此计算
    市值/浮动盈亏/仓位占比，并在发起 AI 诊断时把持仓上下文注入工作流，
    让首席决策官给出针对性的「割/守/补」建议。
    """

    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    # 持股数量（股）：A 股最小交易单位 100 股，但碎股（送转）也允许录入
    shares: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 摊薄成本价（元/股）：用户自己按含费口径填写
    cost_price: Mapped[float] = mapped_column(nullable=False, default=0.0)
    # 备注（如"2024-03 建仓，止损 35"）
    note: Mapped[str] = mapped_column(Text, default="")
    # 多用户演进预留位：当前恒为 0
    user_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.now, onupdate=datetime.now)


class InitProgress(OrmBase):
    """历史初始化的逐只股票进度表 —— 断点续传的核心。

    初始化开始时为每只股票插入一行 pending；
    每拉完一只改为 done（记录已覆盖到的最后日期）；失败标记 failed 待重试。
    中断后重新开始时，只处理 status != 'done' 的股票，从而实现断点续传。
    """

    __tablename__ = "init_progress"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    # pending（待拉取）/ done（已完成）/ failed（失败，可重试）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # 已入库数据覆盖到的最后交易日（yyyy-MM-dd），增量修复时参考
    last_date: Mapped[str] = mapped_column(String(10), default="")
    # 最近一次失败的原因（便于排查被限频/退市等情况）
    error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(default=datetime.now, onupdate=datetime.now)
