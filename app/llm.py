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

    def load(self) -> int:
        """Tokens used in the current one-minute window (for picking the least-busy key)."""
        self._prune(time.monotonic())
        return sum(e[1] for e in self._events)

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


def parse_keys(raw: str | None) -> list[str]:
    """GROQ_API_KEY may hold several keys separated by commas (e.g. one per teammate)."""
    return [k.strip() for k in (raw or "").replace(";", ",").split(",") if k.strip()]


class _NoKeyLeft(LLMError):
    """Every key has used up its daily quota for this model (or was rejected)."""


class GroqJSONClient:
    """Groq chat client with one rate limiter per API key.

    Free-tier keys have a daily token quota per model. When a key runs out for a model,
    it is skipped for the rest of the process: the next key takes over, and when no key
    is left the fallback model is used. Calls go to the least-busy key, so two keys also
    make long runs (the replay) about twice as fast.
    """

    def __init__(self, settings: Settings, api_keys: list[str] | None = None):
        self.settings = settings
        keys = (api_keys if api_keys is not None else parse_keys(settings.groq_api_key)) or ["missing"]
        self.clients = [AsyncOpenAI(api_key=k, base_url=settings.llm_base_url, timeout=45.0, max_retries=0)
                        for k in keys]
        self.limiters = [RateLimiter(settings.llm_tpm_limit, settings.llm_rpm_limit) for _ in keys]
        self.exhausted: set[tuple[int, str]] = set()  # (key index, model) pairs out of daily quota
        self.dead: set[int] = set()  # keys the API rejected (401/403)
        self._reasoning_ok = True

    # kept for callers/tests that use a single client
    @property
    def client(self):
        return self.clients[0]

    @client.setter
    def client(self, value) -> None:
        self.clients[0] = value

    @property
    def limiter(self) -> RateLimiter:
        return self.limiters[0]

    def _pick_key(self, model: str) -> int | None:
        usable = [i for i in range(len(self.clients)) if i not in self.dead and (i, model) not in self.exhausted]
        if not usable:
            return None
        return min(usable, key=lambda i: self.limiters[i].load())

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
            hint = None
            # attempt 1: strict schema; attempt 2: json_object + repair hint
            for mode in ("json_schema", "json_object"):
                try:
                    data = await self._call(model, system, user, schema, schema_name, mode, max_completion_tokens,
                                            repair_hint=hint)
                    return data, model
                except _NoKeyLeft as exc:
                    errors.append(f"{model}: {exc}")
                    break
                except LLMError as exc:
                    errors.append(f"{model}/{mode}: {exc}")
                    hint = str(exc)
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
        attempts = 0
        while attempts < 4:
            idx = self._pick_key(model)
            if idx is None:
                raise _NoKeyLeft(f"daily limit reached on every Groq key ({len(self.clients)})")
            limiter = self.limiters[idx]
            ticket = await limiter.acquire(est)
            try:
                kwargs: dict[str, Any] = dict(model=model, messages=messages, temperature=0.1,
                                              max_completion_tokens=max_completion_tokens,
                                              response_format=response_format)
                if "gpt-oss" in model and self.settings.llm_reasoning_effort and self._reasoning_ok:
                    kwargs["reasoning_effort"] = self.settings.llm_reasoning_effort
                resp = await self.clients[idx].chat.completions.create(**kwargs)
                usage = getattr(resp, "usage", None)
                await limiter.settle(ticket, getattr(usage, "total_tokens", None))
                content = (resp.choices[0].message.content or "").strip()
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    return extract_json(content)
            except openai.RateLimitError as exc:
                wait = _retry_after_seconds(exc)
                if _is_daily_limit(exc, wait):
                    self.exhausted.add((idx, model))
                    others = self._pick_key(model) is not None
                    print(f"  Groq key {idx + 1} used up its free daily quota for {model}"
                          + ("; switching to the next key." if others else
                             "; no key left for this model, using the fallback model."), flush=True)
                    continue  # not counted as an attempt: the next key (or model) takes over at once
                wait = wait or backoff
                log.warning("Groq 429 on %s (key %d), sleeping %.1fs", model, idx + 1, wait)
                await asyncio.sleep(min(wait, 60))
                backoff *= 2
            except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
                if len(self.clients) > 1:
                    self.dead.add(idx)
                    print(f"  Groq key {idx + 1} was rejected ({_short(exc)[:80]}); using the other key(s).", flush=True)
                    continue
                raise LLMError(f"key rejected: {_short(exc)}") from exc
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
                    attempts += 1
                    continue
                raise LLMError(f"status {exc.status_code}: {_short(exc)}") from exc
            except (ValueError, json.JSONDecodeError) as exc:
                raise LLMError(f"unparseable JSON: {exc}") from exc
            attempts += 1
        raise LLMError("gave up after retries (rate limit / network)")


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def _retry_after_seconds(exc: Exception) -> float | None:
    """Seconds to wait, from the retry-after header or Groq's "Please try again in 7m12.5s" message."""
    try:
        value = exc.response.headers.get("retry-after")  # type: ignore[attr-defined]
        if value:
            return float(value)
    except Exception:
        pass
    match = re.search(r"try again in\s+([0-9hms.]+)", str(exc), re.IGNORECASE)
    if not match:
        return None
    units = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    total = sum(float(n) * units[u] for n, u in _DURATION.findall(match.group(1)))
    return total or None


def _is_daily_limit(exc: Exception, wait: float | None) -> bool:
    text = str(exc).lower()
    return "per day" in text or "(tpd)" in text or "(rpd)" in text or (wait is not None and wait > 120)


def _short(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) < 300 else text[:300] + "..."
