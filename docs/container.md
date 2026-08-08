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
numeric.

## Claude binary cache

The `claude` binary is ~300MB and a new release ships most days, so where it
comes from dominates build time. It is **not** downloaded inside the build:
Docker's layer cache is per-daemon, so every host paid for every release
separately. `run.sh` instead fetches it into a version-keyed cache on the
shared `/scratch` NFS mount and hands that directory to the build as a named
context:

    /scratch/tmp/claude-dist/<platform>/<version>/claude

The first host to see a release downloads it; the rest find it there and copy
it over the LAN. `--build-context claude-dist=<dir>` plus `COPY --from` means
the layer's cache key is the binary's *content*, so an unchanged version hits
the normal build cache with no `CACHEBUST` build-arg to guess at (hadolint
DL3022 is ignored inline: `--from` names a build context, not a stage).

The fetch verifies the SHA-256 from the release manifest — the same one
`install.sh` checks — and stages the download in a dot-directory, renaming it
into place only once it verifies, so another host can never see a version
directory holding a partial binary. Concurrent runs serialise on an `flock`
over the same mount. The result is laid out exactly as `install.sh` leaves it
(versioned file under `~/.local/share/claude/versions`, `~/.local/bin/claude`
symlinked to it), so the client's own update path still works.

If the version check is unreachable, `run.sh` falls back to the newest release
already cached rather than to a fresh download, so an offline host still
starts. That fallback is what the older versions are for, so the cache keeps
the newest five and prunes the rest — under the same lock, and only when a new
version actually lands, so a prune can neither race a fetch nor delete a
version another host is copying into a build.

## Mounts

All host-side sources are the invoking identity's `$HOME`.

- `~/.claude/.credentials.json` — each identity on each host keeps its own
  login; see [Credentials](#credentials) for why sharing one breaks.
- global `CLAUDE.md` (ro) — skipped when absent or a dangling symlink, so
  docker can't materialise a stray directory in its place.
- `~/.claude.json` and `~/.claude/settings.json` — seeded read-only; the
  entrypoint copies each to a writable in-container path, so session writes stay
  ephemeral and never touch the host. For settings it also overlays the image's
  canonical `settings.json` (see [Settings](#settings)).
- `/scratch`, the host docker socket, `/etc/pip.conf`, `~/.config/gh` and
  `~/.gitconfig` (ro).
- `~/.ssh` (ro, with `known_hosts` remounted read-write so new host keys
  persist).
- `/tmp` — host-backed per container name under `/scratch/tmp/<name>`, so the
  client's working files (scratchpads, task output) can be read live without
  `docker exec`. The entrypoint empties it at startup, so a session never
  inherits the previous one's scratch, and the last session's files stay
  readable until the next run.
- `/opt/venv` — persisted per container name under `/scratch/venv/<name>`; a
  separate mount so the `/tmp` wipe leaves it alone. The entrypoint creates the
  venv there once and reuses it across restarts.

`.credentials.json` is the only read-write host state, so a fresh login sticks;
config is read-only. Session state — conversations, history, memories — lives
only in the container and is discarded on exit. With no args, `run.sh` starts a
`--remote-control` session named `<host>-<dir>`.

## Settings

The image's `settings.json` is canonical: the entrypoint merges it over the
host seed and it wins on conflict, so every container gets `auto` permission
mode, the shared allow list and the PreToolUse guards (see
[hooks.md](hooks.md)) regardless of host settings. The `permissions.allow` and
`hooks.PreToolUse` lists are unioned rather than replaced, so host entries
survive; host-only keys (e.g. `theme`) pass through untouched.

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
run` or override `BASE_IMAGE` and the resource caps
(`PIDS_LIMIT`/`MEMORY_LIMIT`) — optionally keyed on `REPO_NAME` (the working
directory's basename) so an override applies to one repo only. This is how
host-specific extras are opted in without changing the default config.
Identity is not overridable here: it is the invoking user (see
[Identity](#identity)).

See `hosts/vek-x.sh`, which grants `--privileged` for that host's attached
audio/camera/serial/USB peripherals. `--privileged` bind-mounts the host's
entire `/dev`, so those nodes need no `-v` of their own — and must not get
one. Docker materialises a missing bind source as a root-owned *directory*,
so mounting `-v /dev/ttyACM0:/dev/ttyACM0` while that device happens to be
unplugged creates a directory at `/dev/ttyACM0` **on the host's devtmpfs**,
which then shadows the real character device for every program on the host,
not just the container, until it is `rmdir`'d and the device re-enumerated.
Mounting host `/dev` wholesale via `--privileged` also tracks hotplug, which
a per-node bind mount cannot: it pins one inode, so a replugged device leaves
the container holding a stale node.

## GPU

`hosts/defroster.sh` is the GPU case: it adds `--gpus all` and swaps
`BASE_IMAGE` to `nvidia/cuda:<version>-cudnn-devel-ubuntu24.04`. The two go
together because they supply different halves. `--gpus` needs only
`nvidia-container-toolkit` on the host — *not* an `nvidia` entry in the
daemon's runtimes, which is why `docker info` listing only `runc` is not a
problem: dockerd's built-in GPU device driver calls
`nvidia-container-runtime-hook` itself, injecting the driver libraries,
`nvidia-smi` and the `/dev/nvidia*` nodes at container start. Nothing to mount
and nothing to `chmod` — unlike the peripherals above, those nodes arrive
world-accessible.

What the driver injection does *not* supply is anything above `libcuda`:
`nvcc`, the CUDA headers, cuDNN, the runtime that GPU wheels link against.
That is what the base image swap is for, `-devel` rather than `-runtime` so
code can be compiled in the container, not just run.

The image's CUDA version and the host driver's need not match, but how they
fail to match matters. defroster's driver (595.71.05) is a CUDA 13.2 driver
running a 13.3 image, which works because the image ships `cuda-compat`:
`ldconfig` resolves `libcuda.so.1` to `/usr/local/cuda-13.3/compat` ahead of
the driver's own, and CUDA then reports 13.3 (verified — a kernel executes and
`cudnnCreate` succeeds).

Reaching that requires `-e NVIDIA_DISABLE_REQUIRE=1`. The image declares
`NVIDIA_REQUIRE_CUDA "cuda>=13.3 ... driver>=595,driver<596"`; the driver
clause passes, but `nvidia-container-cli` evaluates `cuda>=13.3` against the
kernel driver rather than the compat libraries, and hard-fails the container at
init with `unsatisfied condition: cuda>=13.3` before anything runs. The
override is the documented escape hatch for a check testing the wrong thing,
not a way to paper over a genuine incompatibility.

Matching the image to the driver (`13.2.1-cudnn-devel-ubuntu24.04`) needs
neither compat nor the override. The tradeoff is that `cuda-compat` forward
compatibility is officially a datacenter-GPU feature, so on a GeForce it is
working-but-unsupported. Either way, a container that dies at init with an
`unsatisfied condition` is this check, and a CUDA call that fails later is the
compat pairing.

Everything else in `Dockerfile.claude` is plain apt-on-noble and builds
unchanged on the CUDA image, which is itself derived from `ubuntu:24.04`.

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
