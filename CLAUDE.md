# Global coding directives

Shared user-level guidance, applied across all projects. Project-specific
`CLAUDE.md` files override or extend anything here.

## Working style
- Make the smallest change that solves the problem. Don't refactor unrelated code.
- Match the surrounding code: naming, structure, comment density, and idioms.
- When unsure between approaches, state a recommendation and proceed rather than
  surveying every option.
- Don't add comments that restate the code. Comment the non-obvious "why".
- Prefer editing existing files over creating new ones. Don't create docs,
  READMEs, or summary files unless asked.

## Before finishing
- Run the project's linter/formatter and tests if they exist; report real
  results, including failures. Don't claim something works unverified.
- Leave the tree clean: no stray debug prints, commented-out blocks, or scratch
  files.

## Git
- Branch before committing if on the default branch. Commit/push only when asked.
- Write concise, imperative commit messages explaining the why, not a file list.

## Languages
- Python: type hints on new code, prefer stdlib, follow PEP 8 / black formatting.
- Shell: prefer `set -euo pipefail`; quote variables; avoid bashisms in `sh`.

## Security & ops
- Never commit secrets, tokens, or credentials. Read config from env/secret stores.
- Validate and sanitize external input; fail closed.
