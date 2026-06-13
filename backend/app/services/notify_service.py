"""消息推送服务：把 AI 研报 / 重要提醒推送到 IM 与邮箱。

支持四个通道（按设置项开关，配了哪个推哪个）：
- 企业微信群机器人（wecom）：群里加机器人拿到的 Webhook，发 markdown 消息
- 飞书自定义机器人（feishu）：群自定义机器人 Webhook，发 text 消息（稳）
- Telegram Bot（telegram）：BotFather 给的 token + 目标 chat_id，sendMessage
- 邮件（email）：标准 SMTP over SSL，HTML 正文

设计要点：
- 统一入口 push(title, markdown, summary)：遍历已启用通道逐个发，
  单通道失败只记日志、不影响其它通道，也不抛给上游（定时流水线不能被推送拖死）；
- 配置全部走 settings_service（零配置文件），Webhook/token/密码等敏感项打码存储；
- 所有网络与 SMTP 调用带超时，避免挂起。
"""

import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import formataddr

import httpx

from app.core.database import create_session
from app.services import settings_service

logger = logging.getLogger(__name__)

# 单次推送的网络超时（秒）：IM Webhook 一般很快，给足冗余
_HTTP_TIMEOUT = 12.0


def _cfg() -> dict:
    """一次性读出全部推送相关配置（明文，供发送使用）。"""
    with create_session() as db:
        keys = [
            "notify.wecom_webhook",
            "notify.feishu_webhook",
            "notify.telegram_token",
            "notify.telegram_chat_id",
            "notify.email_enabled",
            "notify.email_host",
            "notify.email_port",
            "notify.email_user",
            "notify.email_password",
            "notify.email_to",
        ]
        return {k.split(".", 1)[1]: settings_service.get_value(db, k) for k in keys}


def enabled_channels() -> list[str]:
    """当前已配置（可用）的通道列表，用于前端展示与测试。"""
    cfg = _cfg()
    channels: list[str] = []
    if cfg.get("wecom_webhook"):
        channels.append("wecom")
    if cfg.get("feishu_webhook"):
        channels.append("feishu")
    if cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
        channels.append("telegram")
    if cfg.get("email_enabled") and cfg.get("email_host") and cfg.get("email_to"):
        channels.append("email")
    return channels


# ---------------- 各通道发送实现 ----------------


async def _send_wecom(webhook: str, title: str, markdown: str) -> None:
    """企业微信群机器人：markdown 消息（标题加粗 + 正文）。"""
    content = f"**{title}**\n{markdown}"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            webhook, json={"msgtype": "markdown", "markdown": {"content": content[:4000]}}
        )
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"企业微信返回 {data}")


async def _send_feishu(webhook: str, title: str, text: str) -> None:
    """飞书自定义机器人：text 消息（最稳，无需配置卡片模板）。"""
    body = {"msg_type": "text", "content": {"text": f"{title}\n{text}"[:4000]}}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(webhook, json=body)
        data = resp.json()
        # 飞书成功返回 StatusCode=0 或 code=0（不同版本字段名不同）
        if data.get("StatusCode", data.get("code", 0)) not in (0, None):
            raise RuntimeError(f"飞书返回 {data}")


async def _send_telegram(token: str, chat_id: str, text: str) -> None:
    """Telegram Bot sendMessage（Markdown 解析）。"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            url,
            json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "Markdown"},
        )
        data = resp.json()
        if not data.get("ok", False):
            raise RuntimeError(f"Telegram 返回 {data}")


def _send_email_sync(cfg: dict, title: str, html: str) -> None:
    """SMTP over SSL 发邮件（阻塞，调用方用 to_thread 包装）。"""
    host = str(cfg["email_host"])
    port = int(cfg["email_port"] or 465)
    user = str(cfg["email_user"])
    password = str(cfg["email_password"])
    to_addrs = [a.strip() for a in str(cfg["email_to"]).replace("；", ";").split(";") if a.strip()]
    if not to_addrs:
        raise RuntimeError("收件人为空")

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = title
    msg["From"] = formataddr(("星智股 StockNova", user))
    msg["To"] = ", ".join(to_addrs)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as server:
        server.login(user, password)
        server.sendmail(user, to_addrs, msg.as_string())


# ---------------- 统一入口 ----------------


async def push(title: str, markdown: str, summary: str = "") -> list[dict]:
    """向所有已启用通道推送一条消息。

    返回每个通道的结果 [{channel, ok, error}]，供调用方记日志/前端展示。
    summary：邮件等需要纯文本简述时用；为空则回退用 markdown。
    """
    import asyncio

    cfg = _cfg()
    results: list[dict] = []

    async def run(channel: str, coro) -> None:
        try:
            await coro
            results.append({"channel": channel, "ok": True, "error": ""})
        except Exception as e:  # noqa: BLE001 - 单通道容错边界
            logger.warning("推送通道 %s 失败：%s", channel, e)
            results.append({"channel": channel, "ok": False, "error": str(e)})

    tasks = []
    if cfg.get("wecom_webhook"):
        tasks.append(run("wecom", _send_wecom(cfg["wecom_webhook"], title, markdown)))
    if cfg.get("feishu_webhook"):
        tasks.append(run("feishu", _send_feishu(cfg["feishu_webhook"], title, summary or markdown)))
    if cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
        tg_text = f"*{title}*\n{markdown}"
        tasks.append(
            run(
                "telegram",
                _send_telegram(cfg["telegram_token"], cfg["telegram_chat_id"], tg_text),
            )
        )
    if cfg.get("email_enabled") and cfg.get("email_host") and cfg.get("email_to"):
        pre_style = "font-family:inherit;white-space:pre-wrap"
        html = f"<h3>{title}</h3><pre style='{pre_style}'>{markdown}</pre>"
        tasks.append(run("email", asyncio.to_thread(_send_email_sync, cfg, title, html)))

    if tasks:
        await asyncio.gather(*tasks)
    return results


async def test_push() -> list[dict]:
    """测试推送：向所有已配置通道发一条测试消息，返回各通道结果。"""
    if not enabled_channels():
        return []
    return await push(
        "星智股 StockNova · 推送测试",
        "这是一条测试消息。如果你收到了，说明推送通道配置成功 🎉",
        summary="星智股推送测试：通道配置成功。",
    )
