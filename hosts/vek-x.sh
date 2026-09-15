#!/usr/bin/env bash
# Host-specific docker flags for vek-x: full privileged access, plus the
# ansible tree this host keeps on the NAS. Sourced by run.sh, which
# pre-declares HOST_DOCKER_ARGS.
#
# Deliberately no -v /dev/... device mounts: --privileged lifts the device
# cgroup controller and populates the container's /dev with the host's device
# nodes, so the audio, camera, serial and USB nodes are all present at start.
#
# That /dev is a private tmpfs snapshot taken when the container starts. It is
# NOT the host's live devtmpfs and it does NOT track hotplug: a device plugged
# in afterwards never appears, and a replugged one goes stale -- libusb fails
# LIBUSB_ERROR_NO_DEVICE while lsusb on the host still lists the device. Plug
# the device in before starting the container, or nest one
# `docker run --privileged -v /dev:/dev ...` inside it for the command that
# has to follow a re-enumeration. Measured twice on the Boatswain bench, both
# times after the wrong belief here had cost a diagnosis.
#
# An explicit per-node mount is not the fix and is harmful: docker
# materialises a missing bind source as a root-owned DIRECTORY, so starting
# the container with (say) the Videomancer unplugged creates /dev/ttyACM0 as a
# directory *on the host*, which then permanently shadows the real character
# device until someone rmdir's it. Same failure mode the CREDS block in run.sh
# guards against, but on devtmpfs, where it also breaks the device for every
# other program on the host.
HOST_DOCKER_ARGS+=(
    --privileged
    -v /synology/ansible:/synology/ansible
)
