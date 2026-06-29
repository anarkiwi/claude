#!/usr/bin/env bash
# Seed the top-level Claude config from the host so the client recognises the
# existing authenticated install (onboarding state, OAuth account) instead of
# treating the container as a new installation. The seed is bind-mounted
# read-only; we copy it to a writable in-container path so session writes stay
# ephemeral and never touch the host file.
set -euo pipefail

SEED="${HOME}/.claude.json.seed"
DEST="${HOME}/.claude.json"

# Overwrite any config baked in at build time (install.sh writes a fresh
# ~/.claude.json) so the host's onboarding/account state always wins.
if [[ -f "${SEED}" ]]; then
    cp "${SEED}" "${DEST}"
fi

exec claude "$@"
