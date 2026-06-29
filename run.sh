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

# Always rebuild so the image tracks the latest claude and host identity.
docker build \
    --build-arg "USERNAME=$(id -un)" \
    --build-arg "UID=$(id -u)" \
    --build-arg "GID=$(id -g)" \
    --build-arg "DOCKER_GID=${DOCKER_GID}" \
    --build-arg "CACHEBUST=$(date +%s)" \
    -t "${IMAGE}" "$(dirname "$0")"

exec docker run --rm -it \
    -v /scratch:/scratch \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /etc/pip.conf:/etc/pip.conf:ro \
    -v "${PIP_CACHE}:${HOME}/.cache/pip" \
    -v "${HOME}/.claude/.credentials.json:${HOME}/.claude/.credentials.json" \
    -v "${HOME}/.claude/settings.json:${HOME}/.claude/settings.json:ro" \
    -v "${HOME}/.claude/CLAUDE.md:${HOME}/.claude/CLAUDE.md:ro" \
    "${SEED_MOUNT[@]}" \
    -v "${HOME}/.ssh:${HOME}/.ssh:ro" \
    -v "${HOME}/.config/gh:${HOME}/.config/gh" \
    -v "${HOME}/.gitconfig:${HOME}/.gitconfig:ro" \
    -w "$(pwd)" \
    "${IMAGE}" "$@"
