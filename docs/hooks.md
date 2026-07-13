# Hooks

PreToolUse guards under `hooks/`. Each reads the Claude Code hook payload on
stdin and, on a violation in the *added* text of a write, emits a `deny`
decision on stdout; silence (exit 0, no output) allows the write.

Wiring:

- `settings.json` registers the guard under `hooks.PreToolUse` with a
  `Write|Edit|MultiEdit|NotebookEdit` matcher, referencing it by absolute path.
- The `Dockerfile` `COPY`s each guard to `/usr/local/bin/` so that path exists
  in the container. Guards run on the system `python3` with no third-party deps.
- The `Dockerfile` also bakes `settings.json` into the image; `entrypoint.sh`
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

## Testing

    cd hooks
    pytest -n auto --cov=min_comments --cov-fail-under=85

`black --check .` and `pylint` also run in CI (`.github/workflows/lint.yml`).
