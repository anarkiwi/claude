#!/usr/bin/python3
"""Shared parsing for the PreToolUse guards that inspect a `git commit`.
Follows `cd`, `git -C` and `&&` chains through the Bash command, then returns
the bytes each commit in it would record, so a guard sees the committed
content rather than the worktree."""

import json
import os
import shlex
import subprocess
import sys

SEPARATORS = frozenset({"&&", "||", ";", ";;", "|", "&", "(", ")", "\n"})
# git options consuming the following token, before the subcommand.
GIT_VALUE_OPTS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--exec-path", "--namespace"})
# Commit options consuming a value, so the token after them is not a pathspec.
COMMIT_VALUE_SHORT = frozenset("mFtcC")
COMMIT_VALUE_LONG = frozenset(
    {
        "--message",
        "--file",
        "--author",
        "--date",
        "--template",
        "--cleanup",
        "--reuse-message",
        "--reedit-message",
        "--fixup",
        "--squash",
        "--trailer",
        "--pathspec-from-file",
    }
)


def resolve(base, path):
    """Resolve path against base, as a shell cd would."""
    return os.path.normpath(os.path.join(base, os.path.expanduser(path)))


def segments(command):
    """Split a shell command into argv lists, one per simple command."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return
    current = []
    for token in tokens:
        if token in SEPARATORS:
            yield current
            current = []
        else:
            current.append(token)
    yield current


def _skip_git_options(segment, cwd):
    """Return (repo_dir, index of the subcommand) for a git invocation."""
    repo, i = cwd, 1
    while i < len(segment):
        token = segment[i]
        if not token.startswith("-"):
            break
        name, _, inline = token.partition("=")
        takes_value = name in GIT_VALUE_OPTS and not inline
        value = inline or (segment[i + 1] if takes_value and i + 1 < len(segment) else "")
        if name == "-C" and value:
            repo = resolve(repo, value)
        i += 2 if takes_value else 1
    return repo, i


def parse_commit(segment, cwd):
    """Return (repo_dir, all_flag, pathspec) if the segment is a git commit, else None."""
    if not segment or os.path.basename(segment[0]) != "git":
        return None
    repo, i = _skip_git_options(segment, cwd)
    if i >= len(segment) or segment[i] != "commit":
        return None
    args, all_flag, pathspec, literal = segment[i + 1 :], False, [], False
    j = 0
    while j < len(args):
        token, j = args[j], j + 1
        if literal or not token.startswith("-") or token == "-":
            pathspec.append(token)
        elif token == "--":
            literal = True
        elif token.startswith("--"):
            all_flag = all_flag or token == "--all"
            j += token.partition("=")[0] in COMMIT_VALUE_LONG and "=" not in token
        else:
            for k, char in enumerate(token[1:], 1):
                if char in COMMIT_VALUE_SHORT:
                    j += k == len(token) - 1
                    break
                all_flag = all_flag or char == "a"
    return repo, all_flag, pathspec


def git(repo, *args, text=False):
    """Run git in repo, returning stdout, or None if the command fails."""
    try:
        proc = subprocess.run(
            ("git", "-C", repo) + args, capture_output=True, check=False, text=text
        )
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _names(out):
    return [name for name in (out or "").split("\0") if name]


def _everything(path):
    return bool(path)


def commit_contents(repo, all_flag, pathspec, wanted=_everything):
    """Return {path: bytes} for the files this commit would record that wanted() selects."""
    if pathspec:
        base = "HEAD" if git(repo, "rev-parse", "--verify", "--quiet", "HEAD") else "--cached"
        sources = dict.fromkeys(
            _names(
                git(
                    repo,
                    "diff",
                    "--name-only",
                    "--diff-filter=ACMR",
                    "-z",
                    base,
                    "--",
                    *pathspec,
                    text=True,
                )
            ),
            False,
        )
    else:
        sources = dict.fromkeys(
            _names(
                git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z", text=True)
            ),
            True,
        )
        if all_flag:
            sources.update(
                dict.fromkeys(
                    _names(git(repo, "diff", "--name-only", "--diff-filter=ACMR", "-z", text=True)),
                    False,
                )
            )
    contents = {}
    for path, from_index in sources.items():
        if not wanted(path):
            continue
        if from_index:
            content = git(repo, "show", f":{path}")
        else:
            try:
                with open(os.path.join(repo, path), "rb") as handle:
                    content = handle.read()
            except OSError:
                content = None
        if content is not None:
            contents[path] = content
    return contents


def payload():
    """Return the hook payload waiting on stdin, or None if it is not JSON."""
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None


def committed(event, wanted=_everything):
    """Yield (repo_root, {path: bytes}) for every git commit the Bash payload would run."""
    if not event or event.get("tool_name") != "Bash":
        return
    cwd = previous = event.get("cwd") or os.getcwd()
    for segment in segments(event.get("tool_input", {}).get("command", "")):
        if segment and segment[0] == "cd":
            target = segment[1] if len(segment) > 1 else "~"
            cwd, previous = previous if target == "-" else resolve(cwd, target), cwd
            continue
        commit = parse_commit(segment, cwd)
        if commit is None:
            continue
        repo, all_flag, pathspec = commit
        root = git(repo, "rev-parse", "--show-toplevel", text=True)
        if root is None:
            continue
        root = root.strip()
        yield root, commit_contents(root, all_flag, pathspec, wanted)


def deny(reason):
    """Render the PreToolUse deny payload carrying reason."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
