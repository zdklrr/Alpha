"""notice_llm 的 OpenAI 兼容双协议测试（Chat Completions 优先，Responses 回退）。"""

from __future__ import annotations

import asyncio
import json

import pytest


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(payload or {}, ensure_ascii=False)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    """记录调用序列，按预设返回响应。"""

    def __init__(self, responses: list[_FakeResponse], *args, **kwargs):
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append((url, json or {}))
        if self._responses:
            return self._responses.pop(0)
        raise AssertionError("unexpected extra request")


def _install(monkeypatch, responses):
    import app.services.notice_llm as nl

    client = _FakeAsyncClient(responses)
    monkeypatch.setattr(nl.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("HERMES_MODEL", "test-model")
    return client


def test_chat_completions_is_preferred(monkeypatch):
    import app.services.notice_llm as nl

    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        [{"code": "600000", "score": 88, "reason": "good", "risk": "none"}]
                    )
                }
            }
        ]
    }
    client = _install(monkeypatch, [_FakeResponse(200, payload)])

    scores, enabled = asyncio.run(
        nl.score_with_llm([{"code": "600000", "title": "业绩预增"}])
    )

    assert enabled is True
    assert scores["600000"]["score"] == 88
    assert client.calls[0][0].endswith("/chat/completions")
    assert "messages" in client.calls[0][1]
    assert "input" not in client.calls[0][1]


def test_falls_back_to_responses_api(monkeypatch):
    import app.services.notice_llm as nl

    payload = {
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "[]"}]}
        ]
    }
    client = _install(monkeypatch, [_FakeResponse(404, text="not found"), _FakeResponse(200, payload)])

    scores, enabled = asyncio.run(nl.score_with_llm([{"code": "600000", "title": "t"}]))

    assert enabled is True
    assert scores == {}
    assert client.calls[0][0].endswith("/chat/completions")
    assert client.calls[1][0].endswith("/responses")


def test_json_fence_is_tolerated(monkeypatch):
    import app.services.notice_llm as nl

    fenced = "```json\n[{\"code\": \"000001\", \"score\": 70, \"reason\": \"ok\", \"risk\": \"low\"}]\n```"
    payload = {"choices": [{"message": {"content": fenced}}]}
    _install(monkeypatch, [_FakeResponse(200, payload)])

    scores, enabled = asyncio.run(nl.score_with_llm([{"code": "000001", "title": "t"}]))

    assert enabled is True
    assert scores["000001"]["score"] == 70


def test_http_error_disables_llm(monkeypatch):
    import app.services.notice_llm as nl

    _install(monkeypatch, [_FakeResponse(401, text="unauthorized")])

    scores, enabled = asyncio.run(nl.score_with_llm([{"code": "600000", "title": "t"}]))

    assert scores == {}
    assert enabled is False
