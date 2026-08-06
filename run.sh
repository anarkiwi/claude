#!/usr/bin/env bash
# Build (if needed) and run Claude Code interactively in a container. The
# container runs as a shared "claude" identity (UID/GID, ~/.ssh and its
# Claude Code credentials, host-overridable per repo; see hosts/) rather than
# the invoking host user, with the host's docker socket, scratch share and
# pip config/caches also bind-mounted in.
#
# The OAuth credentials come from the *identity's* home (SSH_HOME), so each
# identity on each host keeps its own login -- see the CREDS block below for
# why sharing one is actively harmful. Settings and the global CLAUDE.md are
# still shared with the invoking user, and ~/.claude.json is seeded read-only
# (copied to a writable path by the entrypoint) so the client recognises the
# existing install; session state (conversations, history, tasks) lives in
# the container and is discarded when it exits.
set -euo pipefail

# Resolve this script's own directory so the build always targets the claude
# Dockerfile, regardless of the caller's working directory. dirname "$0" alone
# yields "." when $0 has no directory component, which would build whatever
# Dockerfile happens to sit in the caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE="${IMAGE:-claude-code:local}"
DOCKER_GID="$(getent group docker | cut -d: -f3)"
PIP_CACHE="${PIP_CACHE:-$HOME/.cache/pip}"

mkdir -p "${PIP_CACHE}"

# Seed the host's top-level ~/.claude.json read-only so the client recognises
# the existing install (onboarding/account state); the entrypoint copies it to a
# writable in-container path, so session writes stay ephemeral. Only mount it if
# it exists, otherwise docker would create a stray directory on the host.
SEED_MOUNT=()
if [[ -f "${HOME}/.claude.json" ]]; then
    SEED_MOUNT=(-v "${HOME}/.claude.json:${HOME}/.claude.json.seed:ro")
fi

# Seed the host settings.json read-only; the entrypoint copies it to a writable
# path and merges the image's hooks block in, so session writes stay ephemeral.
SETTINGS_MOUNT=()
if [[ -f "${HOME}/.claude/settings.json" ]]; then
    SETTINGS_MOUNT=(-v "${HOME}/.claude/settings.json:${HOME}/.claude/settings.json.seed:ro")
fi

# CACHEBUST is the latest available claude version, not a timestamp: the
# install layer (a ~300MB download, minutes on a throttled link) only
# reruns when a new release actually ships, since Docker reuses its cached
# layer for an unchanged build-arg value. Falls back to a once-a-day value
# if the version check itself fails, so a transient network blip costs at
# most one rebuild rather than none.
CACHEBUST="$(curl -fsSL --max-time 5 https://downloads.claude.ai/claude-code-releases/latest 2>/dev/null || date +%Y%m%d)"

HOST="$(hostname -s)"
REPO_NAME="$(basename "$(pwd)")"

# The container and its Remote Control session share one name: <host>-<dir>.
NAME="${HOST}-${REPO_NAME}"

# Container identity defaults to the shared "claude" user/UID and "sw" group,
# not the invoking host user, so the same identity is portable across hosts.
# hosts/<hostname>.sh (sourced below) can override this per repo -- e.g.
# hosts/vek-x.sh switches to the "ansible" user's UID (and matching ~/.ssh),
# but only for the finf-ansible repo (REPO_NAME). The container's home
# directory still lands at this host user's $HOME (see HOME_DIR build-arg
# below), so the mounts further down keep working; other host-side config
# (git, etc.) migrates off the invoking user gradually -- ~/.ssh is the
# first to move.
CONTAINER_USER="claude"
CONTAINER_UID="$(id -u claude)"
CONTAINER_GID="$(getent group sw | cut -d: -f3)"

# ~/.ssh is sourced from the same account as the container identity above
# (default: "claude"), not the invoking user's -- each identity keeps its
# own SSH keys, shared across hosts and repos that use that identity.
# hosts/<hostname>.sh can override this in lockstep with CONTAINER_USER.
SSH_HOME="$(getent passwd "${CONTAINER_USER}" | cut -d: -f6)"

