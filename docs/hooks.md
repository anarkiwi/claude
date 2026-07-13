# Hooks

PreToolUse guards under `hooks/`. Each reads the Claude Code hook payload on
stdin and, on a violation in the *added* text of a write, emits a `deny`
decision on stdout; silence (exit 0, no output) allows the write.

Wiring:

- `settings.json` registers the guard under `hooks.PreToolUse` with a
  `Write|Edit|MultiEdit|NotebookEdit` matcher, referencing it by absolute path.
- The `Dockerfile` `COPY`s each guard to `/usr/local/bin/` so that path exists
  in the container. Guards run on the system `python3` with no third-party deps.

## min_comments.py

Enforces the directive to minimize narrative comments in Python. Denies a write
when the added text contains:

- a docstring (module, class, or function) spanning more than 3 source lines, or
- a run of two or more consecutive full-line `#` comments.

Docstrings are located with `ast`; comment runs with `tokenize` (inline
trailing comments and comments separated by a blank line are allowed). Malformed
payloads or unparseable fragments fall through to *allow*. Non-Python paths are
ignored; `.py`, `.pyi` and `.ipynb` cells are scanned.

## Testing

    cd hooks
    pytest -n auto --cov=min_comments --cov-fail-under=85

`black --check .` and `pylint` also run in CI (`.github/workflows/lint.yml`).
