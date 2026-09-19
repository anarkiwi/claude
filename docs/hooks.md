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
  in the container. Guards run on the system `python3` -- pinned by shebang, so
  the session's activated venv does not shadow it -- and `pygments`, their only
  third-party dependency, is installed from apt as `python3-pygments`.
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

## durable_comments.py

Enforces the directive that a comment states what the code does and the
invariant behind it, never a measurement or the story of how the code got here.
Matches `git commit` in the `Bash` command with the same parsing as
`format_check.py` (see [commits.py](#commitspy)), reconstructs the bytes that
commit would record, and denies it when a comment or docstring carries:

- a measured quantity — a number with a time, size, rate, power or throughput
  unit (`30ms`, `4 GiB`, `900 Mbps`),
- a speedup or ratio beside what it compares (`3x faster`, `4 times slower`);
  a bare `1x` names a standard, so it passes,
- a percentage claim (`40% faster`, `reduced by 10%`),
- a date,
- the story of an earlier version (`used to return`, `the old design`,
  `previously ... now`); the purpose sense of `used to` passes,
- a note about the author's process (`turns out`, `after trying`, `for now`),
- a number beside a reporting word (`measured`, `benchmark`, `latency`, `p99`).

Comments and docstrings are located by lexing each file with `pygments`, so
every language it recognises is covered. Preprocessor lines and shebangs are
code it happens to tag as comment and are skipped, as are prose suffixes
(`.md`, `.rst`, `.txt`) and any file it has no lexer for.

Spans that name something rather than measure it are blanked before the rules
run: backticked symbols, URLs, `#123` issue references, version numbers and
standards references (`RFC 2119`, `ISO 8601`). A backticked span is kept when it
hides a quantity, so quoting does not launder one, and a licence or copyright
header is exempt in full. The deny names each comment, what it records and
where to move it — a measurement to docs, a change story to the commit message.

The same code surveys paths given as arguments, which is how CI checks the
tracked tree (`.github/workflows/lint.yml`):

    python3 hooks/durable_comments.py $(git ls-files)           # exit 1 on a finding
    python3 hooks/durable_comments.py $(git ls-files) --list    # report, exit 0

## commits.py

Shared parsing rather than a guard of its own: both commit-time guards import
it. It walks the `Bash` command through `cd`, `git -C` and `&&` chains, works
out which files each `git commit` in it would record — index content, or
worktree content under `-a`/`--all` and a trailing pathspec — and returns them
as bytes, along with the `deny` payload the guards emit. The image `COPY`s it to
`/usr/local/bin/commits.py`, beside the guards and keeping its module name: that
directory is the script directory, so it is on `sys.path` when a guard runs.

## Testing

    cd hooks
    pytest -n auto --cov=min_comments --cov=format_check --cov=commits \
        --cov=durable_comments --cov-fail-under=85

`black --check .` and `pylint` also run in CI (`.github/workflows/lint.yml`).
