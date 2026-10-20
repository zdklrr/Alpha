"""LLM 端点连通性自检。

用途：验证 .env 里配置的 OPENAI_BASE_URL / OPENAI_API_KEY / HERMES_MODEL
是否真的能对话（任意 OpenAI 兼容服务都支持，不限于 Ollama）。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\test_llm.py
    .\\.venv\\Scripts\\python.exe scripts\\test_llm.py --base-url http://127.0.0.1:1234/v1 --model my-model
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    print("[test_llm] httpx not installed: pip install httpx")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def mask(key: str) -> str:
    if not key:
        return "(empty)"
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"


async def probe(base_url: str, api_key: str, model: str, timeout: float) -> tuple[bool, str]:
    base = base_url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    chat_payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复 OK 两个字"}],
        "temperature": 0,
    }
    async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
        resp = await client.post(f"{base}/chat/completions", headers=headers, json=chat_payload)
        if resp.status_code in (404, 405, 501):
            responses_payload = {
                "model": model,
                "input": [{"role": "user", "content": "只回复 OK 两个字"}],
            }
            resp = await client.post(f"{base}/responses", headers=headers, json=responses_payload)
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:400]}"
        data = resp.json()
    choices = data.get("choices")
    if choices:
        content = (choices[0].get("message") or {}).get("content", "")
        return True, str(content).strip()[:200]
    text = data.get("output_text")
    if isinstance(text, str) and text:
        return True, text.strip()[:200]
    return True, json.dumps(data, ensure_ascii=False)[:200]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()

    env = load_env_file()
    base_url = args.base_url or env.get("OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL", "")
    api_key = args.api_key or env.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    model = args.model or env.get("HERMES_MODEL") or os.getenv("HERMES_MODEL", "")

    print(f"env file    : {ENV_FILE} ({'found' if ENV_FILE.exists() else 'missing'})")
    print(f"base url    : {base_url or '(empty)'}")
    print(f"model       : {model or '(empty)'}")
    print(f"api key     : {mask(api_key)}")

    if not base_url or not model:
        print("\n[test_llm] FAIL: OPENAI_BASE_URL / HERMES_MODEL 未配置")
        print("[test_llm] 先运行 .\\set-llm.ps1 -Provider <name> -ApiKey <key>")
        return 1

    try:
        ok, detail = asyncio.run(probe(base_url, api_key, model, args.timeout))
    except Exception as exc:  # 网络异常等
        print(f"\n[test_llm] FAIL: {type(exc).__name__}: {exc}")
        print("[test_llm] 若为连接超时，检查代理（HTTP(S)_PROXY）或本机模型服务是否已启动")
        return 1

    if ok:
        print(f"\n[test_llm] OK: 服务端回复 -> {detail}")
        print("[test_llm] 现在可以 .\\restart.ps1 让 Alpha 读取新配置")
        return 0
    print(f"\n[test_llm] FAIL: {detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
