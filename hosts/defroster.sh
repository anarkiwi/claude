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
# CUDA extension, a numba kernel -- can compile against the headers.
#
# The image's CUDA version tracks this host's kernel driver (595.71.05, a CUDA
# 13.2 driver) rather than the newest release, and should be re-pinned on every
# driver update. A newer image runs only on the cuda-compat libraries it ships
# -- ldconfig resolves libcuda.so.1 to the image's compat directory ahead of
# the driver's own -- and while that does work, cuda-compat forward
# compatibility is officially a datacenter-GPU feature, so on a GeForce it is
# unsupported territory. It also needs NVIDIA_DISABLE_REQUIRE=1, because
# nvidia-container-cli evaluates the image's NVIDIA_REQUIRE_CUDA against the
# kernel driver rather than against those compat libraries, and hard-fails the
# container at init. Matching the versions here means neither is needed.
# shellcheck disable=SC2034  # read by run.sh, which sources this file
BASE_IMAGE=nvidia/cuda:13.2.1-cudnn-devel-ubuntu24.04
HOST_DOCKER_ARGS+=(
    --gpus all
)
