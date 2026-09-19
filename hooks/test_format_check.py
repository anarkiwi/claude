"""Tests for the format_check PreToolUse guard."""

import io
import json
import subprocess
import sys

import pytest

import format_check
import hooktest
from hooktest import bash, reason, stage

BAD_PY = "def f( a ):\n  return  a\n"
GOOD_PY = "def f(a):\n    return a\n"
BAD_CC = "int main(){return 0;}\n"
GOOD_CC = "int main() { return 0; }\n"


def run(payload, monkeypatch, capsys):
    return hooktest.drive(format_check.main, payload, monkeypatch, capsys)


def test_unformatted_python_denied(repo, monkeypatch, capsys):
    stage(repo, "bad.py", BAD_PY)
    detail = reason(run(bash("git commit -m wip", repo), monkeypatch, capsys))
    assert "bad.py: black would reformat it" in detail
    assert "Fix: black bad.py" in detail


def test_unformatted_cxx_denied(repo, monkeypatch, capsys):
    stage(repo, "bad.cc", BAD_CC)
    detail = reason(run(bash("git commit -m wip", repo), monkeypatch, capsys))
    assert "bad.cc: bad.cc:1:11: error: code should be clang-formatted" in detail
    assert "Fix: clang-format -i bad.cc" in detail


def test_both_languages_denied_with_both_fixes(repo, monkeypatch, capsys):
    stage(repo, "bad.py", BAD_PY)
    stage(repo, "bad.cc", BAD_CC)
    detail = reason(run(bash("git commit -m wip", repo), monkeypatch, capsys))
    fix = detail.split("Fix: ")[1]
    assert "black bad.py" in fix
    assert "clang-format -i bad.cc" in fix
    assert " && " in fix


def test_clean_staged_content_allowed(repo, monkeypatch, capsys):
    stage(repo, "good.py", GOOD_PY)
    stage(repo, "good.cc", GOOD_CC)
    assert run(bash("git commit -m wip", repo), monkeypatch, capsys) == ""


def test_non_bash_tool_allowed(repo, monkeypatch, capsys):
    stage(repo, "bad.py", BAD_PY)
    payload = {"tool_name": "Write", "tool_input": {"file_path": "bad.py", "content": BAD_PY}}
    assert run(payload, monkeypatch, capsys) == ""


def test_non_commit_command_allowed(repo, monkeypatch, capsys):
    stage(repo, "bad.py", BAD_PY)
    assert run(bash("git status && git add -A", repo), monkeypatch, capsys) == ""


def test_malformed_stdin_allows(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert format_check.main() == 0
    assert capsys.readouterr().out == ""


def test_empty_stdin_allows(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert format_check.main() == 0
    assert capsys.readouterr().out == ""


def test_unformattable_suffixes_ignored(repo, monkeypatch, capsys):
    stage(repo, "notes.txt", BAD_PY)
    stage(repo, "README.md", BAD_CC)
    stage(repo, "Makefile", BAD_PY)
    assert run(bash("git commit -m wip", repo), monkeypatch, capsys) == ""


def test_worktree_content_denied_when_committed_with_all(repo, monkeypatch, capsys):
    stage(repo, "m.py", GOOD_PY)
    (repo / "m.py").write_text(BAD_PY)
    assert "m.py" in reason(run(bash("git commit -am wip", repo), monkeypatch, capsys))


@pytest.mark.parametrize("path", ["m.py", "m.pyi", "pkg/M.PY"])
def test_formatter_for_python(path):
    assert format_check.formatter_for(path) is format_check.BLACK


@pytest.mark.parametrize("path", ["m.cc", "m.hpp", "m.cu", "m.c++"])
def test_formatter_for_cxx(path):
    assert format_check.formatter_for(path) is format_check.CLANG_FORMAT


@pytest.mark.parametrize("path", ["notes.txt", "README.md", "Makefile", "dir.py/script"])
def test_formatter_for_unknown(path):
    assert format_check.formatter_for(path) is None


def test_reason_groups_paths_under_one_fix():
    found = [
        ("a.py", format_check.BLACK, "black would reformat it"),
        ("b.py", format_check.BLACK, "black would reformat it"),
    ]
    detail = format_check.reason(found)
    assert detail.startswith(format_check.DIRECTIVE)
    assert detail.endswith("Fix: black a.py b.py")
    assert "a.py: black would reformat it; b.py:" in detail


def test_check_accepts_formatted_content(repo):
    assert format_check.check(str(repo), format_check.BLACK, "m.py", GOOD_PY.encode()) is None


def test_check_reports_missing_formatter(repo, monkeypatch):
    def missing(*_args, **_kwargs):
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(format_check.subprocess, "run", missing)
    complaint = format_check.check(str(repo), format_check.CLANG_FORMAT, "m.cc", BAD_CC.encode())
    assert complaint == "clang-format is not installed -- install it, do not skip the check"


def test_missing_formatter_denies_rather_than_skipping(repo, monkeypatch, capsys):
    monkeypatch.setattr(
        format_check, "check", lambda *_args: "black is not installed -- install it"
    )
    stage(repo, "good.py", GOOD_PY)
    detail = reason(run(bash("git commit -m wip", repo), monkeypatch, capsys))
    assert "good.py: black is not installed -- install it" in detail


def test_end_to_end_subprocess(repo):
    stage(repo, "bad.py", BAD_PY)
    proc = subprocess.run(
        [sys.executable, format_check.__file__],
        input=json.dumps(bash("git commit -m wip", repo)),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "bad.py" in reason(proc.stdout.strip())
