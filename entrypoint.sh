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

# Seed the writable settings.json from the host (or {} if absent), then overlay
# the image's canonical settings, which win: that is what enables auto
# permission mode and the PreToolUse guards without depending on host settings.
# The allow and hook lists are unioned rather than replaced, so host entries
# survive, deduped so re-runs stay idempotent.
SETTINGS_SEED="${HOME}/.claude/settings.json.seed"
SETTINGS="${HOME}/.claude/settings.json"
IMAGE_SETTINGS="/usr/local/share/claude-settings.json"
BASE='{}'
if [[ -f "${SETTINGS_SEED}" ]] && jq -e . "${SETTINGS_SEED}" >/dev/null 2>&1; then
    BASE="$(cat "${SETTINGS_SEED}")"
fi
printf '%s' "${BASE}" | jq --slurpfile s "${IMAGE_SETTINGS}" '
    def union($a; $b): (($a // []) + ($b // [])) | unique_by(tojson);
    . as $host | ($host * $s[0])
    | .hooks.PreToolUse = union($host.hooks.PreToolUse; $s[0].hooks.PreToolUse)
    | .permissions.allow = union($host.permissions.allow; $s[0].permissions.allow)
' > "${SETTINGS}"

# /tmp is a host mount so the client's working files are visible from outside
# (see run.sh), which would otherwise carry a session's scratch into the next
# one: empty it here instead, so only the live session's state is ever present.
# mindepth 1 keeps the mount point itself; sudo covers root-owned leftovers
# (e.g. from the apt-get below), which would otherwise fail under set -e.
sudo find /tmp -mindepth 1 -maxdepth 1 -exec rm -rf {} +

# The venv is the one piece of host-persisted runtime state, on its own mount so
# the wipe above leaves it alone: create it once and activate it, so installed
# packages survive restarts.
VENV="/opt/venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
    python3 -m venv "${VENV}"
fi
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

# Install userspace tooling for whichever host devices are visible in the
# container (on a --privileged host config that is the host's whole /dev; see
# hosts/), then open up their permissions so using them doesn't need sudo.
# The apt lists cached at build time (Dockerfile.claude keeps them) make this
# apt-get install work without a prior apt-get update.
declare -A DEVICE_PACKAGES=(
    [/dev/snd]=alsa-utils
    [/dev/video0]=v4l-utils
    [/dev/bus/usb]=usbutils
)
DEVICE_PKGS=()
for dev in "${!DEVICE_PACKAGES[@]}"; do
    [[ -e "${dev}" ]] && DEVICE_PKGS+=("${DEVICE_PACKAGES[${dev}]}")
done
if [[ ${#DEVICE_PKGS[@]} -gt 0 ]]; then
    sudo apt-get install -y --no-install-recommends "${DEVICE_PKGS[@]}"
fi
# The tty entry is a glob: the CDC node's index follows enumeration order, so a
# device that was replugged (or that shares the host with another CDC gadget)
# comes back as ttyACM1 and up. A glob that matches nothing stays literal and
# is skipped by the -e test. The test is inside an if rather than trailing a
# && so a final non-existent entry cannot make the loop -- and, under set -e,
# this script -- exit non-zero on a host with none of these devices attached.
for dev in /dev/snd /dev/video0 /dev/ttyACM* /dev/bus/usb; do
    if [[ -e "${dev}" ]]; then
        sudo chmod -R a+rw "${dev}"
    fi
done

exec claude "$@"
