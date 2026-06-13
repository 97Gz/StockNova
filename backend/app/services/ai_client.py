"""AI 客户端：OpenAI 兼容协议的最小封装（DeepSeek / Ollama / 中转站通用）。

设计：
- 配置（地址/密钥/模型/温度）全部从设置中心读取，改配置即时生效，无需重启；
- 只封装 chat / chat_json / chat_json_stream 三个动作，业务提示词由调用方组织；
- 流式接口同时回传 reasoning_content（DeepSeek-R1 类推理模型的思考过程）与正文，
  供前端实时展示"AI 正在想什么"；非推理模型没有思考流，正文流同样可见；
- 未配置密钥时抛 AINotConfigured，API 层转成业务错误码引导用户去设置中心。
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

from app.core.database import create_session
from app.services import settings_service

logger = logging.getLogger(__name__)

# 思考/正文增量回调：参数为本次新增的文本片段
DeltaCallback = Callable[[str], Awaitable[None]]


class AINotConfigured(RuntimeError):
    """AI 未配置（缺 API Key）。"""


class AIClient:
    """每次调用现读配置（调用频率低，省去配置变更通知的复杂度）。"""

    def _load_config(self) -> dict:
        with create_session() as db:
            return {
                "base_url": str(settings_service.get_value(db, "ai.base_url")).rstrip("/"),
                "api_key": str(settings_service.get_value(db, "ai.api_key")),
                "model": str(settings_service.get_value(db, "ai.model")),
                "temperature": float(settings_service.get_value(db, "ai.temperature")),
            }

    async def chat(self, system: str, user: str, *, timeout: float = 60.0) -> str:
        """单轮对话，返回助手文本。未配置密钥时抛 AINotConfigured。"""
        cfg = self._load_config()
        if not cfg["api_key"]:
            raise AINotConfigured("AI 服务未配置，请到 设置中心 → AI 接入 填写 API Key")

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{cfg['base_url']}/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={
                    "model": cfg["model"],
                    "temperature": cfg["temperature"],
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return str(data["choices"][0]["message"]["content"])

    @staticmethod
    def _parse_json(text: str) -> dict:
        """从模型输出中提取 JSON（容忍 ```json 代码块外壳与前后杂文）。"""
        cleaned = text.strip()
        m = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, flags=re.S)
        if m:
            cleaned = m.group(1)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            # 再宽容一档：找第一个 { 到最后一个 } 之间的内容
            start, end = cleaned.find("{"), cleaned.rfind("}")
            if start >= 0 and end > start:
                return json.loads(cleaned[start : end + 1])
            raise ValueError(f"AI 返回的不是合法 JSON：{text[:200]}") from e

    async def chat_json(self, system: str, user: str, *, timeout: float = 60.0) -> dict:
        """要求模型输出 JSON 并解析。解析失败抛 ValueError（调用方决定是否重试）。"""
        text = await self.chat(system, user, timeout=timeout)
        return self._parse_json(text)

    async def chat_json_stream(
        self,
        system: str,
        user: str,
        *,
        timeout: float = 180.0,
        on_thinking: DeltaCallback | None = None,
        on_content: DeltaCallback | None = None,
    ) -> dict:
        """流式对话 + JSON 解析，回调实时透出思考过程。

        返回 {"data": 解析后的 JSON, "thinking": 完整思考文本}。
        - DeepSeek-R1 / deepseek-reasoner 等推理模型：delta 里带 reasoning_content
          （思考流）与 content（正文流）两条通道；
        - 普通模型：只有 content，思考流为空——前端按"无思考过程"展示即可；
        - SSE 单行解析失败直接跳过（个别厂商会混发心跳/注释行）。
        """
        cfg = self._load_config()
        if not cfg["api_key"]:
            raise AINotConfigured("AI 服务未配置，请到 设置中心 → AI 接入 填写 API Key")

        thinking_parts: list[str] = []
        content_parts: list[str] = []
        # read 超时给单流读取（推理模型思考久，间隔可能拉长到数十秒）
        timeout_cfg = httpx.Timeout(30.0, read=timeout, pool=30.0)

        async with httpx.AsyncClient(timeout=timeout_cfg) as client:
            async with client.stream(
                "POST",
                f"{cfg['base_url']}/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={
                    "model": cfg["model"],
                    "temperature": cfg["temperature"],
                    "stream": True,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    rc = delta.get("reasoning_content")
                    if rc:
                        thinking_parts.append(rc)
                        if on_thinking is not None:
                            await on_thinking(rc)
                    c = delta.get("content")
                    if c:
                        content_parts.append(c)
                        if on_content is not None:
                            await on_content(c)

        text = "".join(content_parts)
        return {"data": self._parse_json(text), "thinking": "".join(thinking_parts)}


async def test_connection() -> dict:
    """设置中心"测试连接"按钮：发一条最小对话验证配置可用。"""
    client = AIClient()
    reply = await client.chat("你是连通性测试助手。", "请只回复两个字：正常", timeout=30.0)
    return {"ok": True, "reply": reply[:50]}
