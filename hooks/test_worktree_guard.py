"""Tests for the worktree_guard PreToolUse guard."""

import io
import subprocess
import sys

import pytest

import hooktest
import worktree_guard
from hooktest import bash, reason


@pytest.fixture(name="session")
def session_fixture(repo, monkeypatch):
    hooktest.git(repo, "commit", "--quiet", "--allow-empty", "-m", "root")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    return repo


def run(payload, monkeypatch, capsys):
    return hooktest.drive(worktree_guard.main, payload, monkeypatch, capsys)


@pytest.mark.parametrize(
    "command",
    [
        "git worktree add ../elsewhere",
        "git worktree add -b feat ../elsewhere main",
        "git worktree add --lock --reason why /tmp/elsewhere",
        "git worktree add -- ../elsewhere",
        "cd .. && git worktree add elsewhere",
        "git -C .. worktree add elsewhere",
        "git worktree add .claude/worktrees/../../../elsewhere",
        "git worktree move .claude/worktrees/a ../elsewhere",
        "git fetch && git worktree add ok/a && git worktree add ../elsewhere",
    ],
)
def test_outside_denied(session, command, monkeypatch, capsys):
    detail = reason(run(bash(command, session), monkeypatch, capsys))
    assert f"inside the session's repo ({session})" in detail
    assert str(session.parent / "elsewhere") in detail or "/tmp/elsewhere" in detail
    assert "ok/a" not in detail


@pytest.mark.parametrize(
    "command",
    [
        "git worktree add .claude/worktrees/feat",
        "git worktree add -b feat .claude/worktrees/feat origin/main",
        "cd sub && git worktree add ../.claude/worktrees/feat",
        "git worktree move ../somewhere .claude/worktrees/feat",
        "git worktree list",
        "git worktree remove ../elsewhere",
        "git worktree add -b feat",
        "git worktree",
        "git status",
        "echo git worktree add ../elsewhere",
        "git worktree add 'unterminated",
    ],
)
def test_inside_or_unrelated_allowed(session, command, monkeypatch, capsys):
    assert run(bash(command, session), monkeypatch, capsys) == ""


def test_symlink_escape_denied(session, tmp_path, monkeypatch, capsys):
    (tmp_path / "outside").mkdir()
    (session / "link").symlink_to(tmp_path / "outside")
    assert reason(run(bash("git worktree add link/feat", session), monkeypatch, capsys))


def test_boundary_is_main_worktree(session, monkeypatch, capsys):
    linked = session / ".claude" / "worktrees" / "a"
    hooktest.git(session, "worktree", "add", "--quiet", "-b", "a", str(linked))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(linked))
    assert run(bash("git worktree add ../b", linked), monkeypatch, capsys) == ""
    assert reason(run(bash("git worktree add ../../../../b", linked), monkeypatch, capsys))


def test_project_outside_repo_allowed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert run(bash("git worktree add /elsewhere", tmp_path), monkeypatch, capsys) == ""


def test_payload_cwd_used_without_project_dir(session, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    assert reason(run(bash("git worktree add ../x", session), monkeypatch, capsys))


@pytest.mark.usefixtures("session")
def test_non_bash_and_bad_payload_allowed(monkeypatch, capsys):
    payload = {"tool_name": "Write", "tool_input": {"file_path": "../x"}}
    assert run(payload, monkeypatch, capsys) == ""
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert worktree_guard.main() == 0
    assert capsys.readouterr().out == ""


def test_script_entrypoint(session):
    proc = subprocess.run(
        [sys.executable, worktree_guard.__file__],
        input='{"tool_name": "Bash", "tool_input": {"command": "git worktree add ../x"}}',
        capture_output=True,
        text=True,
        check=True,
        env={"CLAUDE_PROJECT_DIR": str(session), "PATH": "/usr/bin:/bin"},
    )
    assert reason(proc.stdout)
