# Hooks

PreToolUse guards under `hooks/`. Each reads the Claude Code hook payload on
stdin and, on a violation, emits a `deny` decision on stdout; silence (exit 0,
no output) allows the call.

Wiring:

- `settings.json` registers each guard under `hooks.PreToolUse`, referencing it
  by absolute path, with the matcher for the tools it inspects
  (`Write|Edit|MultiEdit|NotebookEdit` for write guards, `Bash` for
  command guards).
- The `Dockerfile.claude` `COPY`s each guard to `/usr/local/bin/` so that path exists
  in the container. Guards run on the system `python3` with no third-party deps.
- The `Dockerfile.claude` also bakes `settings.json` into the image; `entrypoint.sh`
  seeds the writable in-container `~/.claude/settings.json` from the host and
  merges this `hooks.PreToolUse` block in (deduped, idempotent). The guard is
  therefore self-wiring — it runs even when the host settings omit it.

## min_comments.py

Enforces the directive to minimize narrative comments in Python. Denies a write
when the added text contains:

- a docstring (module, class, or function) whose description exceeds one summary
  line plus 3 lines of elaboration (PEP 257), or
- a run of two or more consecutive full-line `#` comments.

Docstrings are located with `ast`; only the free-text description is counted —
Google- and NumPy-style sections (`Args`, `Returns`, `Raises`, `Parameters`,
…) and blank lines are excluded. Comment runs are found with `tokenize` (inline
trailing comments and comments separated by a blank line are allowed). Malformed
payloads or unparseable fragments fall through to *allow*. Non-Python paths are
ignored; `.py`, `.pyi` and `.ipynb` cells are scanned.

## format_check.py

Enforces the formatting CI checks, at commit time rather than after a push.
Matches `git commit` in the `Bash` command — through `&&` chains, a preceding
`cd`, and `git -C <dir>` — then reconstructs exactly what that commit would
record and denies it if any file fails its formatter:

| Suffix | Formatter |
| --- | --- |
| `.py`, `.pyi` | `black --check` |
| `.c`, `.cc`, `.cpp`, `.cxx`, `.c++`, `.cu`, `.h`, `.hh`, `.hpp`, `.hxx`, `.h++`, `.cuh`, `.ipp`, `.inl` | `clang-format --dry-run -Werror --style=file --fallback-style=LLVM` |

Content comes from the index, so a dirty worktree does not trigger a false
denial; `-a`/`--all` and a trailing pathspec switch the affected files to their
worktree content, matching what git would stage. Each file is piped to the
formatter under its own path (`--stdin-filename`/`--assume-filename`), so
`pyproject.toml` and `.clang-format` settings apply, falling back to LLVM style
where a project ships no `.clang-format`. The deny names each failure and the
command that fixes them all.

Both formatters are installed from apt in the image, and CI installs the same
packages so the two agree. A formatter missing from `PATH` is itself a denial —
per directive, install it rather than skip the check. Unparseable commands,
non-`Bash` calls, paths outside a repo, and unknown suffixes fall through to
*allow*.

## Testing

    cd hooks
    pytest -n auto --cov=min_comments --cov=format_check --cov-fail-under=85

`black --check .` and `pylint` also run in CI (`.github/workflows/lint.yml`).
