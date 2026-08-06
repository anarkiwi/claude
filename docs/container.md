# Container

`run.sh` builds and runs Claude Code interactively in a container that shares
credentials with the host, so bind-mounted paths keep correct ownership and
the client recognises the existing authenticated install.

## Identity

The image is built with `CONTAINER_USER`/`UID`/`GID` defaulting to the shared
`claude` user and `sw` group -- looked up on the invoking host via `id -u
claude` and `getent group sw` -- not the invoking host user, and `DOCKER_GID`
from the host `docker` group, creating a matching non-root user with
passwordless sudo and membership of the docker group. Host config
(`hosts/<hostname>.sh`) can override the identity per repo: see
[Host-specific config](#host-specific-config).

`HOME_DIR` keeps the container user's home directory at the invoking host
user's `$HOME` (e.g. `/home/josh`), not `/home/${CONTAINER_USER}`, so
`$HOME`-relative bind mounts (`~/.claude`, `~/.gitconfig`, ...) still land in
the right place even though `CONTAINER_USER` names a different account.
`~/.ssh` is the exception: `run.sh` mounts it from the active identity's own
home (`SSH_HOME`, default the `claude` account's home; see
[Mounts](#mounts)), not the invoking user's, since SSH keys belong to the
identity, not the host account running `run.sh`. Other host-side config
(git, etc.) still comes from the invoking user's `$HOME` and migrates over
gradually.

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

- `~/.claude` credentials and global `CLAUDE.md` — shared with the host
  (`CLAUDE.md` read-only).
- `~/.claude.json` and `~/.claude/settings.json` — seeded read-only; the
  entrypoint copies each to a writable in-container path, so session writes stay
  ephemeral and never touch the host. For settings it also merges in the hooks
  block baked into the image (see [hooks.md](hooks.md)), so the guards are wired
  even when the host settings omit them.
- `/scratch`, the host docker socket, `/etc/pip.conf`, the pip cache,
  `~/.config/gh`, and `~/.gitconfig` (ro) — from the invoking host user's
  `$HOME`.
- `~/.ssh` (ro, with `known_hosts` remounted read-write so new host keys
  persist) — from `SSH_HOME`, the active container identity's own home (see
  [Identity](#identity)), not the invoking user's.
- `/tmp` — persisted per container name under `/scratch/tmp/<name>`; the
  entrypoint creates a venv there once and reuses it across restarts.

Session state (conversations, history, tasks) lives only in the container and
is discarded on exit. With no args, `run.sh` starts a `--remote-control`
session named `<host>-<dir>`.

## Host-specific config

`run.sh` sources `hosts/<hostname>.sh` if it exists, before building the
image, so it can append to the `HOST_DOCKER_ARGS` array passed to `docker
run`, override resource caps (`PIDS_LIMIT`/`MEMORY_LIMIT`), or override the
default identity (`CONTAINER_USER`/`CONTAINER_UID`/`CONTAINER_GID`/
`SSH_HOME`) — typically keyed on `REPO_NAME` (the working directory's
basename) so the override only applies to one repo. This is how
host-specific extras and identity exceptions are opted in without changing
the default config.

See `hosts/vek-x.sh`, which grants `--privileged` and mounts `/dev/snd`,
`/dev/video0`, `/dev/ttyACM0` and `/dev/bus/usb` for that host's attached
audio/camera/serial/USB peripherals, and runs as the `ansible` user's UID
and `~/.ssh` (instead of the default `claude` identity) when invoked from
the `finf-ansible` repo specifically.

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
