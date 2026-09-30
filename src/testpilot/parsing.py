"""Extraction helpers: fenced code blocks and JSON out of LLM text."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:python\d*|py\d*)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _balanced_spans(text: str) -> list[str]:
    """Return top-level {...} spans using real brace matching.

    Handles nested objects/arrays and braces inside JSON strings — the naive
    `find("{") .. rfind("}")` and the non-greedy `.*?` regex both break on
    prose that trails the object or on `{"a": {"b": 1}}`.

    Quote tracking only applies *inside* a span (`depth > 0`): an unbalanced
    quote in the surrounding prose — `the 6" pipe` — must not swallow the
    braces that follow it.
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
        if depth > 0 and ch == '"':
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
    file is the one worth keeping. The language tag is optional, so an untagged
    ``` fence is matched by the same regex — no second pass needed.
    """
    blocks = _FENCE_RE.findall(text)
    if blocks:
        # Prefer the longest block — usually the full file.
        return max(blocks, key=len).strip("\n")

    return text.strip()


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from LLM output; tolerate fences and prose.

    Several objects may parse — reasoning text routinely contains its own
    little `{"a": 1}` illustration, and the first one found used to win, which
    silently defaulted a real verdict to `unclear`. So: prefer the object that
    actually carries the answer key we asked for, and failing that the LAST
    object, which is where a model restates its final answer.
    """
    text = text.strip()

    candidates: list[str] = []
    candidates.extend(_JSON_BLOCK_RE.findall(text))

    # Balanced-brace spans: robust to nesting, trailing prose, and stray braces.
    candidates.extend(_balanced_spans(text))

    candidates.append(text)

    found: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            found.append(data)

    if not found:
        raise ValueError(f"Could not extract JSON from model output: {text[:300]!r}")

    for key in ("verdict", "answer", "result"):
        for data in found:
            if key in data:
                return data

    return found[-1]
