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

# Wire the PreToolUse guard hooks without depending on the host settings: seed
# the writable settings.json from the host (or {} if absent) and merge in the
# hooks block baked into the image, deduping so re-runs stay idempotent.
SETTINGS_SEED="${HOME}/.claude/settings.json.seed"
SETTINGS="${HOME}/.claude/settings.json"
HOOK_SRC="/usr/local/share/claude-settings.json"
BASE='{}'
if [[ -f "${SETTINGS_SEED}" ]] && jq -e . "${SETTINGS_SEED}" >/dev/null 2>&1; then
    BASE="$(cat "${SETTINGS_SEED}")"
fi
printf '%s' "${BASE}" | jq --slurpfile s "${HOOK_SRC}" \
    '.hooks.PreToolUse = ((.hooks.PreToolUse // []) + ($s[0].hooks.PreToolUse // []) | unique_by(tojson))' \
    > "${SETTINGS}"

# /tmp is a persistent host mount; create a venv there once and activate it so
# session state (and the venv) survives container restarts.
VENV="/tmp/venv"
if [[ ! -d "${VENV}" ]]; then
    python3 -m venv "${VENV}"
fi
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

exec claude "$@"
