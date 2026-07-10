"""Tests for the no_guess PreToolUse guard."""

import io
import json
import subprocess
import sys

import pytest

import no_guess

HOOK = no_guess.__file__


def run(payload, monkeypatch, capsys):
    """Drive main() in-process; return its stdout (empty string = allow)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert no_guess.main() == 0
    return capsys.readouterr().out.strip()


def write(path, content):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


def denied(out):
    return bool(out) and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


GUESSING = [
    "x = 0.7  # magic number",
    "eps = 1e-6  # fudge factor for stability",
    "n = 42  # tuned this threshold until tests pass",
    "k = 3  // empirically chosen",
    "scale = 1.5  # arbitrary value that seems fine",
    "delta = 0.1  # bumped until it works",
    "w = 2  # no idea why but works",
    "t = 5  # keep raising until the tests pass",
    "# just guessing here\nfoo = 9",
    "y = 1  # this should be enough",
]

CLEAN = [
    "TWO_PI = 2 * math.pi  # full turn in radians",
    "GRAVITY = 9.80665  # standard gravity, CODATA 2018",
    "def tune_model(cfg):\n    return cfg  # configure model from cfg",
    "guess_count = len(candidates)  # number of proposals to score",
    "timeout = int(os.environ['TIMEOUT'])  # from caller",
    "# derived from RFC 791: minimum IPv4 header is 20 bytes\nHDR = 20",
]


@pytest.mark.parametrize("content", GUESSING)
def test_blocks_guessing(content, monkeypatch, capsys):
    assert denied(run(write("m.py", content), monkeypatch, capsys))


@pytest.mark.parametrize("content", CLEAN)
def test_allows_clean(content, monkeypatch, capsys):
    assert run(write("m.py", content), monkeypatch, capsys) == ""


def test_ignores_non_code_files(monkeypatch, capsys):
    assert run(write("notes.md", "x = 0.7  # magic number"), monkeypatch, capsys) == ""


def test_scans_only_added_text_on_edit(monkeypatch, capsys):
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "m.py",
            "old_string": "# magic number",
            "new_string": "y = 1  # radius",
        },
    }
    assert run(payload, monkeypatch, capsys) == ""


def test_edit_flags_new_string(monkeypatch, capsys):
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "m.py",
            "old_string": "a",
            "new_string": "z = 1  # fudge factor",
        },
    }
    assert denied(run(payload, monkeypatch, capsys))


def test_multiedit_flags_any_edit(monkeypatch, capsys):
    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": "m.py",
            "edits": [{"new_string": "a = 1  # count"}, {"new_string": "b = 2  # fudge factor"}],
        },
    }
    assert denied(run(payload, monkeypatch, capsys))


def test_notebook_scanned_as_code(monkeypatch, capsys):
    payload = {
        "tool_name": "NotebookEdit",
        "tool_input": {"notebook_path": "n.ipynb", "new_source": "z = 3  # magic constant"},
    }
    assert denied(run(payload, monkeypatch, capsys))


def test_unknown_tool_allows(monkeypatch, capsys):
    assert run({"tool_name": "Bash", "tool_input": {"command": "ls"}}, monkeypatch, capsys) == ""


def test_truncates_to_eight_tells(monkeypatch, capsys):
    content = "\n".join(f"v{i} = {i}  # magic number" for i in range(20))
    reason = json.loads(run(write("m.py", content), monkeypatch, capsys))
    assert reason["hookSpecificOutput"]["permissionDecisionReason"].count("line ") == 8


def test_malformed_stdin_allows(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert no_guess.main() == 0
    assert capsys.readouterr().out.strip() == ""


def test_end_to_end_subprocess():
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(write("m.py", "x = 1  # magic number")),
        capture_output=True,
        text=True,
        check=True,
    )
    assert denied(proc.stdout.strip())
