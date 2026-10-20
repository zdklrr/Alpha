from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx


log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是A股公告事件分析助手。请仅基于公告标题和类型判断短期上涨潜力。
输出 JSON 数组，每项包含:
- code: 股票代码
- score: 0-100 分
- reason: 不超过40字
- risk: 不超过30字
要求:
1) 分数体现短线事件驱动强度
2) 若信息不足，给中性分 50 附近
3) 仅输出 JSON，不要额外文字
"""


def _resolve_endpoint() -> tuple[str, str, str]:
    """返回 (base_url, api_key, model)。未正确配置时 api_key 为空。

    兼容任意 OpenAI 兼容端点（DeepSeek / 通义千问 / 智谱 / SiliconFlow /
    LM Studio / llama.cpp server / vLLM 等），只需填 OPENAI_BASE_URL。
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    model = os.getenv("HERMES_MODEL", "gpt-4o-mini").strip()
    # 占位 key（如 .env.example 中 Ollama 方案的 "ollama"）视为未配置真实凭据，
    # 但仍允许请求 —— 本地推理服务通常不校验 key。
    return base_url, api_key, model


def _extract_chat_text(payload: dict[str, Any]) -> str:
    """解析 OpenAI Chat Completions 响应（choices[].message.content）。"""
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("text"):
                chunks.append(str(part["text"]))
        return "".join(chunks)
    return ""


def _parse_json_array(raw: str) -> list[dict[str, Any]]:
    """宽松解析模型返回的 JSON 数组（容忍 ```json 包裹与前后缀说明文字）。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.replace("```", "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if isinstance(parsed, dict):
        return [parsed]
    return list(parsed)


async def _request_completion(
    client: Any,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """调用 OpenAI 兼容端点。

    优先 Chat Completions（几乎所有兼容服务都支持）；
    端点不支持该协议时回退 Responses API（OpenAI 官方 / 新版 Ollama）。
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    chat_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=chat_payload)
    if resp.status_code == 400:
        # 部分服务不支持 response_format，去掉后重试
        chat_payload.pop("response_format", None)
        resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=chat_payload)
    if resp.status_code in (404, 405, 501):
        responses_payload = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        resp = await client.post(f"{base_url}/responses", headers=headers, json=responses_payload)
    resp.raise_for_status()
    data = resp.json()
    return _extract_chat_text(data) or _extract_text(data)


async def score_with_llm(
    notices: list[dict[str, Any]],
    timeout_seconds: int = 40,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """LLM 公告打分。

    返回 (scores, llm_enabled)。任何失败都返回 ( {}, False) 并记录日志，
    绝不把故障伪装成"已启用"，避免错误状态被持久化误导下游复盘。
    """
    base_url, api_key, model = _resolve_endpoint()
    if not api_key:
        return {}, False

    items = [
        {
            "code": n.get("code", ""),
            "name": n.get("name", ""),
            "title": n.get("title", ""),
            "notice_type": n.get("notice_type", ""),
        }
        for n in notices
    ]
    if not items:
        return {}, True

    user_prompt = f"请给以下公告打分:\n{json.dumps(items, ensure_ascii=False)}"

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=True) as client:
            output_text = await _request_completion(
                client, base_url, api_key, model, SYSTEM_PROMPT, user_prompt
            )
        parsed = _parse_json_array(output_text)
        result: dict[str, dict[str, Any]] = {}
        for row in parsed:
            code = str(row.get("code", "")).zfill(6)
            if not code:
                continue
            result[code] = {
                "score": float(row.get("score", 50)),
                "reason": str(row.get("reason", "")),
                "risk": str(row.get("risk", "")),
            }
        return result, True
    except Exception:
        log.exception(
            "[notice_llm] LLM scoring failed (base=%s model=%s)", base_url, model
        )
        return {}, False


def _extract_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str) and payload.get("output_text"):
        return str(payload["output_text"])

    output = payload.get("output", [])
    chunks: list[str] = []
    for item in output:
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(str(text))
    return "".join(chunks).strip()
