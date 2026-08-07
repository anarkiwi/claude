# Container

`run.sh` builds and runs Claude Code interactively in a container that shares
credentials with the host, so bind-mounted paths keep correct ownership and
the client recognises the existing authenticated install.

## Identity

The container runs as the **invoking user**, which must be one of the shared
`claude` (default) or `ansible` identities — `run.sh` exits with an error for
anyone else. Those accounts exist with the same UID on every host, so SSH
keys, credentials and file ownership follow the account rather than the
machine, and no privilege escalation (`sudo`) is needed on the host side.

`CONTAINER_USER`/`UID` therefore come from `id -un`/`id -u`, `GID` from the
`sw` group shared by both identities, `DOCKER_GID` from the host `docker`
group, and `HOME_DIR` from `$HOME` — so the container home is the same path as
the host home and every `$HOME`-relative bind mount (`~/.claude`, `~/.ssh`,
`~/.gitconfig`, ...) lands in the right place with correct ownership. The
image's `ARG` defaults describe the `claude` identity; `run.sh` always passes
its own values.

To run a repo under the `ansible` identity, invoke `run.sh` as that user.

`USER` is set to `CONTAINER_USER` (the name), not a numeric UID: a numeric
value stops runc resolving supplementary groups (the `docker` group
disappears from `id -Gn`), and hadolint flags it either way (DL3066, ignored
in `.hadolint.yaml`) since it can't statically verify a build-arg value is
numeric. The native `claude` binary installs into `~/.local` (outside the
mounted `~/.claude`).
`CACHEBUST` is the latest available `claude` version (resolved by `run.sh`),
not a timestamp, so the ~300MB install layer only reruns — and only pays for
a fresh download — when a new release actually ships; an unchanged version
hits Docker's normal build cache.

## Mounts

All host-side sources are the invoking identity's `$HOME`.

- `~/.claude/.credentials.json` — each identity on each host keeps its own
  login; see [Credentials](#credentials) for why sharing one breaks.
- global `CLAUDE.md` (ro) — skipped when absent or a dangling symlink, so
  docker can't materialise a stray directory in its place.
- `~/.claude.json` and `~/.claude/settings.json` — seeded read-only; the
  entrypoint copies each to a writable in-container path, so session writes stay
  ephemeral and never touch the host. For settings it also merges in the hooks
  block baked into the image (see [hooks.md](hooks.md)), so the guards are wired
  even when the host settings omit them.
- `/scratch`, the host docker socket, `/etc/pip.conf`, `~/.config/gh` and
  `~/.gitconfig` (ro).
- `~/.ssh` (ro, with `known_hosts` remounted read-write so new host keys
  persist).
- `/tmp` — persisted per container name under `/scratch/tmp/<name>`; the
  entrypoint creates a venv there once and reuses it across restarts.

Session state (conversations, history, tasks) lives only in the container and
is discarded on exit. With no args, `run.sh` starts a `--remote-control`
session named `<host>-<dir>`.

## Credentials

Claude Code persists its OAuth login to `~/.claude/.credentials.json`. That
file belongs to the **identity**, so every identity on every host keeps its
own.

Sharing one credential across containers is not merely untidy — it destroys
it. The OAuth refresh token rotates on refresh, so two containers using the
same file invalidate each other in turn until the client gives up and clears
it. This is not hypothetical: it wiped every stored login on the fleet on
2026-08-06, when the identity switch landed while this mount still pointed at
the invoking user's `$HOME`. The tell at the time was having to hand-widen
josh's credentials (`chgrp sw` / `chmod g+r`) so containers running as a
different UID could read them — a correctly-scoped credential never needs
that.

`run.sh` pre-creates the file before `docker run` sees it. This matters: docker silently materialises a **missing** bind-mount source
as a *root-owned directory*, which the container can then never log in to.
The optional mounts (`.claude.json`, `settings.json`, `CLAUDE.md`) avoid this
by simply not mounting when absent; the credentials mount can't, since the
container needs somewhere to persist a fresh login, so it creates an empty
`0600` file instead. It also repairs the directory artifact if an earlier run
left one — via `rmdir`, which only succeeds when empty, so a real credentials
file is never at risk.

## Host-specific config

`run.sh` sources `hosts/<hostname>.sh` if it exists, before building the
image, so it can append to the `HOST_DOCKER_ARGS` array passed to `docker
run` or override resource caps (`PIDS_LIMIT`/`MEMORY_LIMIT`) — optionally
keyed on `REPO_NAME` (the working directory's basename) so an override
applies to one repo only. This is how host-specific extras are opted in
without changing the default config. Identity is not overridable here: it is
the invoking user (see [Identity](#identity)).

See `hosts/vek-x.sh`, which grants `--privileged` and mounts `/dev/snd`,
`/dev/video0`, `/dev/ttyACM0` and `/dev/bus/usb` for that host's attached
audio/camera/serial/USB peripherals.

## Device packages and permissions

`Dockerfile.claude` deliberately keeps its `apt-get update` lists (hadolint
DL3009 is ignored for this reason) so the entrypoint can `apt-get install`
device-support packages at container start without a network round-trip:
`alsa-utils` for `/dev/snd`, `v4l-utils` for `/dev/video0`, `usbutils` for
`/dev/bus/usb`. It also `chmod`s any bind-mounted device paths so the
container user can access them without `sudo`.

## Zombie processes

`docker run` is passed `--init`, which runs Docker's built-in `tini` as the
container's real PID 1. `entrypoint.sh` execs `claude` as tini's child rather
than as PID 1 itself, so if `claude` orphans children (e.g. killing a Python
multiprocessing pool without waiting for its workers), tini reaps them
instead of leaving zombies.

## Resource caps

`docker run` is passed `--pids-limit` (default `2048`, guarding against
runaway forks) and `--memory` (default: total host RAM minus 1G of headroom).
Both are plain shell variables (`PIDS_LIMIT`, `MEMORY_LIMIT`) set before
`hosts/<hostname>.sh` is sourced, so a host config can override either by
reassigning them, in addition to appending to `HOST_DOCKER_ARGS`.

## APT proxy

Build time: APT transport is rewritten to `http` so a caching proxy can serve
hits; `signed-by` keyrings still verify Release signatures.
`download.docker.com` stays on `https` (CloudFront redirects `http`), so it
is not proxy-cacheable.

Run time: `run.sh` bind-mounts the host's apt proxy/cache config read-only,
if present (`/etc/apt/apt.conf.d/*proxy*`, `/etc/apt/apt.conf`), the same way
it mounts `/etc/pip.conf` for pip, so the runtime `apt-get install` in
`entrypoint.sh` (see [Device packages](#device-packages-and-permissions))
goes through the same proxy as the host.
