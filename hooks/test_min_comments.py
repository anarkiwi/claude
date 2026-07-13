"""Tests for the min_comments PreToolUse guard."""

import io
import json
import subprocess
import sys

import pytest

import min_comments

HOOK = min_comments.__file__


def run(payload, monkeypatch, capsys):
    """Drive main() in-process; return its stdout (empty string = allow)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert min_comments.main() == 0
    return capsys.readouterr().out.strip()


def write(path, content):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


def denied(out):
    return bool(out) and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


VERBOSE = [
    '"""line one\nline two\nline three\nline four"""\nx = 1\n',
    'def f():\n    """one\n    two\n    three\n    four"""\n    return 1\n',
    'class C:\n    """a\n    b\n    c\n    d"""\n',
    "# first note\n# second note\nx = 1\n",
    "x = 1\n# a\n# b\n# c\ny = 2\n",
    "def f():\n    # step one\n    # step two\n    return 1\n",
]

TERSE = [
    'def f():\n    """one\n    two\n    three"""\n    return 1\n',
    'def f():\n    """single line."""\n    return 1\n',
    "x = 1  # radius\n",
    "# lone note\nx = 1\n",
    "# note\n\n# separated note\nx = 1\n",
    "x = 1  # a\ny = 2  # b\n",
    "def f():\n    return 1\n",
]


@pytest.mark.parametrize("content", VERBOSE)
def test_blocks_verbose(content, monkeypatch, capsys):
    assert denied(run(write("m.py", content), monkeypatch, capsys))


@pytest.mark.parametrize("content", TERSE)
def test_allows_terse(content, monkeypatch, capsys):
    assert run(write("m.py", content), monkeypatch, capsys) == ""


def test_ignores_non_python_files(monkeypatch, capsys):
    assert run(write("notes.md", "# a\n# b\n# c\n"), monkeypatch, capsys) == ""


def test_pyi_scanned(monkeypatch, capsys):
    assert denied(run(write("m.pyi", "# a\n# b\n"), monkeypatch, capsys))


def test_edit_flags_new_string(monkeypatch, capsys):
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "m.py", "old_string": "a", "new_string": "    # a\n    # b\n"},
    }
    assert denied(run(payload, monkeypatch, capsys))


def test_edit_allows_clean_new_string(monkeypatch, capsys):
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "m.py", "old_string": "a", "new_string": "y = 1  # radius\n"},
    }
    assert run(payload, monkeypatch, capsys) == ""


def test_multiedit_flags_any_edit(monkeypatch, capsys):
    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": "m.py",
            "edits": [{"new_string": "a = 1  # ok"}, {"new_string": "# a\n# b\n"}],
        },
    }
    assert denied(run(payload, monkeypatch, capsys))


def test_notebook_scanned_as_python(monkeypatch, capsys):
    payload = {
        "tool_name": "NotebookEdit",
        "tool_input": {"notebook_path": "n.ipynb", "new_source": "# a\n# b\n"},
    }
    assert denied(run(payload, monkeypatch, capsys))


def test_unknown_tool_allows(monkeypatch, capsys):
    assert run({"tool_name": "Bash", "tool_input": {"command": "ls"}}, monkeypatch, capsys) == ""


def test_boundary_three_line_docstring_allowed(monkeypatch, capsys):
    content = 'def f():\n    """one\n    two\n    three"""\n    return 1\n'
    assert run(write("m.py", content), monkeypatch, capsys) == ""


def test_blank_line_breaks_comment_run(monkeypatch, capsys):
    assert run(write("m.py", "# a\n\n# b\n"), monkeypatch, capsys) == ""


def test_truncates_to_eight_violations(monkeypatch, capsys):
    content = "".join(f"x{i} = {i}\n# a{i}\n# b{i}\n" for i in range(20))
    reason = json.loads(run(write("m.py", content), monkeypatch, capsys))
    assert reason["hookSpecificOutput"]["permissionDecisionReason"].count(" line ") == 8


def test_malformed_stdin_allows(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert min_comments.main() == 0
    assert capsys.readouterr().out.strip() == ""


def test_end_to_end_subprocess():
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(write("m.py", "# a\n# b\n")),
        capture_output=True,
        text=True,
        check=True,
    )
    assert denied(proc.stdout.strip())
