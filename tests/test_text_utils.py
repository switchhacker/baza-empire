"""Tests for dashboard/text_utils.py — text normalization helpers.

normalize_escaped_newlines heals LLM-extracted / pasted text where intended
line breaks arrived as the two literal characters backslash-n (e.g. a model
emitting \\n inside a JSON string), which otherwise render verbatim in a
textarea and pollute downstream prompts (see Ritz water-damage project bug,
2026-06-15).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard"))

from text_utils import normalize_escaped_newlines  # noqa: E402


def test_literal_backslash_n_becomes_real_newline():
    assert normalize_escaped_newlines("line one\\nline two") == "line one\nline two"


def test_mixed_literal_and_real_newlines_all_normalized():
    # The actual Ritz bug shape: a literal \n right next to a real newline.
    src = "(full height)\\n- Strip\n\\nNext section"
    out = normalize_escaped_newlines(src)
    assert "\\n" not in out
    assert out == "(full height)\n- Strip\n\nNext section"


def test_literal_crlf_collapses_to_single_newline():
    assert normalize_escaped_newlines("a\\r\\nb") == "a\nb"


def test_plain_text_with_real_newlines_is_unchanged():
    src = "Remove tile from the kitchen\ninstall new flooring"
    assert normalize_escaped_newlines(src) is src or normalize_escaped_newlines(src) == src


def test_text_without_backslashes_returned_unchanged():
    src = "kitchen, bathroom, addition"
    assert normalize_escaped_newlines(src) == src


def test_non_string_passthrough():
    assert normalize_escaped_newlines(None) is None
    assert normalize_escaped_newlines(123) == 123


def test_empty_string():
    assert normalize_escaped_newlines("") == ""
