"""Extraction helpers: fenced code blocks and JSON out of LLM text."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:python\d*|py\d*)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _balanced_spans(text: str) -> list[str]:
    """Yield top-level {...} spans using real brace matching.

    Handles nested objects/arrays and braces inside JSON strings — the naive
    `find("{") .. rfind("}")` and the non-greedy `.*?` regex both break on
    prose that trails the object or on `{"a": {"b": 1}}`.
    """
    spans: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                spans.append(text[start : i + 1])
                start = -1
    return spans


def extract_code(text: str) -> str:
    """Return the longest fenced code block, else the raw text.

    Prefers the longest block rather than the first: models often restate the
    original file briefly before emitting the full replacement, and the full
    file is the one worth keeping. Falls back to any fence, then the whole string.
    """
    blocks = _FENCE_RE.findall(text)
    if blocks:
        # Prefer the longest block — usually the full file.
        return max(blocks, key=len).strip("\n")

    # A bare fence with no language tag.
    bare = re.findall(r"```\s*\n(.*?)```", text, re.DOTALL)
    if bare:
        return max(bare, key=len).strip("\n")

    return text.strip()


def extract_all_code(text: str) -> list[str]:
    return [block.strip("\n") for block in _FENCE_RE.findall(text)]


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from LLM output; tolerate fences and prose."""
    text = text.strip()

    candidates: list[str] = []
    candidates.extend(_JSON_BLOCK_RE.findall(text))

    # Balanced-brace spans: robust to nesting, trailing prose, and stray braces.
    candidates.extend(_balanced_spans(text))

    candidates.append(text)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data

    raise ValueError(f"Could not extract JSON from model output: {text[:300]!r}")


def strip_fences(text: str) -> str:
    """Extract the first fenced block, tolerating prose around it.

    Uses re.search (not fullmatch) so "here you go:\n```py\nx=1\n```"
    returns the inner code instead of silently no-op'ing. Plain text with
    no fence at all is returned unchanged.
    """
    match = re.search(r"```[a-zA-Z]*[ \t]*\n(.*?)\n?```", text, re.DOTALL)
    return match.group(1) if match else text
