from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin


def normalize_chat_endpoint(base_url: str | None) -> str:
    """统一 OpenAI 兼容 chat completions 终点，避免不同模块各自拼接导致漂移。"""
    stripped = (base_url or "").strip()
    if not stripped:
        return "https://api.openai.com/v1/chat/completions"

    normalized = stripped.rstrip("/") + "/"
    if normalized.endswith("/chat/completions/"):
        return normalized[:-1]
    if normalized.endswith("/v1/"):
        return urljoin(normalized, "chat/completions")
    return urljoin(normalized, "v1/chat/completions")


def extract_message_content(payload: dict[str, Any]) -> str:
    """从 OpenAI 兼容响应里提取首条文本内容。"""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing choices")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("missing message")

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
        if text_parts:
            return "\n".join(text_parts)

    raise ValueError("missing content")


def extract_json_object(content: str) -> dict[str, Any]:
    """提取并解析模型返回中的 JSON object，兼容 fenced code block 与前后噪声文本。"""
    stripped = content.strip()
    if not stripped:
        raise ValueError("empty content")

    # 兼容 ```json ... ``` 与 ``` ... ``` 两类 fenced 返回。
    fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        stripped = fence_match.group(1).strip()

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("json object not found")

    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("json root is not object")
    return parsed
