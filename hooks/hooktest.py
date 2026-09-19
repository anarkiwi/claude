"""Fixtures and helpers shared by the guard tests: a throwaway repo and the hook payloads."""

import io
import json
import os
import subprocess
import sys

import pytest


def git(repo, *args):
    subprocess.run(("git", "-C", str(repo)) + args, check=True, capture_output=True)


def stage(repo, name, content):
    blob = content if isinstance(content, bytes) else content.encode()
    (repo / name).write_bytes(blob)
    git(repo, "add", name)


@pytest.fixture(name="repo")
def repo_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "--quiet", "--initial-branch=main")
    git(path, "config", "user.email", "hook@example.invalid")
    git(path, "config", "user.name", "Hook Test")
    return path


def bash(command, cwd):
    return {"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}}


def drive(main, payload, monkeypatch, capsys):
    """Run a guard's main() in-process over payload; return its stdout (empty = allow)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main() == 0
    return capsys.readouterr().out.strip()


def reason(out):
    """The deny reason in a guard's stdout, asserting the payload shape around it."""
    hook = json.loads(out)["hookSpecificOutput"]
    assert hook["hookEventName"] == "PreToolUse"
    assert hook["permissionDecision"] == "deny"
    return hook["permissionDecisionReason"]
