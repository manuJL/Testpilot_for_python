"""One seam for all LLM access: chat(step, messages) -> str.

The rest of the codebase never sees a provider name, an API URL, or an SDK —
swapping Groq for OpenRouter/Gemini happens right here.

There is deliberately no scripted/fake backend in this module: TestPilot only
ever talks to a real provider, and refuses to start without an API key.
"""

from __future__ import annotations

import json
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
        self._client = client or httpx.Client(timeout=60.0)

    def chat(self, step: str, messages: list[Message]) -> str:
        model = self.config.model_for(step)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": 4096,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/testpilot/testpilot"
            headers["X-Title"] = "TestPilot"

        data: dict | None = None
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
                break
            except httpx.HTTPStatusError as exc:
                # Transient throttling/server errors are worth retrying; a
                # genuine client error (401, 404 bad model) never will be.
                if (
                    exc.response.status_code in _RETRYABLE_STATUS
                    and attempt < _MAX_ATTEMPTS - 1
                ):
                    time.sleep(_retry_delay(exc.response, attempt))
                    continue
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

        if data is None:  # unreachable: the last attempt breaks or raises
            raise LLMError(f"{self.provider} returned no response")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response shape: {json.dumps(data)[:500]}") from exc

        if not content:
            raise LLMError(f"{self.provider} returned empty content: {json.dumps(data)[:500]}")

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
            raise LLMError(
                f"{self.provider} returned non-text content: {json.dumps(data)[:500]}"
            )
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
