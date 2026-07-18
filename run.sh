#!/usr/bin/env bash
# Build (if needed) and run Claude Code interactively in a container with the
# host's identity, docker socket, scratch share, pip config/caches and ~/.ssh
# bind-mounted in. The ~/.claude credentials, settings and global CLAUDE.md are
# shared with the host, and ~/.claude.json is seeded read-only (copied to a
# writable path by the entrypoint) so the client recognises the existing
# install; session state (conversations, history, tasks) lives in the container
# and is discarded when it exits.
set -euo pipefail

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

# Always rebuild so the image tracks the latest claude and host identity.
docker build \
    --build-arg "USERNAME=$(id -un)" \
    --build-arg "UID=$(id -u)" \
    --build-arg "GID=$(id -g)" \
    --build-arg "DOCKER_GID=${DOCKER_GID}" \
    --build-arg "CACHEBUST=$(date +%s)" \
    -t "${IMAGE}" -f "$(dirname "$0")"/Dockerfile "$(dirname "$0")"

# The container and its Remote Control session share one name: <host>-<dir>.
NAME="$(hostname -s)-$(basename "$(pwd)")"

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

# Auto-mount any host paths matching these globs read-write to the same location
# inside the container, so tool config/state (e.g. ~/.ansible*) is shared.
AUTO_MOUNT_PATTERNS=("${HOME}/.ansible"*)
AUTO_MOUNTS=()
for path in "${AUTO_MOUNT_PATTERNS[@]}"; do
    [[ -e "${path}" ]] && AUTO_MOUNTS+=(-v "${path}:${path}")
done

exec docker run --rm -it \
    --name "${NAME}" \
    -v "${CONTAINER_TMP}:/tmp" \
    -v /scratch:/scratch \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /etc/pip.conf:/etc/pip.conf:ro \
    -v "${PIP_CACHE}:${HOME}/.cache/pip" \
    -v "${HOME}/.claude/.credentials.json:${HOME}/.claude/.credentials.json" \
    -v "${HOME}/.claude/CLAUDE.md:${HOME}/.claude/CLAUDE.md:ro" \
    "${SEED_MOUNT[@]}" \
    "${SETTINGS_MOUNT[@]}" \
    "${AUTO_MOUNTS[@]}" \
    -v "${HOME}/.ssh:${HOME}/.ssh:ro" \
    -v "${HOME}/.config/gh:${HOME}/.config/gh" \
    -v "${HOME}/.gitconfig:${HOME}/.gitconfig:ro" \
    -w "$(pwd)" \
    "${IMAGE}" "${RUN_ARGS[@]}"
