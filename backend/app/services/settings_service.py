"""设置服务：所有 UI 化配置的唯一读写入口。

设计要点（对应"零配置文件"原则）：
1. DEFAULTS 注册表声明每个配置项的 key / 默认值 / 分组 / 说明，
   前端设置中心直接按分组渲染表单，新增配置项只需在这里加一行。
2. 值以 JSON 存进 SQLite settings 表；没存过的 key 读取时返回默认值。
3. 敏感项（API Key）在"读取全部"时打码返回，只有保存时才写明文。
"""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BizError
from app.models.orm import Setting

# 打码后的占位符：前端拿到它表示"已配置但不可见"；提交它表示"保持原值不变"
MASKED = "••••••••"


@dataclass(frozen=True)
class SettingDef:
    """单个配置项的元数据定义。"""

    key: str  # 形如 "data.request_delay_ms"，点号前缀即分组
    default: Any  # 默认值（决定类型：bool/int/float/str）
    label: str  # 设置中心显示的中文名
    hint: str = ""  # 表单下方的说明文字
    secret: bool = False  # 是否敏感项（读取时打码）


# ---------------- 配置项注册表 ----------------
# 分组约定：data=数据同步 / quotes=实时行情 / ai=AI 接入 / notify=通知（M7 启用）
DEFAULTS: list[SettingDef] = [
    # ---- 数据同步 ----
    SettingDef(
        "data.history_years",
        10,
        "历史数据年数",
        "首次初始化拉取最近 N 年日线，访谈确认默认 10 年",
    ),
    SettingDef(
        "data.history_concurrency",
        6,
        "初始化并发数",
        "同时拉取几只股票的历史数据；过大易被数据源限频（建议 4~8）",
    ),
    SettingDef(
        "data.request_delay_ms",
        150,
        "请求间隔（毫秒）",
        "每个请求之间的最小间隔，防封禁；被限频时可调大",
    ),
    SettingDef(
        "data.daily_sync_enabled",
        True,
        "每日自动增量同步",
        "交易日收盘后自动同步当日数据",
    ),
    SettingDef(
        "data.daily_sync_time",
        "15:35",
        "增量同步时间",
        "格式 HH:MM；A 股 15:00 收盘，数据源一般 15:30 后稳定",
    ),
    # ---- 实时行情 ----
    SettingDef(
        "quotes.source",
        "tencent",
        "实时报价源",
        "tencent=腾讯（推荐，批量快）/ sina=新浪（备用）",
    ),
    SettingDef(
        "quotes.watchlist_interval_s",
        5,
        "自选股刷新间隔（秒）",
        "盘中自选股报价轮询频率",
    ),
    SettingDef(
        "quotes.market_interval_s",
        60,
        "全市场快照间隔（秒）",
        "盘中全市场行情快照刷新频率（驱动涨跌分布/热力图）",
    ),
    # ---- AI 接入（M5 全面启用，配置面板先就绪）----
    SettingDef(
        "ai.base_url",
        "https://api.deepseek.com",
        "API 地址",
        "OpenAI 兼容协议的服务地址；DeepSeek / Ollama / 其他中转均可",
    ),
    SettingDef("ai.api_key", "", "API Key", "服务商控制台获取；仅保存在本机数据库", secret=True),
    SettingDef("ai.model", "deepseek-chat", "模型名称", "如 deepseek-chat / deepseek-reasoner"),
    SettingDef(
        "ai.temperature",
        0.3,
        "温度",
        "0~1，越低输出越稳定；分析类任务建议 0.2~0.4",
    ),
    # ---- 定时 AI 研报 ----
    SettingDef(
        "report.enabled",
        False,
        "启用定时研报",
        "盘后数据同步完成后，自动对自选+持仓批量 AI 诊断并归档到研报库",
    ),
    SettingDef(
        "report.include_watchlist",
        True,
        "包含自选股",
        "定时研报是否覆盖自选股清单",
    ),
    SettingDef(
        "report.include_holdings",
        True,
        "包含持仓股",
        "定时研报是否覆盖持仓（持仓会注入成本/浮亏，给出割/守/补建议）",
    ),
    SettingDef(
        "report.mode",
        "quick",
        "研报模式",
        "quick=快速（核心4分析师，省时省 token）/ deep=深度（全角色完整工作流）",
    ),
    SettingDef(
        "report.max_symbols",
        20,
        "单次最多诊断只数",
        "控制定时研报的 token 消耗与耗时（自选+持仓去重后截断）",
    ),
    # ---- 推送通道（研报/提醒推送到 IM 与邮箱）----
    SettingDef(
        "notify.on_report",
        False,
        "研报生成后推送",
        "定时研报跑完后，把摘要推送到下方已配置的通道",
    ),
    SettingDef(
        "notify.wecom_webhook",
        "",
        "企业微信机器人",
        "企业微信群机器人 Webhook 地址；留空不启用",
        secret=True,
    ),
    SettingDef(
        "notify.feishu_webhook",
        "",
        "飞书机器人",
        "飞书群自定义机器人 Webhook 地址；留空不启用",
        secret=True,
    ),
    SettingDef(
        "notify.telegram_token",
        "",
        "Telegram Bot Token",
        "BotFather 创建机器人得到的 token；需同时填 Chat ID",
        secret=True,
    ),
    SettingDef(
        "notify.telegram_chat_id",
        "",
        "Telegram Chat ID",
        "接收消息的会话 ID（个人/群组）",
    ),
    SettingDef(
        "notify.email_enabled",
        False,
        "启用邮件推送",
        "通过 SMTP(SSL) 发送研报邮件",
    ),
    SettingDef("notify.email_host", "", "SMTP 服务器", "如 smtp.qq.com / smtp.163.com"),
    SettingDef("notify.email_port", 465, "SMTP 端口", "SSL 端口，通常 465"),
    SettingDef("notify.email_user", "", "发件邮箱", "登录账号，同时作为发件人"),
    SettingDef(
        "notify.email_password",
        "",
        "邮箱授权码",
        "多数邮箱需用『授权码』而非登录密码；仅存本机",
        secret=True,
    ),
    SettingDef("notify.email_to", "", "收件邮箱", "多个用分号 ; 分隔"),
    # ---- 组合（账户总资金，编辑入口在持仓页，不在设置中心分组展示）----
    SettingDef(
        "portfolio.total_capital",
        0.0,
        "股票账户总资金",
        "你投入股市的资金总额（元）；填写后系统据此算现金仓位/个股占总资金比例，"
        "并让 AI 结合仓位集中度给出加减仓建议。0 表示未设置。",
    ),
]