# Resource caps, overridable per host (see below): --pids-limit guards
# against runaway forks (relevant to the --init/zombie-reaping note further
# down); MEMORY_LIMIT defaults to all host RAM minus 1G of headroom for the
# host itself.
PIDS_LIMIT="${PIDS_LIMIT:-2048}"
HOST_MEM_BYTES="$(free -b | awk '/^Mem:/{print $2}')"
MEMORY_LIMIT="${MEMORY_LIMIT:-$(( (HOST_MEM_BYTES - 1073741824) / 1048576 ))m}"

# Host-specific docker flags (e.g. --privileged, device mounts) live in
# hosts/<hostname>.sh and are opt-in per host; the default config mounts no
# devices. The same file can override PIDS_LIMIT/MEMORY_LIMIT or the
# CONTAINER_USER/CONTAINER_UID/CONTAINER_GID identity above (e.g. per
# REPO_NAME), or push more entries onto HOST_DOCKER_ARGS. Sourced before the
# build so an identity override takes effect in the image. See
# hosts/vek-x.sh for an example.
HOST_DOCKER_ARGS=()
HOST_CONFIG="${SCRIPT_DIR}/hosts/${HOST}.sh"
if [[ -f "${HOST_CONFIG}" ]]; then
    # shellcheck disable=SC1090
    source "${HOST_CONFIG}"
fi

# Always rebuild so the image tracks the latest claude and container identity.
docker build \
    --build-arg "CONTAINER_USER=${CONTAINER_USER}" \
    --build-arg "UID=${CONTAINER_UID}" \
    --build-arg "GID=${CONTAINER_GID}" \
    --build-arg "DOCKER_GID=${DOCKER_GID}" \
    --build-arg "HOME_DIR=${HOME}" \
    --build-arg "CACHEBUST=${CACHEBUST}" \
    -t "${IMAGE}" -f "${SCRIPT_DIR}/Dockerfile.claude" "${SCRIPT_DIR}"

