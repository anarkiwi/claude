# claude

Containerized, host-identity Claude Code and its PreToolUse guard hooks.

## Use

    ./run.sh                 # build image, start an interactive Remote Control session
    ./run.sh <claude args>   # override the default session

Runs the native `claude` binary as a UID/GID-matched non-root user, with the
host's docker socket, `/scratch`, `~/.ssh`, `~/.config/gh` and `~/.claude`
credentials/settings bind-mounted in. Session state stays in the container and
is discarded on exit. See [docs/container.md](docs/container.md).

## Hooks

`hooks/` holds PreToolUse guards wired into `settings.json` and installed into
the image by the `Dockerfile`. Each denies writes that violate a coding
directive; run their tests with `cd hooks && pytest -n auto`.

| Hook | Blocks |
| --- | --- |
| `min_comments.py` | Python docstrings over 3 lines, or consecutive comment lines |

See [docs/hooks.md](docs/hooks.md).
