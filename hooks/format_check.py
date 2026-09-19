#!/usr/bin/env python3
"""PreToolUse guard denying git commits whose content fails a required formatter.
Checks the bytes the commit would record -- index, worktree or pathspec -- so a
style failure surfaces here rather than in CI."""

import collections
import json
import os
import shlex
import subprocess
import sys

Formatter = collections.namedtuple("Formatter", "name argv trailer fix")

BLACK = Formatter(
    "black",
    ("black", "--check", "--quiet", "--stdin-filename"),
    ("-",),
    "black",
)
CLANG_FORMAT = Formatter(
    "clang-format",
    (
        "clang-format",
        "--dry-run",
        "-Werror",
        "--style=file",
        "--fallback-style=LLVM",
        "--assume-filename",
    ),
    (),
    "clang-format -i",
)

PY_SUFFIXES = "py pyi".split()
CXX_SUFFIXES = "c cc cpp cxx c++ cu h hh hpp hxx h++ cuh ipp inl".split()
FORMATTERS = {s: BLACK for s in PY_SUFFIXES} | {s: CLANG_FORMAT for s in CXX_SUFFIXES}

DIRECTIVE = (
    "Blocked: this commit would record unformatted files. Per directive, Python must "
    "pass black and C/C++ must pass clang-format (LLVM style). Format the files, "
    "restage them, then commit."
)

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


def commit_contents(repo, all_flag, pathspec):
    """Return {path: bytes} for the formattable files this commit would record."""
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
        if formatter_for(path) is None:
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


def formatter_for(path):
    suffix = path.rsplit(".", 1)
    return FORMATTERS.get(suffix[1].lower()) if len(suffix) == 2 else None


def check(repo, formatter, path, content):
    """Return a complaint for content that the formatter would rewrite, else None."""
    argv = list(formatter.argv) + [path] + list(formatter.trailer)
    try:
        proc = subprocess.run(argv, input=content, cwd=repo, capture_output=True, check=False)
    except OSError:
        return f"{formatter.name} is not installed -- install it, do not skip the check"
    if proc.returncode == 0:
        return None
    detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    return detail[0] if detail else f"{formatter.name} would reformat it"


def violations(repo, contents):
    """Return [(path, formatter, complaint)] for every file failing its formatter."""
    found = []
    for path, content in sorted(contents.items()):
        formatter = formatter_for(path)
        complaint = check(repo, formatter, path, content)
        if complaint is not None:
            found.append((path, formatter, complaint))
    return found


def deny(found):
    """Render the deny payload naming the failures and the command that fixes them."""
    detail = "; ".join(f"{path}: {complaint}" for path, _, complaint in found)
    fixes = collections.defaultdict(list)
    for path, formatter, _ in found:
        fixes[formatter.fix].append(path)
    commands = " && ".join(f"{fix} {' '.join(paths)}" for fix, paths in fixes.items())
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"{DIRECTIVE}\nFailures: {detail}\nFix: {commands}",
        }
    }


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    cwd = previous = payload.get("cwd") or os.getcwd()

    found = []
    for segment in segments(payload.get("tool_input", {}).get("command", "")):
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
        found += violations(root, commit_contents(root, all_flag, pathspec))
    if found:
        print(json.dumps(deny(found)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
