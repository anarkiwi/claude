#!/usr/bin/env bash
# Host-specific docker flags for vek-x: full privileged access plus the audio,
# camera, serial and USB peripherals attached to this host. Sourced by
# run.sh, which pre-declares HOST_DOCKER_ARGS.
HOST_DOCKER_ARGS+=(
    --privileged
    -v /dev/snd:/dev/snd
    -v /dev/video0:/dev/video0
    -v /dev/ttyACM0:/dev/ttyACM0
    -v /dev/bus/usb:/dev/bus/usb
    -v /synology/ansible:/synology/ansible
)

# The finf-ansible repo runs as the "ansible" user's UID (still "sw" group)
# and uses that user's own ~/.ssh, instead of the default "claude" identity,
# so files it writes and keys it uses match what ansible expects on this
# host. Every other repo on vek-x keeps the default.
# shellcheck disable=SC2034 # consumed by run.sh after it sources this file
if [[ "${REPO_NAME}" == "finf-ansible" ]]; then
    CONTAINER_USER="ansible"
    CONTAINER_UID="$(id -u ansible)"
    SSH_HOME="$(getent passwd ansible | cut -d: -f6)"
fi
