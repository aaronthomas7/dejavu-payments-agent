import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.config import Settings
from app.llm import GroqJSONClient, LLMError, RateLimiter, extract_json

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


def _resp(content, tokens=50):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                           usage=SimpleNamespace(total_tokens=tokens))


def _bad_request(msg="json_validate_failed"):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return openai.BadRequestError(msg, response=httpx.Response(400, request=req), body=None)


def _rate_limited():
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return openai.RateLimitError("slow down", response=httpx.Response(429, request=req, headers={"retry-after": "0.01"}),
                                 body=None)


def _client(responses):
    s = Settings()
    s.groq_api_key = "test"
    c = GroqJSONClient(s)
    calls = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    c.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    return c, calls


def test_extract_json_handles_fences_and_chatter():
    assert extract_json('Sure!\n```json\n{"ok": true}\n```') == {"ok": True}
    assert extract_json('Here you go: {"ok": false} hope that helps') == {"ok": False}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_schema_failure_falls_back_to_json_object_mode():
    c, calls = _client([_bad_request(), _resp('{"ok": true}')])
    data, model = asyncio.run(c.complete_json("sys", "user", SCHEMA, "ping"))
    assert data == {"ok": True}
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"


def test_rate_limit_is_retried():
    c, calls = _client([_rate_limited(), _resp('{"ok": true}')])
    data, _ = asyncio.run(c.complete_json("sys", "user", SCHEMA, "ping"))
    assert data == {"ok": True} and len(calls) == 2


def test_fallback_model_is_used_when_primary_keeps_failing():
    c, calls = _client([_bad_request(), _bad_request(), _resp('{"ok": true}')])
    data, model = asyncio.run(c.complete_json("sys", "user", SCHEMA, "ping"))
    assert model == c.settings.llm_fallback_model
    assert calls[2]["model"] == c.settings.llm_fallback_model


def test_everything_failing_raises_llm_error():
    c, _ = _client([_bad_request()] * 4)
    with pytest.raises(LLMError):
        asyncio.run(c.complete_json("sys", "user", SCHEMA, "ping"))


def test_rate_limiter_admits_within_budget():
    async def run():
        rl = RateLimiter(tpm=5000, rpm=10)
        t1 = await rl.acquire(1000)
        t2 = await rl.acquire(1000)
        await rl.settle(t1, 200)
        return t1, t2, sum(e[1] for e in rl._events)
    t1, t2, used = asyncio.run(run())
    assert t1 != t2 and used == 1200
