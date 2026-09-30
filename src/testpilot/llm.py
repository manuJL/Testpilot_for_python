"""One seam for all LLM access: chat(step, messages) -> str.

The rest of the codebase never sees a provider name, an API URL, or an SDK —
swapping Groq for OpenRouter/Gemini happens right here.

There is deliberately no scripted/fake backend in this module: TestPilot only
ever talks to a real provider, and refuses to start without an API key.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Protocol

import httpx

from .config import PROVIDER_BASE_URLS, Config

Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": str}

# Free tiers throttle hard (Groq's default is 8000 TPM — easy to hit when
# batching a folder), so transient failures are retried before giving up.
_MAX_ATTEMPTS = 4
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})
# Two different ceilings on purpose: a *fallback* we invented must stay short,
# but a *hint the provider asked for* is the one thing that actually knows when
# the window resets. Capping that at 8s is what made retries fire early and
# fail twice in a row against a `retry-after: 15`.
_MAX_BACKOFF = 8.0
_MAX_HINT_WAIT = 45.0
# Groq sometimes says it in the body only: "Please try again in 14.025s."
_BODY_HINT = re.compile(r"try again in ([\d.]+)s")

# Reasoning models (gpt-oss, Nemotron) spend part of `max_tokens` on thinking,
# so a small budget can truncate a generated file mid-fence. One doubling
# retry, then an error naming the env var — beats handing the patcher garbage.
_MAX_TOKENS = 8192
_MAX_TOKENS_CAP = 32768
# 60s was too short for a big reasoning model at a high token budget, and the
# timeout was retried four times — each attempt failing the same way.
_HTTP_TIMEOUT = float(os.getenv("TESTPILOT_HTTP_TIMEOUT", "120"))
# "Waiting will not help" — a spent daily/monthly quota.
_EXHAUSTED_HINT = re.compile(r"daily|per day|monthly|quota exceeded|quota limit", re.IGNORECASE)
# "This one probably will."
_TRANSIENT_HINT = re.compile(
    r"rate limit|too many requests|temporarily|try again|overloaded|server error", re.IGNORECASE
)


def _hit_token_limit(data: dict) -> bool:
    """Did the provider stop because it ran out of output tokens?"""
    try:
        return data["choices"][0].get("finish_reason") == "length"
    except (KeyError, IndexError, TypeError, AttributeError):
        return False


def _balanced_fences(content: str) -> bool:
    """True when no code fence is left open (i.e. the reply wasn't cut off)."""
    return content.count("```") % 2 == 0


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Seconds to wait: trust the provider's hint, else exponential backoff."""
    if response is not None:
        for header, divisor in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
            raw = response.headers.get(header)
            if not raw:
                continue
            try:
                return min(float(raw) / divisor, _MAX_HINT_WAIT)
            except ValueError:
                continue
        hint = _BODY_HINT.search(response.text or "")
        if hint:
            return min(float(hint.group(1)), _MAX_HINT_WAIT)
    return min(2.0**attempt, _MAX_BACKOFF)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def chat(self, step: str, messages: list[Message]) -> str: ...


class HTTPChatLLM:
    """OpenAI-compatible chat.completions client with provider fallback."""

    def __init__(self, config: Config | None = None, client: httpx.Client | None = None):
        self.config = config or Config()
        self.provider, self.api_key = self.config.resolve_provider()
        self.base_url = PROVIDER_BASE_URLS[self.provider]
        self._client = client or httpx.Client(timeout=_HTTP_TIMEOUT)

    def chat(self, step: str, messages: list[Message]) -> str:
        model = self.config.model_for(step)
        max_tokens = max(1024, int(getattr(self.config, "max_tokens", _MAX_TOKENS)))

        data = self._post(model, messages, max_tokens)
        content = self._content(data)

        # A reasoning model that ran the budget out emits an unterminated code
        # fence; extract_code() then falls back to the raw text and the patch
        # step reports "invalid Python, kept previous" — an iteration burnt and
        # no visible cause. Re-ask with a doubled budget before giving up.
        if _hit_token_limit(data) and not _balanced_fences(content):
            max_tokens = min(max_tokens * 2, _MAX_TOKENS_CAP)
            data = self._post(model, messages, max_tokens)
            content = self._content(data)
            if _hit_token_limit(data) and not _balanced_fences(content):
                raise LLMError(
                    f"{model} hit max_tokens={max_tokens} and the reply is still "
                    "truncated. Raise TESTPILOT_MAX_TOKENS (or "
                    "TESTPILOT_MAX_TOKENS=... in .env), or pick a model with a "
                    "larger output limit."
                )
        return content

    def _post(self, model: str, messages: list[Message], max_tokens: int) -> dict:
        """POST /chat/completions, retrying transient failures. Returns the body."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/testpilot/testpilot"
            headers["X-Title"] = "TestPilot"

        data: dict | None = None
        exhausted_body = ""
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                # Decoding belongs inside this guard: raise_for_status() only
                # sees 4xx/5xx, but a 200 carrying an HTML page (captive portal,
                # proxy) raises json.JSONDecodeError and a truncated gzip stream
                # raises httpx.DecodingError. Either would otherwise escape
                # LLMError and abort the whole run with no report.
                data = response.json()
                # OpenRouter answers HTTP 200 with an {"error": ...} body for
                # some failures; without this check it surfaces as a useless
                # "Unexpected response shape" instead of the real reason.
                embedded = data.get("error") if isinstance(data, dict) else None
                if embedded:
                    if attempt < _MAX_ATTEMPTS - 1 and _TRANSIENT_HINT.search(
                        json.dumps(embedded)
                    ):
                        time.sleep(_retry_delay(response, attempt))
                        continue
                    raise LLMError(
                        f"{self.provider} returned an error: {json.dumps(embedded)[:500]}"
                    )
                break
            except httpx.HTTPStatusError as exc:
                # Transient throttling/server errors are worth retrying; a
                # genuine client error (401, 404 bad model) never will be.
                if exc.response.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
                    body = exc.response.text or ""
                    delay = _retry_delay(exc.response, attempt)
                    # An explicit "try again in 9m50s" is a *schedule*, not a
                    # dead end — honour it when it fits inside our window, even
                    # though the same body also says "per day". Only give up
                    # when there is no schedule at all, or when the wait the
                    # provider wants is longer than this CLI will block for.
                    has_schedule = _BODY_HINT.search(body) is not None or bool(
                        exc.response.headers.get("retry-after")
                        or exc.response.headers.get("retry-after-ms")
                    )
                    if _EXHAUSTED_HINT.search(body) and (
                        not has_schedule or delay >= _MAX_HINT_WAIT
                    ):
                        exhausted_body = body.strip()[:400]
                        break
                    time.sleep(delay)
                    continue
                raise LLMError(self._friendly_http_error(model, exc)) from exc
                raise LLMError(self._friendly_http_error(model, exc)) from exc
            except httpx.HTTPError as exc:
                # Connection reset / timeout: worth another attempt.
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(_retry_delay(None, attempt))
                    continue
                raise LLMError(f"{self.provider} request failed: {exc}") from exc
            except ValueError as exc:  # json.JSONDecodeError subclasses ValueError
                raise LLMError(
                    f"{self.provider} returned a non-JSON body: {exc}"
                ) from exc

        if data is None:  # the loop broke out early (a limit that outlasts us)
            raise LLMError(
                f"{self.provider} refused the request: its limit will not clear "
                "inside this run's retry window, so it stopped instead of "
                f"sleeping. It said: {exhausted_body}"
            )

        return data

    def _content(self, data: dict) -> str:
        """Assistant text, with the provider named in any error message."""
        try:
            return self._extract_text(data)
        except LLMError as exc:
            raise LLMError(f"{self.provider} returned {exc}") from exc

    @staticmethod
    def _extract_text(data: dict) -> str:
        """Pull the assistant text out of a chat.completions body."""
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {json.dumps(data)[:500]}") from exc

        if not content:
            raise LLMError(f"empty content: {json.dumps(data)[:500]}")

        if not isinstance(content, str):
            # Some providers send the multimodal shape: [{"type":"text",...}].
            # Handing a list to extract_code() would die with TypeError inside
            # re.findall — far from the LLMError contract every node assumes.
            if isinstance(content, list):
                text = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict)
                ).strip()
                if text:
                    return text
            raise LLMError(f"non-text content: {json.dumps(data)[:500]}")
        return content

    @staticmethod
    def _friendly_http_error(model: str, exc: httpx.HTTPStatusError) -> str:
        """Turn a raw provider error into something actionable.

        The API root is derived from the failed request URL (minus the endpoint
        path) so the model-list hint is correct for every provider — Groq and
        Gemini use /openai/v1, OpenRouter uses /api/v1.
        """
        status = exc.response.status_code
        body = exc.response.text[:500]
        api_root = str(exc.request.url).removesuffix("/chat/completions")
        host = exc.request.url.host

        if "model_not_found" in body or "does not exist" in body:
            return (
                f"Model `{model}` is not available on {host} "
                f"(HTTP {status}). It may be retired or your key may lack access.\n"
                "  • List models your key can use, then pick one:\n"
                f"      curl -s {api_root}/models -H \"Authorization: Bearer $API_KEY\"\n"
                "  • Set it in .env:  TESTPILOT_GENERATE_MODEL=<model-id>\n"
                f"  • Full response: {body}"
            )
        if status in (401, 403):
            return (
                f"Authentication failed (HTTP {status}) — your API key is invalid or "
                f"revoked. Check it in .env\n  Full response: {body}"
            )
        if status == 429:
            return (
                f"Rate limited (HTTP 429) by {host} — wait a few seconds "
                f"and re-run, or switch provider with -p.\n  Full response: {body}"
            )
        return f"{host} returned HTTP {status}: {body}"


def make_llm(config: Config | None = None) -> LLM:
    """Build the real HTTP client. Raises if no API key is configured."""
    return HTTPChatLLM(config)
