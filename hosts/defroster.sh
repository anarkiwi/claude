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
# This host's kernel driver (595.71.05) is a CUDA 13.2 driver, so a 13.3 image
# runs on the cuda-compat libraries it ships: ldconfig resolves libcuda.so.1 to
# /usr/local/cuda-13.3/compat ahead of the driver's own, and CUDA then reports
# 13.3. Verified working -- a kernel executes and cudnnCreate succeeds.
#
# NVIDIA_DISABLE_REQUIRE is what makes that reachable. The image declares
# NVIDIA_REQUIRE_CUDA "cuda>=13.3 ... driver>=595,driver<596"; the driver
# clause passes, but nvidia-container-cli evaluates cuda>=13.3 against the
# kernel driver rather than the compat libraries and hard-fails the container
# at init, before anything runs. Disabling the check is not a workaround for an
# incompatibility -- it is the documented escape hatch for the check testing
# the wrong thing.
#
# Dropping to 13.2.1-cudnn-devel-ubuntu24.04 would match the driver natively
# and need neither compat nor this override; the tradeoff is that cuda-compat
# forward compatibility is officially a datacenter-GPU feature, so on a GeForce
# it is working-but-unsupported. Revisit on the next driver update.
# shellcheck disable=SC2034  # read by run.sh, which sources this file
BASE_IMAGE=nvidia/cuda:13.3.1-cudnn-devel-ubuntu24.04
HOST_DOCKER_ARGS+=(
    --gpus all
    -e NVIDIA_DISABLE_REQUIRE=1
)