# Default to an interactive Remote Control session under NAME when no explicit
# claude args are given; passing any args overrides this default.
RUN_ARGS=("$@")
if [[ ${#RUN_ARGS[@]} -eq 0 ]]; then
    RUN_ARGS=(--remote-control "${NAME}")
fi

# Persist the container's /tmp on the host, one dir per container name. Created
# on first run; reused (left intact) on later runs so the venv/session survive.
CONTAINER_TMP="/scratch/tmp/${NAME}"
mkdir -p "${CONTAINER_TMP}"
chgrp sw "${CONTAINER_TMP}"
chmod g+w "${CONTAINER_TMP}"

# Auto-mount any host paths matching these globs read-write to the same location
# inside the container, so tool config/state (e.g. ~/.ansible*) is shared.
AUTO_MOUNT_PATTERNS=("${HOME}/.ansible"*)
AUTO_MOUNTS=()
for path in "${AUTO_MOUNT_PATTERNS[@]}"; do
    [[ -e "${path}" ]] && AUTO_MOUNTS+=(-v "${path}:${path}")
done

# known_hosts must be writable so the container can record host keys it hasn't
# seen; ~/.ssh itself stays read-only to protect the private keys. Ensure the
# file exists so docker bind-mounts a file (not a fresh directory) over the
# read-only parent.
SSH_OWNER="$(stat -c '%U' "${SSH_HOME}")"
sudo -u "${SSH_OWNER}" touch "${SSH_HOME}/.ssh/known_hosts"

# Claude Code persists its OAuth login to ~/.claude/.credentials.json. That
# file belongs to the *identity* (SSH_HOME), not the invoking host user, and
# every identity on every host keeps its own. Sharing one across containers
# is not merely untidy: the OAuth refresh token ROTATES on refresh, so two
# containers using the same credential invalidate each other until the client
# gives up and clears the file. That is how every stored login on the fleet
# was lost (2026-08-06) -- the identity switch landed while this mount still
# pointed at the invoking user's $HOME, so all six containers shared one
# credential and the host user had to hand-widen it (chgrp sw / chmod g+r)
# just to let the container read a file it could not legitimately own.
#
# The source must exist, as a file owned by the identity, BEFORE docker sees
# it: docker silently materialises a missing bind-mount source as a
# root-owned DIRECTORY, which the container can then never log in to. Same
# reasoning as the known_hosts touch above and the -f guards on the seed
# mounts further up.
CREDS="${SSH_HOME}/.claude/.credentials.json"
# Clean up exactly that failure mode if an earlier run left one behind.
# rmdir only succeeds on an empty directory, so a real credentials file (or a
# directory with anything in it) is never at risk here.
if [[ -d "${CREDS}" ]]; then
    sudo rmdir "${CREDS}" 2>/dev/null || {
        echo "!! ${CREDS} is a non-empty directory -- inspect it by hand" >&2
        exit 1
    }
fi
if [[ ! -f "${CREDS}" ]]; then
    sudo -u "${SSH_OWNER}" install -d -m 0700 "$(dirname "${CREDS}")"
    sudo -u "${SSH_OWNER}" install -m 0600 /dev/null "${CREDS}"
fi

# The global CLAUDE.md is shared, non-secret guidance rather than identity
# state, so it still comes from the invoking user's home. Guarded like the
# seed mounts: an absent source would become a stray root-owned directory.
CLAUDE_MD_MOUNT=()
if [[ -e "${HOME}/.claude/CLAUDE.md" ]]; then
    CLAUDE_MD_MOUNT=(-v "${HOME}/.claude/CLAUDE.md:${HOME}/.claude/CLAUDE.md:ro")
fi

# Mount the host's apt proxy/cache config read-only, if present, so runtime
# apt-get installs (see entrypoint.sh) go through the same proxy as the host
# -- mirrors the pip.conf mount below for pip.
APT_PROXY_MOUNTS=()
while IFS= read -r -d '' path; do
    APT_PROXY_MOUNTS+=(-v "${path}:${path}:ro")
done < <(find /etc/apt/apt.conf.d -maxdepth 1 -iname '*proxy*' -print0 2>/dev/null)
if [[ -f /etc/apt/apt.conf ]]; then
    APT_PROXY_MOUNTS+=(-v /etc/apt/apt.conf:/etc/apt/apt.conf:ro)
fi

# --init runs Docker's built-in tini as the real PID 1 (entrypoint execs
# claude as its child, not PID 1 itself), so orphaned grandchildren -- e.g.
# workers left behind when claude kills a python multiprocessing pool -- get
# reaped instead of turning into zombies.
exec docker run --rm -it \
    --name "${NAME}" \
    --init \
    --pids-limit "${PIDS_LIMIT}" \
    --memory "${MEMORY_LIMIT}" \
    "${HOST_DOCKER_ARGS[@]}" \
    -v "${CONTAINER_TMP}:/tmp" \
    -v /scratch:/scratch \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /etc/pip.conf:/etc/pip.conf:ro \
    "${APT_PROXY_MOUNTS[@]}" \
    -v "${CREDS}:${HOME}/.claude/.credentials.json" \
    "${CLAUDE_MD_MOUNT[@]}" \
    "${SEED_MOUNT[@]}" \
    "${SETTINGS_MOUNT[@]}" \
    "${AUTO_MOUNTS[@]}" \
    -v "${SSH_HOME}/.ssh:${HOME}/.ssh:ro" \
    -v "${SSH_HOME}/.ssh/known_hosts:${HOME}/.ssh/known_hosts:rw" \
    -v "${HOME}/.config/gh:${HOME}/.config/gh" \
    -v "${HOME}/.gitconfig:${HOME}/.gitconfig:ro" \
    -w "$(pwd)" \
    "${IMAGE}" "${RUN_ARGS[@]}"
