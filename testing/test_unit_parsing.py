"""Unit tests: parsing helpers (code blocks + JSON out of LLM output)."""

import pytest

from testpilot.parsing import extract_code, extract_json


class TestExtractCode:
    def test_single_fenced_python_block(self):
        text = "Here you go:\n```python\ndef test_a():\n    assert 1\n```\nDone."
        assert extract_code(text) == "def test_a():\n    assert 1"

    def test_unlabeled_fence(self):
        text = "```\nx = 1\n```"
        assert extract_code(text) == "x = 1"

    def test_returns_longest_block_when_multiple(self):
        text = "```python\na = 1\n```\nand\n```python\na = 1\nb = 2\nc = 3\n```"
        assert extract_code(text) == "a = 1\nb = 2\nc = 3"

    def test_plain_text_passthrough(self):
        assert extract_code("no fences here") == "no fences here"

    def test_empty_string(self):
        assert extract_code("") == ""


class TestExtractJson:
    def test_bare_json(self):
        assert extract_json('{"verdict": "code_bug"}') == {"verdict": "code_bug"}

    def test_json_in_fences(self):
        text = 'Sure!\n```json\n{"verdict": "test_bug", "reasoning": "x"}\n```'
        assert extract_json(text)["verdict"] == "test_bug"

    def test_json_surrounded_by_prose(self):
        text = 'Analysis first. {"verdict": "unclear", "reasoning": "ambiguous"} hope that helps'
        data = extract_json(text)
        assert data["verdict"] == "unclear"

    def test_raises_on_garbage(self):
        with pytest.raises(ValueError):
            extract_json("I could not decide, sorry.")

    def test_raises_on_json_array_not_object(self):
        with pytest.raises(ValueError):
            extract_json('["not", "an", "object"]')


class TestJsonPreference:
    """extract_json must pick the ANSWER, not the first brace it sees."""

    def test_prefers_the_object_carrying_the_verdict(self):
        # Reasoning text that contains its own little JSON illustration used
        # to win, silently defaulting a real verdict to unclear.
        text = 'For example {"a": 1} is illustrative. Final answer: {"verdict": "code_bug", "reasoning": "r"}'
        assert extract_json(text)["verdict"] == "code_bug"

    def test_prefers_verdict_even_when_it_comes_first_in_prose(self):
        text = '{"verdict": "test_bug"} is my call, unlike {"note": 1}'
        assert extract_json(text)["verdict"] == "test_bug"

    def test_falls_back_to_the_last_object_when_no_answer_key(self):
        text = 'first {"a": 1} then {"b": 2}'
        assert extract_json(text) == {"b": 2}

    def test_unbalanced_quote_in_prose_does_not_hide_the_object(self):
        # `_balanced_spans` used to start string mode at depth 0, so a lone
        # quote in the prose (`6" pipe`) swallowed every brace after it.
        text = 'bought the 6" pipe {"verdict": "unclear", "reasoning": "r"}'
        assert extract_json(text)["verdict"] == "unclear"
