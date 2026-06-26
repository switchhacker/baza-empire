"""Compose an agent-skill .py file from metadata fields while preserving the
existing code body verbatim. Pure functions — no Flask, no filesystem I/O.

A skill file's "header" is the leading shebang, module docstring, and a
top-level ``SKILL_META`` assignment. compose_skill_source() regenerates ONLY
that header from the form fields and keeps every line of logic below it, so the
metadata form can never clobber real skill code."""
import ast


def _meta_repr(category, summary, when_to_use, args):
    lines = ["SKILL_META = {"]
    lines.append(f"    'category': {category!r},")
    lines.append(f"    'summary': {summary!r},")
    lines.append(f"    'when_to_use': {when_to_use!r},")
    if args:
        lines.append("    'args': {")
        for k, v in args.items():
            lines.append(f"        {str(k)!r}: {str(v)!r},")
        lines.append("    },")
    else:
        lines.append("    'args': {},")
    lines.append("}")
    return "\n".join(lines)


def _strip_header(source):
    """Return source with leading shebang, module docstring, and a top-level
    SKILL_META assignment removed; leading blank lines trimmed."""
    lines = source.splitlines()
    remove = set()
    if lines and lines[0].startswith("#!"):
        remove.add(0)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        body = [l for i, l in enumerate(lines) if i not in remove]
        return "\n".join(body).lstrip("\n")
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(getattr(tree.body[0], "value", None), ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        d = tree.body[0]
        for i in range(d.lineno - 1, d.end_lineno):
            remove.add(i)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SKILL_META" for t in node.targets):
            for i in range(node.lineno - 1, node.end_lineno):
                remove.add(i)
            break
    body = [l for i, l in enumerate(lines) if i not in remove]
    return "\n".join(body).lstrip("\n")


def compose_skill_source(summary, when_to_use, category, args, body_source):
    """Build a full skill .py source: shebang + docstring(summary) + SKILL_META
    + the preserved code body extracted from body_source."""
    body = _strip_header(body_source or "")
    doc = (summary or "").replace('"""', "'''")
    header = "#!/usr/bin/env python3\n"
    header += f'"""{doc}"""\n\n'
    header += _meta_repr(category or "general", summary or "", when_to_use or "", args or {}) + "\n\n"
    out = header + body
    if not out.endswith("\n"):
        out += "\n"
    return out
