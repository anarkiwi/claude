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
)
