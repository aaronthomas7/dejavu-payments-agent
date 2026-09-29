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


def _daily_limited(model="openai/gpt-oss-120b"):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    msg = (f"Rate limit reached for model `{model}` in organization `org_x` service tier `on_demand` on tokens per day "
           "(TPD): Limit 200000, Used 199100, Requested 1900. Please try again in 7m12.5s.")
    return openai.RateLimitError(msg, response=httpx.Response(429, request=req), body=None)


def _multi_key_client(per_key):
    """per_key: one list of responses per API key."""
    s = Settings()
    s.groq_api_key = ",".join(f"key{i}" for i in range(len(per_key)))
    c = GroqJSONClient(s)
    calls = []
    for i, responses in enumerate(per_key):
        async def fake_create(_i=i, _responses=responses, **kwargs):
            calls.append((_i, kwargs["model"]))
            item = _responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        c.clients[i] = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    return c, calls


def test_daily_limit_switches_to_the_next_key_and_remembers_it():
    c, calls = _multi_key_client([[_daily_limited()], [_resp('{"ok": true}'), _resp('{"ok": true}')]])
    data, model = asyncio.run(c.complete_json("sys", "user", SCHEMA, "ping"))
    assert data == {"ok": True} and model == c.settings.llm_model
    asyncio.run(c.complete_json("sys", "user", SCHEMA, "ping"))
    assert calls == [(0, model), (1, model), (1, model)]  # key 0 is never retried for this model


def test_daily_limit_on_every_key_goes_straight_to_the_fallback_model():
    c, calls = _client([_daily_limited(), _resp('{"ok": true}'), _resp('{"ok": true}')])
    data, model = asyncio.run(c.complete_json("sys", "user", SCHEMA, "ping"))
    assert model == c.settings.llm_fallback_model and len(calls) == 2
    _, model2 = asyncio.run(c.complete_json("sys", "user", SCHEMA, "ping"))
    assert model2 == c.settings.llm_fallback_model and len(calls) == 3  # no wasted call on the exhausted model


def test_retry_after_is_read_from_the_message():
    from app.llm import _retry_after_seconds, parse_keys
    assert abs(_retry_after_seconds(_daily_limited()) - 432.5) < 0.01
    assert parse_keys(" a, b ,,c ") == ["a", "b", "c"]
