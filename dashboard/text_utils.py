"""Small, dependency-free text helpers shared by the dashboard.

Kept separate from app.py so it can be unit-tested without booting Flask.
"""


def normalize_escaped_newlines(s):
    """Heal text whose line breaks arrived as literal escape sequences.

    LLM extraction / pastes sometimes deliver the two literal characters
    backslash-n (or backslash-r-backslash-n) where a real newline was meant —
    e.g. a model emitting ``\\n`` inside a JSON string. Stored verbatim, these
    render as ``\\n`` in textareas and pollute downstream prompts.

    Converts literal ``\\r\\n`` / ``\\n`` / ``\\r`` to a real newline. Strings
    with no backslash are returned unchanged; non-strings pass through.
    """
    if not isinstance(s, str) or "\\" not in s:
        return s
    return s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
