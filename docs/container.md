# Container

`run.sh` builds and runs Claude Code interactively in a container that adopts
the host's identity and shares its credentials, so bind-mounted paths keep
correct ownership and the client recognises the existing authenticated install.

## Identity

The image is built with `USERNAME`/`UID`/`GID` from the invoking host user and
`DOCKER_GID` from the host `docker` group, creating a matching non-root user
with passwordless sudo and membership of the docker group. The native `claude`
binary installs into `~/.local` (outside the mounted `~/.claude`). `CACHEBUST`
forces a fresh `claude` download on every build.

## Mounts

- `~/.claude` credentials and global `CLAUDE.md` — shared with the host
  (`CLAUDE.md` read-only).
- `~/.claude.json` and `~/.claude/settings.json` — seeded read-only; the
  entrypoint copies each to a writable in-container path, so session writes stay
  ephemeral and never touch the host. For settings it also merges in the hooks
  block baked into the image (see [hooks.md](hooks.md)), so the guards are wired
  even when the host settings omit them.
- `/scratch`, the host docker socket, `/etc/pip.conf`, the pip cache, `~/.ssh`
  (ro), `~/.config/gh`, and `~/.gitconfig` (ro).
- `/tmp` — persisted per container name under `/scratch/tmp/<name>`; the
  entrypoint creates a venv there once and reuses it across restarts.

Session state (conversations, history, tasks) lives only in the container and
is discarded on exit. With no args, `run.sh` starts a `--remote-control`
session named `<host>-<dir>`.

## APT proxy

APT transport is rewritten to `http` so a caching proxy can serve hits;
`signed-by` keyrings still verify Release signatures. `download.docker.com`
stays on `https` (CloudFront redirects `http`), so it is not proxy-cacheable.
