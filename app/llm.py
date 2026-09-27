"""A small, defensive client for Groq's OpenAI-compatible chat API.

Why this file is bigger than "call the model": free-tier Groq limits are tight
(8K tokens/min on gpt-oss-120b) and models occasionally return JSON that does
not match the schema. The agent must never crash the analyst's screen because
of that, so every call goes through:

    rate limiter -> strict JSON schema -> validation -> repair retry
                 -> fallback model -> caller gets a clear error it can degrade on
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from typing import Any

import openai
from openai import AsyncOpenAI

from .config import Settings

log = logging.getLogger("dejavu.llm")


class LLMError(RuntimeError):
    """Raised when no model could produce a valid answer."""


class RateLimiter:
    """Sliding-window limiter for requests/min and tokens/min (shared by all calls)."""

    def __init__(self, tpm: int, rpm: int):
        self.tpm = max(1000, tpm)
        self.rpm = max(1, rpm)
        self._events: deque[list] = deque()  # [timestamp, tokens, ticket]
        self._lock = asyncio.Lock()
        self._next_ticket = 0

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] > 60:
            self._events.popleft()

    async def acquire(self, est_tokens: int) -> int:
        """Block until the request fits in the window. Returns a ticket for settle()."""
        est_tokens = min(est_tokens, self.tpm)
        while True:
            async with self._lock:
                now = time.monotonic()
                self._prune(now)
                used = sum(e[1] for e in self._events)
                if len(self._events) < self.rpm and used + est_tokens <= self.tpm:
                    self._next_ticket += 1
                    self._events.append([now, est_tokens, self._next_ticket])
                    return self._next_ticket
                wait = 60 - (now - self._events[0][0]) + 0.25 if self._events else 1.0
            log.info("rate limiter: waiting %.1fs (tokens used in window=%s)", wait, used)
            await asyncio.sleep(min(max(wait, 0.5), 30))

    async def settle(self, ticket: int, actual_tokens: int | None) -> None:
        """Replace a request's estimated token count with the real usage reported by the API."""
        if not actual_tokens:
            return
        async with self._lock:
            for event in self._events:
                if event[2] == ticket:
                    event[1] = actual_tokens
                    return


def estimate_tokens(*texts: str) -> int:
    return sum(len(t) for t in texts) // 4 + 16


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply (handles ```json fences and chatter)."""
    if not text:
        raise ValueError("empty model output")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model output")
        candidate = text[start : end + 1]
    return json.loads(candidate)


class GroqJSONClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.groq_api_key or "missing", base_url=settings.llm_base_url,
                                  timeout=45.0, max_retries=0)
        self.limiter = RateLimiter(settings.llm_tpm_limit, settings.llm_rpm_limit)
        self._reasoning_ok = True

    async def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_completion_tokens: int = 900,
    ) -> tuple[dict[str, Any], str]:
        """Return (parsed_json, model_used). Raises LLMError if every attempt fails."""
        models = [self.settings.llm_model]
        if self.settings.llm_fallback_model and self.settings.llm_fallback_model not in models:
            models.append(self.settings.llm_fallback_model)

        errors: list[str] = []
        for model in models:
            # attempt 1: strict schema; attempt 2: json_object + repair hint
            for mode in ("json_schema", "json_object"):
                try:
                    data = await self._call(model, system, user, schema, schema_name, mode, max_completion_tokens,
                                            repair_hint=errors[-1] if errors else None)
                    return data, model
                except LLMError as exc:
                    errors.append(f"{model}/{mode}: {exc}")
                    log.warning("LLM attempt failed: %s", errors[-1])
        raise LLMError("; ".join(errors[-3:]))

    async def _call(self, model, system, user, schema, schema_name, mode, max_completion_tokens, repair_hint=None):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if repair_hint:
            messages.append({"role": "user", "content": (
                "Your previous answer could not be used (" + repair_hint[:300] + "). "
                "Reply again with ONE valid JSON object that follows the schema exactly. No prose.")})
        if mode == "json_schema":
            response_format = {"type": "json_schema",
                               "json_schema": {"name": schema_name, "strict": True, "schema": schema}}
        else:
            messages[0]["content"] = system + "\n\nReturn ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema)
            response_format = {"type": "json_object"}

        est = estimate_tokens(*(m["content"] for m in messages)) + max_completion_tokens
        backoff = 2.0
        for attempt in range(4):
            ticket = await self.limiter.acquire(est)
            try:
                kwargs: dict[str, Any] = dict(model=model, messages=messages, temperature=0.1,
                                              max_completion_tokens=max_completion_tokens,
                                              response_format=response_format)
                if "gpt-oss" in model and self.settings.llm_reasoning_effort and self._reasoning_ok:
                    kwargs["reasoning_effort"] = self.settings.llm_reasoning_effort
                resp = await self.client.chat.completions.create(**kwargs)
                usage = getattr(resp, "usage", None)
                await self.limiter.settle(ticket, getattr(usage, "total_tokens", None))
                content = (resp.choices[0].message.content or "").strip()
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    return extract_json(content)
            except openai.RateLimitError as exc:
                retry_after = _retry_after_seconds(exc) or backoff
                log.warning("Groq 429 on %s, sleeping %.1fs", model, retry_after)
                await asyncio.sleep(min(retry_after, 60))
                backoff *= 2
            except (openai.APITimeoutError, openai.APIConnectionError) as exc:
                log.warning("Groq network issue (%s), retrying", type(exc).__name__)
                await asyncio.sleep(backoff)
                backoff *= 2
            except openai.BadRequestError as exc:
                if "reasoning" in str(exc).lower() and self._reasoning_ok:
                    self._reasoning_ok = False  # this endpoint/model does not take reasoning_effort; retry without
                    continue
                # e.g. json_validate_failed / tool_use_failed / unsupported response_format
                raise LLMError(f"bad request: {_short(exc)}") from exc
            except openai.APIStatusError as exc:
                if exc.status_code >= 500:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise LLMError(f"status {exc.status_code}: {_short(exc)}") from exc
            except (ValueError, json.JSONDecodeError) as exc:
                raise LLMError(f"unparseable JSON: {exc}") from exc
        raise LLMError("gave up after retries (rate limit / network)")


def _retry_after_seconds(exc: Exception) -> float | None:
    try:
        headers = exc.response.headers  # type: ignore[attr-defined]
        value = headers.get("retry-after")
        return float(value) if value else None
    except Exception:
        return None


def _short(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) < 300 else text[:300] + "..."
