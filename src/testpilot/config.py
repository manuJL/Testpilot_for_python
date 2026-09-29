"""Configuration: provider + model IDs + run limits.

Everything is overridable through environment variables / .env so the
hackathon demo can switch providers without a code change.

Updated for current free / recommended models (Sept 2026).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Ordered fallbacks: first provider with an API key wins.
PROVIDER_ORDER = ("groq", "openrouter", "gemini")

PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}

PROVIDER_ENV_KEYS = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# ---------------------------------------------------------------------------
# Current recommended / free models (September 2026)
# ---------------------------------------------------------------------------

# Every ID below was verified against the provider's live /models endpoint.
# (Groq retired meta-llama/llama-3.3-70b-versatile on 2026-08-16 — it 404s now.)

# Groq – very fast, free tier with rate limits
GROQ_MODELS = {
    "fast": "openai/gpt-oss-20b",          # ~1000 t/s, good for generate
    "deep": "openai/gpt-oss-120b",         # stronger reasoning
    "qwen": "qwen/qwen3.8-27b",            # solid alternative
}

# OpenRouter – free models must end with :free (16 available, verified)
OPENROUTER_MODELS = {
    "fast": "cohere/north-mini-code:free",                 # coding focused
    "deep": "nvidia/nemotron-3-super-120b-a12b:free",      # strong reasoning
    "qwen": "qwen/qwen3.8-27b:free",
    "gemma": "google/gemma-4-31b-it:free",
    "ultra": "nvidia/nemotron-3-ultra-550b-a55b:free",     # biggest free model
}

# Gemini (Google AI Studio) – free tier available on Flash models
GEMINI_MODELS = {
    "fast": "gemini-2.5-flash",
    "deep": "gemini-2.5-pro",              # stronger when free tier allows
    "flash": "gemini-2.0-flash",           # reliable older flash
    "lite": "gemini-2.5-flash-lite",
}

# Default models used when the user does not override via env
DEFAULT_GENERATE_MODEL = {
    "groq": GROQ_MODELS["fast"],
    "openrouter": OPENROUTER_MODELS["fast"],
    "gemini": GEMINI_MODELS["fast"],
}

DEFAULT_DIAGNOSE_MODEL = {
    "groq": GROQ_MODELS["deep"],
    "openrouter": OPENROUTER_MODELS["deep"],
    "gemini": GEMINI_MODELS["deep"],
}

# Friendly, user-facing message shown whenever no API key is configured.
MISSING_KEY_MESSAGE = (
    "API key is not found. Please open .env and add the API key to continue.\n"
    "  • No .env yet?  cp .env.example .env\n"
    "  • Groq (free + fast):     https://console.groq.com/keys  →  GROQ_API_KEY=\n"
    "  • OpenRouter (free models): https://openrouter.ai/keys  →  OPENROUTER_API_KEY=\n"
    "  • Gemini (free tier):     https://aistudio.google.com/apikey  →  GEMINI_API_KEY=\n"
    "  Adding one key is enough. Then re-run the same command."
)


class MissingAPIKeyError(RuntimeError):
    """Raised when TestPilot is run without any provider API key configured."""


@dataclass
class Config:
    provider: str = ""
    # These can still be overridden by env vars for maximum flexibility
    generate_model: str = field(
        default_factory=lambda: os.getenv("TESTPILOT_GENERATE_MODEL", "")
    )
    diagnose_model: str = field(
        default_factory=lambda: os.getenv("TESTPILOT_DIAGNOSE_MODEL", "")
    )
    max_iterations: int = field(
        default_factory=lambda: int(os.getenv("TESTPILOT_MAX_ITER", "4"))
    )
    sandbox_timeout: int = field(
        default_factory=lambda: int(os.getenv("TESTPILOT_TIMEOUT", "30"))
    )
    temperature: float = field(
        default_factory=lambda: float(os.getenv("TESTPILOT_TEMPERATURE", "0.2"))
    )
    # Set once a provider warning has been emitted, so the preflight (cli.py)
    # and the client (llm.py) don't both print the same message.
    _warned: bool = field(default=False, init=False, repr=False, compare=False)

    def resolve_provider(self, *, quiet: bool = False) -> tuple[str, str]:
        """Pick a provider (env preference first, else first with a key).

        Returns (provider, api_key).
        Raises MissingAPIKeyError when no key is configured.

        `quiet=True` suppresses the stderr warnings. `model_for()` calls this
        once per LLM request, so without it a misconfigured provider would print
        the same warning a dozen times in a single run — the warning belongs to
        the preflight, not to every request.
        """
        preferred = (self.provider or os.getenv("TESTPILOT_PROVIDER", "")).strip().lower()

        if preferred and preferred not in PROVIDER_BASE_URLS:
            if not quiet and not self._warned:
                self._warned = True
                print(
                    f"Warning: unknown provider '{preferred}' "
                    f"(expected one of: {', '.join(PROVIDER_ORDER)}); auto-detecting.",
                    file=sys.stderr,
                )
            candidates = list(PROVIDER_ORDER)
        else:
            candidates = [preferred] if preferred else list(PROVIDER_ORDER)

        for provider in candidates:
            key = os.getenv(PROVIDER_ENV_KEYS[provider], "").strip()
            if key:
                return provider, key

        # A provider was explicitly requested but has no key — say so loudly
        # rather than silently handing the user a different provider.
        # `preferred` is known-good here: unknown names were replaced by
        # PROVIDER_ORDER above, so PROVIDER_ENV_KEYS[preferred] cannot KeyError.
        if preferred and preferred in PROVIDER_ENV_KEYS and not quiet and not self._warned:
            self._warned = True
            print(
                f"Warning: requested provider '{preferred}' has no API key set "
                f"({PROVIDER_ENV_KEYS[preferred]}); falling back to auto-detected "
                f"provider order.",
                file=sys.stderr,
            )

        # Last resort: any provider key present at all.
        for provider in PROVIDER_ORDER:
            key = os.getenv(PROVIDER_ENV_KEYS[provider], "").strip()
            if key:
                return provider, key

        raise MissingAPIKeyError(MISSING_KEY_MESSAGE)

    def model_for(self, step: str) -> str:
        """Return the model ID for a given step (generate | diagnose).

        Priority:
        1. Explicit env var / instance attribute
        2. Provider-specific default from the tables above
        """
        # quiet: this runs once per LLM request; the warning belongs to the
        # preflight in cli.py, not to every generate/diagnose/patch call.
        provider, _ = self.resolve_provider(quiet=True)

        # Explicit override wins (strip so a whitespace-only value is ignored)
        if step == "diagnose" and self.diagnose_model.strip():
            return self.diagnose_model.strip()
        if step != "diagnose" and self.generate_model.strip():
            return self.generate_model.strip()

        # Fall back to the curated defaults
        if step == "diagnose":
            return DEFAULT_DIAGNOSE_MODEL.get(provider, DEFAULT_DIAGNOSE_MODEL["groq"])
        return DEFAULT_GENERATE_MODEL.get(provider, DEFAULT_GENERATE_MODEL["groq"])


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up looking for pyproject.toml; fall back to CWD."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return current
