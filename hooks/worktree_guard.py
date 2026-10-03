#!/usr/bin/python3
"""PreToolUse guard denying a git worktree placed outside the session's repo.
The boundary is the main worktree of the repo holding CLAUDE_PROJECT_DIR; a
session started outside any repo is unconstrained."""

import json
import os
import sys

import commits

# Options of `git worktree add` and `move` consuming the following token.
VALUE_OPTS = frozenset({"-b", "-B", "--reason"})
# Index, among a subcommand's positionals, of the path that receives the worktree.
TARGET = {"add": 0, "move": 1}
SUGGEST = ".claude/worktrees"


def worktree_target(segment, cwd):
    """Return the absolute path a `git worktree add|move` segment creates, else None."""
    if not segment or os.path.basename(segment[0]) != "git":
        return None
    repo, i = commits.skip_git_options(segment, cwd)
    if segment[i : i + 1] != ["worktree"] or i + 1 >= len(segment):
        return None
    action, args = segment[i + 1], segment[i + 2 :]
    if action not in TARGET:
        return None
    positional, j, literal = [], 0, False
    while j < len(args):
        token, j = args[j], j + 1
        if literal or not token.startswith("-") or token == "-":
            positional.append(token)
        elif token == "--":
            literal = True
        elif token in VALUE_OPTS:
            j += 1
    index = TARGET[action]
    return commits.resolve(repo, positional[index]) if len(positional) > index else None


def contains(root, path):
    root, path = os.path.realpath(root), os.path.realpath(path)
    return os.path.commonpath((root, path)) == root


def violations(event, root):
    """Yield (command, target) for each worktree the payload would place outside root."""
    for cwd, segment in commits.commands(event):
        target = worktree_target(segment, cwd)
        if target is not None and not contains(root, target):
            yield " ".join(segment), target


def main():
    event = commits.payload()
    project = os.environ.get("CLAUDE_PROJECT_DIR") or (event or {}).get("cwd")
    listing = project and commits.git(project, "worktree", "list", "--porcelain", text=True)
    if not event or not listing:
        return 0
    root = listing.split("\n", 1)[0].removeprefix("worktree ")
    found = list(violations(event, root))
    if found:
        lines = "\n".join(f"  {cmd}  ->  {target}" for cmd, target in found)
        print(
            json.dumps(
                commits.deny(
                    f"Blocked: worktrees must live inside the session's repo ({root}).\n"
                    f"{lines}\nPlace it under {os.path.join(root, SUGGEST)}/<branch> "
                    "and keep that directory gitignored."
                )
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
