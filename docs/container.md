# Container

`run.sh` builds and runs Claude Code interactively in a container that adopts
the host's identity and shares its credentials, so bind-mounted paths keep
correct ownership and the client recognises the existing authenticated install.

## Identity

The image is built with `CONTAINER_USER`/`UID`/`GID` from the invoking host
user and `DOCKER_GID` from the host `docker` group, creating a matching
non-root user with passwordless sudo and membership of the docker group.
`USER` is set to the numeric `UID` alone (not `UID:GID`, and not the
username) — that keeps hadolint happy (DL3066) while still resolving
supplementary groups (like `docker`) the way a named `USER` would; adding an
explicit `:GID` suppresses that lookup and silently drops them. The native
`claude` binary installs into `~/.local` (outside the mounted `~/.claude`).
`CACHEBUST` is the latest available `claude` version (resolved by `run.sh`),
not a timestamp, so the ~300MB install layer only reruns — and only pays for
a fresh download — when a new release actually ships; an unchanged version
hits Docker's normal build cache.

## Mounts

- `~/.claude` credentials and global `CLAUDE.md` — shared with the host
  (`CLAUDE.md` read-only).
- `~/.claude.json` and `~/.claude/settings.json` — seeded read-only; the
  entrypoint copies each to a writable in-container path, so session writes stay
  ephemeral and never touch the host. For settings it also merges in the hooks
  block baked into the image (see [hooks.md](hooks.md)), so the guards are wired
  even when the host settings omit them.
- `/scratch`, the host docker socket, `/etc/pip.conf`, the pip cache, `~/.ssh`
  (ro, with `known_hosts` remounted read-write so new host keys persist),
  `~/.config/gh`, and `~/.gitconfig` (ro).
- `/tmp` — persisted per container name under `/scratch/tmp/<name>`; the
  entrypoint creates a venv there once and reuses it across restarts.

Session state (conversations, history, tasks) lives only in the container and
is discarded on exit. With no args, `run.sh` starts a `--remote-control`
session named `<host>-<dir>`.

## Host-specific config

`run.sh` sources `hosts/<hostname>.sh` if it exists, appending to a
`HOST_DOCKER_ARGS` array passed to `docker run`. This is how host-specific
extras (`--privileged`, device mounts) are opted in without changing the
default config. See `hosts/vek-x.sh`, which grants `--privileged` and mounts
`/dev/snd`, `/dev/video0`, `/dev/ttyACM0` and `/dev/bus/usb` for that host's
attached audio/camera/serial/USB peripherals.

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
