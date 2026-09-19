"""Tests for the format_check PreToolUse guard."""

import io
import json
import os
import subprocess
import sys

import pytest

import format_check

BAD_PY = "def f( a ):\n  return  a\n"
GOOD_PY = "def f(a):\n    return a\n"
BAD_CC = "int main(){return 0;}\n"
GOOD_CC = "int main() { return 0; }\n"


def git(repo, *args):
    subprocess.run(("git", "-C", str(repo)) + args, check=True, capture_output=True)


def stage(repo, name, content):
    (repo / name).write_text(content)
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


def run(payload, monkeypatch, capsys):
    """Drive main() in-process; return its stdout (empty string = allow)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert format_check.main() == 0
    return capsys.readouterr().out.strip()


def bash(command, cwd):
    return {"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}}


def reason(out):
    hook = json.loads(out)["hookSpecificOutput"]
    assert hook["hookEventName"] == "PreToolUse"
    assert hook["permissionDecision"] == "deny"
    return hook["permissionDecisionReason"]


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


def test_unparsable_command_allowed(repo, monkeypatch, capsys):
    stage(repo, "bad.py", BAD_PY)
    assert run(bash("git commit -m 'unterminated", repo), monkeypatch, capsys) == ""


def test_outside_any_repo_allowed(tmp_path, monkeypatch, capsys):
    (tmp_path / "bad.py").write_text(BAD_PY)
    assert run(bash("git commit -m wip", tmp_path), monkeypatch, capsys) == ""


def test_cd_then_commit(repo, tmp_path, monkeypatch, capsys):
    stage(repo, "bad.py", BAD_PY)
    out = run(bash(f"cd {repo} && git commit -m wip", tmp_path), monkeypatch, capsys)
    assert "bad.py" in reason(out)


def test_git_c_option_form(repo, tmp_path, monkeypatch, capsys):
    stage(repo, "bad.py", BAD_PY)
    out = run(bash(f"git -C {repo} commit -m wip", tmp_path), monkeypatch, capsys)
    assert "bad.py" in reason(out)


def test_bare_cd_leaves_home(repo, monkeypatch, capsys):
    stage(repo, "bad.py", BAD_PY)
    assert run(bash("cd && git commit -m wip", repo), monkeypatch, capsys) == ""


def test_cd_dash_returns_to_previous_directory(repo, tmp_path, monkeypatch, capsys):
    stage(repo, "bad.py", BAD_PY)
    command = f"cd {tmp_path} && cd - && git commit -m wip"
    assert "bad.py" in reason(run(bash(command, repo), monkeypatch, capsys))


def test_commit_am_sees_unstaged_modification(repo, monkeypatch, capsys):
    stage(repo, "m.py", GOOD_PY)
    (repo / "m.py").write_text(BAD_PY)
    assert "m.py" in reason(run(bash("git commit -am wip", repo), monkeypatch, capsys))


def test_plain_commit_ignores_dirty_worktree(repo, monkeypatch, capsys):
    stage(repo, "m.py", GOOD_PY)
    (repo / "m.py").write_text(BAD_PY)
    assert run(bash("git commit -m wip", repo), monkeypatch, capsys) == ""


def test_pathspec_limits_the_check(repo, monkeypatch, capsys):
    stage(repo, "clean.py", GOOD_PY)
    stage(repo, "dirty.py", GOOD_PY)
    git(repo, "commit", "--quiet", "-m", "base")
    (repo / "dirty.py").write_text(BAD_PY)
    assert run(bash("git commit -m wip -- clean.py", repo), monkeypatch, capsys) == ""
    out = run(bash("git commit -m wip -- dirty.py", repo), monkeypatch, capsys)
    assert "dirty.py" in reason(out)


def test_unformattable_suffixes_ignored(repo, monkeypatch, capsys):
    stage(repo, "notes.txt", BAD_PY)
    stage(repo, "README.md", BAD_CC)
    stage(repo, "Makefile", BAD_PY)
    assert run(bash("git commit -m wip", repo), monkeypatch, capsys) == ""


def test_initial_commit_without_head_denied(repo, monkeypatch, capsys):
    proc = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "HEAD"),
        check=False,
        capture_output=True,
    )
    assert proc.returncode != 0
    stage(repo, "bad.py", BAD_PY)
    assert "bad.py" in reason(run(bash("git commit -m first", repo), monkeypatch, capsys))


def test_amend_of_dirty_worktree_uses_index(repo, monkeypatch, capsys):
    stage(repo, "m.py", GOOD_PY)
    git(repo, "commit", "--quiet", "-m", "base")
    stage(repo, "other.py", GOOD_PY)
    (repo / "m.py").write_text(BAD_PY)
    assert run(bash("git commit --amend --no-edit", repo), monkeypatch, capsys) == ""


def test_segments_splits_on_separators():
    assert list(format_check.segments("a b && c; d | e")) == [["a", "b"], ["c"], ["d"], ["e"]]


def test_segments_drops_unparsable_command():
    assert not list(format_check.segments("git commit -m 'oops"))


COMMITS = [
    ("git commit", "/w", False, []),
    ("git commit -m msg", "/w", False, []),
    ("git commit -m msg a.py", "/w", False, ["a.py"]),
    ("git commit --message=wip a.py", "/w", False, ["a.py"]),
    ("git commit --message wip a.py", "/w", False, ["a.py"]),
    ("git commit -qam msg", "/w", True, []),
    ("git commit -a", "/w", True, []),
    ("git commit --all", "/w", True, []),
    ("git commit --amend --no-edit", "/w", False, []),
    ("git commit -mmsg a.py", "/w", False, ["a.py"]),
    ("git commit --file notes.txt a.py", "/w", False, ["a.py"]),
    ("git commit -m msg -- --amend", "/w", False, ["--amend"]),
    ("git commit -- a.py b.cc", "/w", False, ["a.py", "b.cc"]),
    ("/usr/bin/git commit -m msg", "/w", False, []),
    ("git -C sub commit -m msg", "/w/sub", False, []),
    ("git -C /elsewhere commit -a", "/elsewhere", True, []),
    ("git -c user.name=x commit -m msg", "/w", False, []),
    ("git --git-dir=/w/.git commit -m msg", "/w", False, []),
]


@pytest.mark.parametrize("command,repo,all_flag,pathspec", COMMITS)
def test_parse_commit(command, repo, all_flag, pathspec):
    segment = next(format_check.segments(command))
    assert format_check.parse_commit(segment, "/w") == (repo, all_flag, pathspec)


@pytest.mark.parametrize("command", ["git status", "ls", "gitk commit", "cd /w"])
def test_parse_commit_rejects_non_commits(command):
    segment = next(format_check.segments(command))
    assert format_check.parse_commit(segment, "/w") is None


def test_parse_commit_rejects_empty_segment():
    assert format_check.parse_commit([], "/w") is None


@pytest.mark.parametrize(
    "base,path,expected",
    [
        ("/w", "sub", "/w/sub"),
        ("/w", "/abs/dir", "/abs/dir"),
        ("/w/a", "../b", "/w/b"),
        ("/w", ".", "/w"),
    ],
)
def test_resolve(base, path, expected):
    assert format_check.resolve(base, path) == expected


def test_resolve_expands_home():
    assert format_check.resolve("/w", "~/x") == os.path.join(os.path.expanduser("~"), "x")


@pytest.mark.parametrize("path", ["m.py", "m.pyi", "pkg/M.PY"])
def test_formatter_for_python(path):
    assert format_check.formatter_for(path) is format_check.BLACK


@pytest.mark.parametrize("path", ["m.cc", "m.hpp", "m.cu", "m.c++"])
def test_formatter_for_cxx(path):
    assert format_check.formatter_for(path) is format_check.CLANG_FORMAT


@pytest.mark.parametrize("path", ["notes.txt", "README.md", "Makefile", "dir.py/script"])
def test_formatter_for_unknown(path):
    assert format_check.formatter_for(path) is None


def test_deny_groups_paths_under_one_fix():
    found = [
        ("a.py", format_check.BLACK, "black would reformat it"),
        ("b.py", format_check.BLACK, "black would reformat it"),
    ]
    detail = format_check.deny(found)["hookSpecificOutput"]["permissionDecisionReason"]
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


def test_git_helper_returns_none_on_failure(repo):
    assert format_check.git(str(repo), "rev-parse", "--verify", "--quiet", "HEAD") is None
    assert format_check.git(str(repo), "rev-parse", "--show-toplevel", text=True) is not None


def test_commit_contents_prefers_index_unless_all(repo):
    stage(repo, "m.py", GOOD_PY)
    (repo / "m.py").write_text(BAD_PY)
    assert format_check.commit_contents(str(repo), False, []) == {"m.py": GOOD_PY.encode()}
    assert format_check.commit_contents(str(repo), True, []) == {"m.py": BAD_PY.encode()}


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
