"""Tests for the shared git-commit parsing behind the PreToolUse guards."""

import io
import json
import os
import sys

import pytest

import commits
from hooktest import bash, git, stage

INDEXED = "indexed\n"
WORKTREE = "worktree\n"


def harvest(command, cwd, *wanted):
    """Return [(repo_root, {path: text})] for every commit the command would run."""
    return [
        (root, {path: content.decode() for path, content in contents.items()})
        for root, contents in commits.committed(bash(command, cwd), *wanted)
    ]


def only(command, cwd, *wanted):
    """Return {path: text} for a command holding exactly one commit."""
    found = harvest(command, cwd, *wanted)
    assert len(found) == 1
    return found[0][1]


def test_segments_splits_on_separators():
    assert list(commits.segments("a b && c; d | e")) == [["a", "b"], ["c"], ["d"], ["e"]]


def test_segments_drops_unparsable_command():
    assert not list(commits.segments("git commit -m 'oops"))


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
    segment = next(commits.segments(command))
    assert commits.parse_commit(segment, "/w") == (repo, all_flag, pathspec)


@pytest.mark.parametrize("command", ["git status", "ls", "gitk commit", "cd /w"])
def test_parse_commit_rejects_non_commits(command):
    segment = next(commits.segments(command))
    assert commits.parse_commit(segment, "/w") is None


def test_parse_commit_rejects_empty_segment():
    assert commits.parse_commit([], "/w") is None


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
    assert commits.resolve(base, path) == expected


def test_resolve_expands_home():
    assert commits.resolve("/w", "~/x") == os.path.join(os.path.expanduser("~"), "x")


def test_git_helper_returns_none_on_failure(repo):
    assert commits.git(str(repo), "rev-parse", "--verify", "--quiet", "HEAD") is None
    assert commits.git(str(repo), "rev-parse", "--show-toplevel", text=True) is not None


def test_git_helper_returns_none_when_git_is_missing(repo, monkeypatch):
    def missing(*_args, **_kwargs):
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(commits.subprocess, "run", missing)
    assert commits.git(str(repo), "rev-parse", "--show-toplevel", text=True) is None


def test_commit_contents_prefers_index_unless_all(repo):
    stage(repo, "m.py", INDEXED)
    (repo / "m.py").write_text(WORKTREE)
    assert commits.commit_contents(str(repo), False, []) == {"m.py": INDEXED.encode()}
    assert commits.commit_contents(str(repo), True, []) == {"m.py": WORKTREE.encode()}


def test_commit_contents_omits_deleted_pathspec_entry(repo):
    stage(repo, "gone.py", INDEXED)
    git(repo, "commit", "--quiet", "-m", "base")
    (repo / "gone.py").write_text(WORKTREE)
    (repo / "gone.py").unlink()
    assert not commits.commit_contents(str(repo), False, ["gone.py"])


def test_payload_parses_stdin(monkeypatch):
    event = bash("git commit -m wip", "/w")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert commits.payload() == event


