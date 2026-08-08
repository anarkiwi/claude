#!/usr/bin/env bash
# Host-specific config for defroster, which has an NVIDIA GPU. Sourced by
# run.sh, which pre-declares HOST_DOCKER_ARGS and BASE_IMAGE.
#
# --gpus needs only nvidia-container-toolkit on the host, not an "nvidia"
# entry in the daemon's runtimes: dockerd's built-in GPU device driver calls
# nvidia-container-runtime-hook itself, which injects the driver libraries,
# nvidia-smi and the /dev/nvidia* nodes at container start. Nothing to mount
# and nothing to chmod -- those nodes are injected world-readable/writable.
#
# The driver supplies libcuda; everything above it (nvcc, cuDNN, the CUDA
# runtime the wheels link against) has to come from the image, hence the base
# swap. -devel rather than -runtime so anything built in the container -- a
# CUDA extension, a numba kernel -- can compile against the headers. CUDA
# minor versions are forward compatible within a major release, so a 13.3
# toolkit runs against this host's 13.2 driver; that pairing is the thing to
# check first if a CUDA call fails at init.
# shellcheck disable=SC2034  # read by run.sh, which sources this file
BASE_IMAGE=nvidia/cuda:13.3.1-cudnn-devel-ubuntu24.04
HOST_DOCKER_ARGS+=(
    --gpus all
)
