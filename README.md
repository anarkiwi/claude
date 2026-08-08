# claude

Containerized Claude Code and its PreToolUse guard hooks.

## Use

    ./run.sh                 # build image, start an interactive Remote Control session
    ./run.sh <claude args>   # override the default session

Must be run as the shared `claude` (default) or `ansible` identity; any other
user is rejected. Runs the native `claude` binary as that user, with its
UID/GID, home path, `~/.ssh` and Claude Code credentials, plus the host's
docker socket, `/scratch`, `~/.config/gh` and `~/.claude` settings
bind-mounted in. Each identity on each host keeps its **own** login — sharing
one across containers invalidates it, see
[docs/container.md](docs/container.md#credentials). Credentials are the only
read-write host state; config is read-only, and session state (conversations,
history, memories) is discarded on exit. The container's `/tmp` is host-backed
at `/scratch/tmp/<name>` for live inspection, and emptied at startup. Permission mode defaults to
`auto` from the image `settings.json`, which wins over the host's. The
`claude` binary is fetched into a version-keyed cache under
`/scratch/tmp/claude-dist`, shared by every host, so a release is downloaded
once for the fleet. Host-specific extras (`--privileged`, `--gpus`, a CUDA
base image) are opt-in via `hosts/<hostname>.sh`.
See [docs/container.md](docs/container.md).

## Hooks

`hooks/` holds PreToolUse guards wired into `settings.json` and installed into
the image by the `Dockerfile.claude`. Each denies writes that violate a coding
directive; run their tests with `cd hooks && pytest -n auto`.

| Hook | Blocks |
| --- | --- |
| `min_comments.py` | Python docstring descriptions over 3 lines (PEP 257, sections excluded), or consecutive comment lines |

See [docs/hooks.md](docs/hooks.md).
