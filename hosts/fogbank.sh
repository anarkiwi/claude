#!/usr/bin/env bash
# Host-specific docker flags for fogbank: full privileged access, so the
# Boatswain bench rig's FT232H and its USB NICs are reachable here as they are
# on vek-x. Sourced by run.sh, which pre-declares HOST_DOCKER_ARGS.
#
# hosts/vek-x.sh carries the long form of why this is --privileged rather than
# per-node -v /dev/... mounts. In short: --privileged lifts the device cgroup
# and populates /dev at container start, but that /dev is a private tmpfs
# snapshot rather than the host's live devtmpfs, so USB nodes do NOT track
# hotplug -- plug the board in first, or nest a -v /dev:/dev run. An explicit
# mount of a missing node is worse: docker materialises it as a root-owned
# directory on the host, which then shadows the real character device for
# every program on that host.
#
# No /synology mount: that tree is vek-x's.
HOST_DOCKER_ARGS+=(
    --privileged
)
