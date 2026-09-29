"""Unit tests: parsing helpers (code blocks + JSON out of LLM output)."""

import pytest

from testpilot.parsing import extract_all_code, extract_code, extract_json, strip_fences


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


class TestHelpers:
    def test_extract_all_code_returns_both_blocks(self):
        text = "```python\na=1\n```\ntext\n```python\nb=2\n```"
        assert extract_all_code(text) == ["a=1", "b=2"]

    def test_strip_fences_removes_wrapper(self):
        assert strip_fences("```python\nx = 1\n```") == "x = 1"

    def test_strip_fences_leaves_plain_text(self):
        assert strip_fences("x = 1") == "x = 1"

    def test_strip_fences_handles_prose_before_fence(self):
        # Regression: was re.fullmatch, so prose around the fence no-op'd.
        text = "Sure! Here is the file:\n```python\nx = 1\n```\nHope that helps."
        assert strip_fences(text) == "x = 1"

    def test_strip_fences_handles_prose_after_fence_only(self):
        text = "```py\ny = 2\n```\nDone."
        assert strip_fences(text) == "y = 2"

    def test_strip_fences_preserves_inner_multiline(self):
        text = "intro\n```python\ndef a():\n    return 1\n```\noutro"
        assert strip_fences(text) == "def a():\n    return 1"