_DEF_MAP: dict[str, SettingDef] = {d.key: d for d in DEFAULTS}


def get_value(db: Session, key: str) -> Any:
    """读取单个配置：库里有取库里的，没有返回注册表默认值。"""
    definition = _DEF_MAP.get(key)
    if definition is None:
        raise BizError(40400, f"未知配置项: {key}")
    row = db.get(Setting, key)
    if row is None:
        return definition.default
    return json.loads(row.value_json)


def get_all(db: Session, *, mask_secrets: bool = True) -> list[dict[str, Any]]:
    """读取全部配置（带元数据），供设置中心渲染表单。

    敏感项默认打码：已配置返回 MASKED，未配置返回空串。
    """
    stored = {row.key: json.loads(row.value_json) for row in db.scalars(select(Setting)).all()}
    items: list[dict[str, Any]] = []
    for d in DEFAULTS:
        value = stored.get(d.key, d.default)
        if d.secret and mask_secrets:
            value = MASKED if value else ""
        items.append(
            {
                "key": d.key,
                "value": value,
                "default": d.default,
                "label": d.label,
                "hint": d.hint,
                "secret": d.secret,
                # 类型提示：前端据此渲染开关/数字框/文本框
                "type": type(d.default).__name__,
            }
        )
    return items


def update_values(db: Session, values: dict[str, Any]) -> None:
    """批量保存配置（设置中心点"保存"时调用）。

    校验规则：
    - key 必须在注册表中（防止任意写入）
    - 值类型必须与默认值类型一致（bool/int/float/str）
    - 敏感项提交 MASKED 表示"不修改"，跳过
    """
    for key, value in values.items():
        definition = _DEF_MAP.get(key)
        if definition is None:
            raise BizError(40001, f"未知配置项: {key}")
        if definition.secret and value == MASKED:
            continue  # 打码占位符 = 保持原值

        # 类型校验：int 配置允许传 float 整数（JSON 数字无类型区分）
        expected = type(definition.default)
        if expected is int and isinstance(value, float) and value.is_integer():
            value = int(value)
        if expected is float and isinstance(value, int):
            value = float(value)
        if not isinstance(value, expected):
            raise BizError(
                40001,
                f"配置 {key} 类型错误：期望 {expected.__name__}，收到 {type(value).__name__}",
            )

        row = db.get(Setting, key)
        if row is None:
            row = Setting(key=key, value_json=json.dumps(value, ensure_ascii=False))
            db.add(row)
        else:
            row.value_json = json.dumps(value, ensure_ascii=False)
    db.commit()
