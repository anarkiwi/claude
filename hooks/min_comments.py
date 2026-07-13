#!/usr/bin/env python3
"""PreToolUse guard denying Python writes with excess comment volume.
Caps docstring descriptions per PEP 257 and bans consecutive comments."""

import ast
import io
import json
import sys
import tokenize

PY_SUFFIXES = frozenset({"py", "pyi"})
MAX_DESCRIPTION_LINES = 3

DIRECTIVE = (
    "Blocked: this Python write carries excess comment volume. Per directive and "
    "PEP 257, keep each docstring to a one-line summary plus at most "
    f"{MAX_DESCRIPTION_LINES} lines of description (Args/Returns/Raises and other "
    "sections excluded), and use no consecutive full-line comments. State facts "
    "compactly, then merge or delete the excess."
)

_DEF_NODES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

# Google/NumPy docstring section headers; text from the first header on is excluded.
_DOC_SECTIONS = frozenset(
    {
        "args",
        "arguments",
        "parameters",
        "params",
        "param",
        "keyword args",
        "keyword arguments",
        "other parameters",
        "returns",
        "return",
        "yields",
        "yield",
        "raises",
        "raise",
        "except",
        "exceptions",
        "warns",
        "attributes",
        "attribute",
        "methods",
        "receives",
        "note",
        "notes",
        "example",
        "examples",
        "references",
        "reference",
        "see also",
        "todo",
        "warning",
        "warnings",
    }
)


def added_texts(tool_name, tool_input):
    """Yield (label, text) pairs for the text this call would add."""
    if tool_name == "Write":
        yield tool_input.get("file_path", ""), tool_input.get("content", "")
    elif tool_name == "Edit":
        yield tool_input.get("file_path", ""), tool_input.get("new_string", "")
    elif tool_name == "MultiEdit":
        path = tool_input.get("file_path", "")
        for i, edit in enumerate(tool_input.get("edits", [])):
            yield f"{path}#{i}", edit.get("new_string", "")
    elif tool_name == "NotebookEdit":
        yield tool_input.get("notebook_path", ""), tool_input.get("new_source", "")


def is_python(path):
    base = path.rsplit("#", 1)[0]
    if base.endswith(".ipynb"):
        return True
    parts = base.rsplit(".", 1)
    return len(parts) == 2 and parts[1].lower() in PY_SUFFIXES


def _is_section_header(line, next_line):
    text = line.strip()
    if not text:
        return False
    if text.endswith(":") and text[:-1].strip().lower() in _DOC_SECTIONS:
        return True
    underline = next_line.strip()
    return text.lower() in _DOC_SECTIONS and bool(underline) and set(underline) == {"-"}


def _description_lines(doc):
    """Count non-blank docstring lines before the first structured section."""
    lines = doc.splitlines()
    end = len(lines)
    for i, line in enumerate(lines):
        following = lines[i + 1] if i + 1 < len(lines) else ""
        if _is_section_header(line, following):
            end = i
            break
    return sum(1 for line in lines[:end] if line.strip())


def _docstring_violations(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return
    for node in ast.walk(tree):
        doc = ast.get_docstring(node) if isinstance(node, _DEF_NODES) else None
        if doc is None:
            continue
        body = _description_lines(doc) - 1
        if body > MAX_DESCRIPTION_LINES:
            yield node.body[0].value.lineno, (
                f"docstring description is {body} lines "
                f"(PEP 257 limit {MAX_DESCRIPTION_LINES}, sections excluded)"
            )


def _standalone_comment_lines(text):
    lines = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and not tok.line[: tok.start[1]].strip():
                lines.append(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return lines


def _comment_violations(text):
    run = []
    for lineno in _standalone_comment_lines(text):
        if run and lineno == run[-1] + 1:
            run.append(lineno)
            continue
        if len(run) >= 2:
            yield run[0], f"{len(run)} consecutive comment lines ({run[0]}-{run[-1]})"
        run = [lineno]
    if len(run) >= 2:
        yield run[0], f"{len(run)} consecutive comment lines ({run[0]}-{run[-1]})"


def find_violations(text):
    """Return [(lineno, message)] for comment-volume violations in text."""
    found = list(_docstring_violations(text)) + list(_comment_violations(text))
    return sorted(found)


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    found = []
    for label, text in added_texts(tool_name, tool_input):
        if is_python(label):
            found += [(label, ln, msg) for ln, msg in find_violations(text)]
    if not found:
        return 0

    detail = "; ".join(f"{lbl} line {ln}: {msg}" for lbl, ln, msg in found[:8])
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"{DIRECTIVE}\nViolations: {detail}",
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
