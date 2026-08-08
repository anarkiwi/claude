#!/usr/bin/env bash
# Host-specific docker flags for vek-x: full privileged access, plus the
# ansible tree this host keeps on the NAS. Sourced by run.sh, which
# pre-declares HOST_DOCKER_ARGS.
#
# Deliberately no -v /dev/... device mounts: --privileged already bind-mounts
# the host's whole /dev, so the audio, camera, serial and USB nodes are
# already present and, being the host's live devtmpfs, they track hotplug --
# a device plugged in after startup appears, and a replugged one keeps
# working. An explicit per-node mount is not just redundant but harmful:
# docker materialises a missing bind source as a root-owned DIRECTORY, so
# starting the container with (say) the Videomancer unplugged creates
# /dev/ttyACM0 as a directory *on the host*, which then permanently shadows
# the real character device until someone rmdir's it. Same failure mode the
# CREDS block in run.sh guards against, but on devtmpfs, where it also
# breaks the device for every other program on the host.
HOST_DOCKER_ARGS+=(
    --privileged
    -v /synology/ansible:/synology/ansible
)
