#!/usr/bin/env bash
# Build (if needed) and run Claude Code interactively in a container with the
# host's identity, docker socket, scratch share, pip config/caches and ~/.ssh
# bind-mounted in. Only the ~/.claude credentials, settings and global
# CLAUDE.md are shared with the host; session state (conversations, history,
# tasks) lives in the container and is discarded when it exits.
set -euo pipefail

IMAGE="${IMAGE:-claude-code:local}"
DOCKER_GID="$(getent group docker | cut -d: -f3)"
PIP_CACHE="${PIP_CACHE:-$HOME/.cache/pip}"

mkdir -p "${PIP_CACHE}"

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
    -v "${HOME}/.ssh:${HOME}/.ssh:ro" \
    -v "${HOME}/.config/gh:${HOME}/.config/gh" \
    -v "${HOME}/.gitconfig:${HOME}/.gitconfig:ro" \
    -w "$(pwd)" \
    "${IMAGE}" "$@"
