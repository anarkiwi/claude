#!/usr/bin/python3
"""PreToolUse guard denying git commits whose content fails a required formatter.
Checks the bytes the commit would record -- index, worktree or pathspec -- so a
style failure surfaces here rather than in CI."""

import collections
import json
import subprocess
import sys

import commits

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


def reason(found):
    """Name each failure and the command that fixes them all."""
    detail = "; ".join(f"{path}: {complaint}" for path, _, complaint in found)
    fixes = collections.defaultdict(list)
    for path, formatter, _ in found:
        fixes[formatter.fix].append(path)
    commands = " && ".join(f"{fix} {' '.join(paths)}" for fix, paths in fixes.items())
    return f"{DIRECTIVE}\nFailures: {detail}\nFix: {commands}"


def formattable(path):
    return formatter_for(path) is not None


def main():
    found = []
    for root, contents in commits.committed(commits.payload(), formattable):
        found += violations(root, contents)
    if found:
        print(json.dumps(commits.deny(reason(found))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