@pytest.mark.parametrize("text", ["", "{not json", "[1, 2", "null}"])
def test_payload_returns_none_on_bad_stdin(text, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    assert commits.payload() is None


@pytest.mark.parametrize(
    "event",
    [
        None,
        {},
        {"tool_name": "Write", "tool_input": {"file_path": "m.py", "content": INDEXED}},
        {"tool_name": "Bash", "tool_input": {}},
    ],
)
def test_committed_ignores_events_without_a_bash_command(event, repo):
    stage(repo, "m.py", INDEXED)
    assert not list(commits.committed(event))


def test_committed_yields_repository_root_and_index_contents(repo):
    stage(repo, "m.py", INDEXED)
    root, contents = next(commits.committed(bash("git commit -m wip", repo)))
    assert root == os.path.realpath(repo)
    assert contents == {"m.py": INDEXED.encode()}


def test_committed_reports_a_commit_per_chained_command(repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "--quiet", "--initial-branch=main")
    stage(repo, "a.py", INDEXED)
    stage(other, "b.py", WORKTREE)
    command = f"git commit -m first && cd {other} && git commit -m second"
    assert [contents for _, contents in harvest(command, repo)] == [
        {"a.py": INDEXED},
        {"b.py": WORKTREE},
    ]


def test_committed_follows_cd(repo, tmp_path):
    stage(repo, "m.py", INDEXED)
    assert only(f"cd {repo} && git commit -m wip", tmp_path) == {"m.py": INDEXED}


def test_committed_follows_git_c_option(repo, tmp_path):
    stage(repo, "m.py", INDEXED)
    assert only(f"git -C {repo} commit -m wip", tmp_path) == {"m.py": INDEXED}


def test_committed_follows_bare_cd_to_home(repo):
    stage(repo, "m.py", INDEXED)
    assert not harvest("cd && git commit -m wip", repo)


def test_committed_follows_cd_dash_to_previous_directory(repo, tmp_path):
    stage(repo, "m.py", INDEXED)
    command = f"cd {tmp_path} && cd - && git commit -m wip"
    assert only(command, repo) == {"m.py": INDEXED}


def test_committed_ignores_non_commit_commands(repo):
    stage(repo, "m.py", INDEXED)
    assert not harvest("git status && git add -A", repo)


def test_committed_ignores_unparsable_command(repo):
    stage(repo, "m.py", INDEXED)
    assert not harvest("git commit -m 'unterminated", repo)


def test_committed_ignores_directory_outside_any_repo(tmp_path):
    (tmp_path / "m.py").write_text(INDEXED)
    assert not harvest("git commit -m wip", tmp_path)


def test_committed_honours_the_wanted_predicate(repo):
    stage(repo, "m.py", INDEXED)
    stage(repo, "notes.txt", WORKTREE)
    assert only("git commit -m wip", repo) == {"m.py": INDEXED, "notes.txt": WORKTREE}
    assert only("git commit -m wip", repo, lambda path: path.endswith(".py")) == {"m.py": INDEXED}


def test_committed_am_sees_unstaged_modification(repo):
    stage(repo, "m.py", INDEXED)
    (repo / "m.py").write_text(WORKTREE)
    assert only("git commit -am wip", repo) == {"m.py": WORKTREE}


def test_committed_plain_commit_ignores_dirty_worktree(repo):
    stage(repo, "m.py", INDEXED)
    git(repo, "commit", "--quiet", "-m", "base")
    (repo / "m.py").write_text(WORKTREE)
    assert only("git commit -m wip", repo) == {}


def test_committed_pathspec_limits_the_contents(repo):
    stage(repo, "clean.py", INDEXED)
    stage(repo, "dirty.py", INDEXED)
    git(repo, "commit", "--quiet", "-m", "base")
    (repo / "dirty.py").write_text(WORKTREE)
    assert only("git commit -m wip -- clean.py", repo) == {}
    assert only("git commit -m wip -- dirty.py", repo) == {"dirty.py": WORKTREE}


def test_committed_initial_commit_without_head(repo):
    assert commits.git(str(repo), "rev-parse", "--verify", "--quiet", "HEAD") is None
    stage(repo, "m.py", INDEXED)
    assert only("git commit -m first", repo) == {"m.py": INDEXED}
    assert only("git commit -m first -- m.py", repo) == {"m.py": INDEXED}


def test_committed_amend_of_dirty_worktree_uses_index(repo):
    stage(repo, "m.py", INDEXED)
    git(repo, "commit", "--quiet", "-m", "base")
    stage(repo, "other.py", INDEXED)
    (repo / "m.py").write_text(WORKTREE)
    assert only("git commit --amend --no-edit", repo) == {"other.py": INDEXED}


def test_deny_carries_the_reason_in_a_pretooluse_denial():
    assert commits.deny("because")["hookSpecificOutput"] == {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "because",
    }
