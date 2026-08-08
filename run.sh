#!/usr/bin/env bash
# Build and run Claude Code interactively in a container that *is* the invoking
# user: one of the shared "claude" (default) or "ansible" identities, whose
# UID/GID and home directory path carry into the container unchanged, so every
# bind-mounted path keeps correct ownership without any privilege escalation.
# The host's docker socket, /scratch, apt/pip proxy config and the identity's
# ~/.ssh, ~/.config/gh and ~/.gitconfig are mounted in.
#
# Credentials come from the identity's own ~/.claude and are the only host state
# mounted read-write, so a login inside the container persists -- see the CREDS
# block below for why each identity on each host keeps its own. Config
# (~/.claude.json, settings.json, CLAUDE.md) is mounted read-only and copied to
# writable paths by the entrypoint, so the client recognises the existing
# install; session state (conversations, history, memories) lives in the
# container and is discarded when it exits. /tmp is host-backed so it can be
# inspected from outside, but is emptied at startup rather than carried over.
set -euo pipefail

# Resolve this script's own directory so the build always targets the claude
# Dockerfile, regardless of the caller's working directory. dirname "$0" alone
# yields "." when $0 has no directory component, which would build whatever
# Dockerfile happens to sit in the caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The container identity is the invoking user, and only the shared identities
# qualify: they exist with the same UID/GID on every host, so SSH keys,
# credentials and file ownership follow the account rather than the machine.
# Running as anyone else would mount a $HOME the container user cannot own.
CONTAINER_USER="$(id -un)"
case "${CONTAINER_USER}" in
claude | ansible) ;;
*)
    echo "!! run.sh must run as claude (default) or ansible, not ${CONTAINER_USER}" >&2
    exit 1
    ;;
esac
CONTAINER_UID="$(id -u)"
# "sw" is the group shared by both identities, so what one writes stays
# writable by the other.
CONTAINER_GID="$(getent group sw | cut -d: -f3)"

IMAGE="${IMAGE:-claude-code:local}"
DOCKER_GID="$(getent group docker | cut -d: -f3)"

# Seed the host's top-level ~/.claude.json read-only so the client recognises
# the existing install (onboarding/account state); the entrypoint copies it to a
# writable in-container path, so session writes stay ephemeral. Only mount it if
# it exists, otherwise docker would create a stray directory on the host.
SEED_MOUNT=()
if [[ -f "${HOME}/.claude.json" ]]; then
    SEED_MOUNT=(-v "${HOME}/.claude.json:${HOME}/.claude.json.seed:ro")
fi

# Seed the host settings.json read-only; the entrypoint copies it to a writable
# path and merges the image's hooks block in, so session writes stay ephemeral.
SETTINGS_MOUNT=()
if [[ -f "${HOME}/.claude/settings.json" ]]; then
    SETTINGS_MOUNT=(-v "${HOME}/.claude/settings.json:${HOME}/.claude/settings.json.seed:ro")
fi

# The global CLAUDE.md is shared guidance rather than identity state, but is
# guarded like the seed mounts: an absent source would become a stray
# root-owned directory.
CLAUDE_MD_MOUNT=()
if [[ -e "${HOME}/.claude/CLAUDE.md" ]]; then
    CLAUDE_MD_MOUNT=(-v "${HOME}/.claude/CLAUDE.md:${HOME}/.claude/CLAUDE.md:ro")
fi

# The claude binary (~300MB) is cached on the shared /scratch mount, keyed by
# platform and version, and handed to the build as a named context rather than
# downloaded inside it. Docker's layer cache is per-daemon, so every host used
# to pay the full download for each new release; this cache is one filesystem
# the whole fleet shares, so the first host to see a release fetches it and the
# rest copy it over the LAN. A few older versions are kept to back the offline
# fallback below, and pruned past that.
CLAUDE_RELEASES=https://downloads.claude.ai/claude-code-releases
case "$(uname -m)" in
x86_64) CLAUDE_PLATFORM=linux-x64 ;;
aarch64) CLAUDE_PLATFORM=linux-arm64 ;;
*)
    echo "!! unsupported architecture $(uname -m)" >&2
    exit 1
    ;;
esac
CLAUDE_DIST=/scratch/tmp/claude-dist
CLAUDE_CACHE="${CLAUDE_DIST}/${CLAUDE_PLATFORM}"
# Cached releases to retain (see the prune below): enough that a host still on
# an older image, or one that has lost the version check, finds something to
# fall back to, without the cache growing by ~300MB a day forever.
CLAUDE_KEEP=5
for CACHE_DIR in "${CLAUDE_DIST}" "${CLAUDE_CACHE}"; do
    mkdir -p "${CACHE_DIR}"
    # Group-writable and setgid, so the version directories and lock file below
    # stay in the shared group whichever identity creates them. Applied every
    # run rather than only on create: a run that died between the mkdir and
    # here would otherwise leave a directory the other identity can never write
    # to. Failure is ignored because the case that causes it -- the directory
    # belongs to the other identity -- is also the case where it is already
    # correct.
    chgrp sw "${CACHE_DIR}" 2>/dev/null || true
    chmod g+ws "${CACHE_DIR}" 2>/dev/null || true
done

CLAUDE_VERSION="$(curl -fsSL --max-time 5 "${CLAUDE_RELEASES}/latest" 2>/dev/null || true)"
if [[ ! "${CLAUDE_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]]; then
    # Version check failed, or returned something that isn't a version (an
    # error page). Fall back to the newest release already cached, so an
    # unreachable network costs nothing at all rather than a rebuild. sort -V,
    # so 2.1.9 doesn't outrank 2.1.10.
    shopt -s nullglob
    CACHED=("${CLAUDE_CACHE}"/*/claude)
    shopt -u nullglob
    if [[ ${#CACHED[@]} -eq 0 ]]; then
        echo "!! ${CLAUDE_RELEASES} unreachable and ${CLAUDE_CACHE} is empty" >&2
        exit 1
    fi
    CLAUDE_VERSION="$(printf '%s\n' "${CACHED[@]%/claude}" | sed 's|.*/||' | sort -V | tail -1)"
    echo "?? version check failed, using cached claude ${CLAUDE_VERSION}" >&2
fi

if [[ ! -x "${CLAUDE_CACHE}/${CLAUDE_VERSION}/claude" ]]; then
    LOCK="${CLAUDE_DIST}/.lock"
    [[ -f "${LOCK}" ]] || install -m 0664 /dev/null "${LOCK}"
    # flock (NFSv4 has real locking) so hosts starting together fetch once
    # between them; the test is repeated inside because the winner of the race
    # populates the cache while the others wait on the lock.
    (
        flock 9
        if [[ ! -x "${CLAUDE_CACHE}/${CLAUDE_VERSION}/claude" ]]; then
            echo ">> fetching claude ${CLAUDE_VERSION} (${CLAUDE_PLATFORM})" >&2
            # Checksum from the release manifest, the same one install.sh
            # verifies against, extracted without jq since the host may not
            # have it. The ":{" in the key pattern keeps "linux-x64" from
            # matching the "linux-x64-musl" entry.
            SHA="$(curl -fsSL "${CLAUDE_RELEASES}/${CLAUDE_VERSION}/manifest.json" |
                tr -d ' \n' |
                grep -o "\"${CLAUDE_PLATFORM}\":{[^}]*}" |
                grep -o '"checksum":"[a-f0-9]\{64\}"' | cut -d'"' -f4)"
            if [[ ! "${SHA}" =~ ^[a-f0-9]{64}$ ]]; then
                echo "!! no ${CLAUDE_PLATFORM} checksum in the ${CLAUDE_VERSION} manifest" >&2
                exit 1
            fi
            # Staged in a dot-directory and renamed only once the checksum
            # verifies: a version directory is therefore never visible to
            # another host holding a partial or unverified binary, and the
            # dot prefix keeps a crashed run's leftovers out of the glob above.
            STAGE="${CLAUDE_CACHE}/.stage.$$"
            trap 'rm -rf "${STAGE}"' EXIT
            mkdir -p "${STAGE}"
            curl -fsSL --retry 3 -o "${STAGE}/claude" \
                "${CLAUDE_RELEASES}/${CLAUDE_VERSION}/${CLAUDE_PLATFORM}/claude"
            echo "${SHA}  ${STAGE}/claude" | sha256sum -c - >/dev/null
            chmod 0755 "${STAGE}/claude"
            # Group-writable so the prune below works whichever identity
            # fetched the version being removed.
            chmod 2775 "${STAGE}"
            mv "${STAGE}" "${CLAUDE_CACHE}/${CLAUDE_VERSION}"

            # Releases ship most days at ~300MB each, so the cache needs a
            # bound. Pruning here rather than every run means it costs nothing
            # except when a new version actually lands, and the lock held over
            # it keeps a prune from deleting a version another host is copying
            # into a build right now.
            shopt -s nullglob
            CACHED=("${CLAUDE_CACHE}"/*/claude)
            shopt -u nullglob
            printf '%s\n' "${CACHED[@]%/claude}" | sed 's|.*/||' | sort -V |
                head -n "-${CLAUDE_KEEP}" |
                while read -r stale; do
                    # Empty-name guard, paired with the :? below: an empty
                    # CACHED expands to one empty line, which would point this
                    # rm at the whole cache rather than at one version.
                    if [[ -n "${stale}" ]]; then
                        rm -rf "${CLAUDE_CACHE:?}/${stale}"
                    fi
                done
        fi
    ) 9>"${LOCK}"
fi

HOST="$(hostname -s)"
REPO_NAME="$(basename "$(pwd)")"

# The container and its Remote Control session share one name: <host>-<dir>.
NAME="${HOST}-${REPO_NAME}"

# Resource caps, overridable per host (see below): --pids-limit guards
# against runaway forks (relevant to the --init/zombie-reaping note further
# down); MEMORY_LIMIT defaults to all host RAM minus 1G of headroom for the
# host itself.
PIDS_LIMIT="${PIDS_LIMIT:-2048}"
HOST_MEM_BYTES="$(free -b | awk '/^Mem:/{print $2}')"
MEMORY_LIMIT="${MEMORY_LIMIT:-$(( (HOST_MEM_BYTES - 1073741824) / 1048576 ))m}"

# Base image, likewise overridable per host: a GPU host builds on an NVIDIA
# CUDA image so the toolkit is present alongside the driver docker injects.
BASE_IMAGE="${BASE_IMAGE:-ubuntu:24.04}"

# Host-specific docker flags (e.g. --privileged, --gpus) live in
# hosts/<hostname>.sh and are opt-in per host; the default config adds none.
# The same file can override BASE_IMAGE/PIDS_LIMIT/MEMORY_LIMIT or push more
# entries onto HOST_DOCKER_ARGS, keyed on REPO_NAME where an override should
# apply to one repo only. See hosts/vek-x.sh and hosts/defroster.sh.
HOST_DOCKER_ARGS=()
HOST_CONFIG="${SCRIPT_DIR}/hosts/${HOST}.sh"
if [[ -f "${HOST_CONFIG}" ]]; then
    # shellcheck disable=SC1090
    source "${HOST_CONFIG}"
fi

# Always rebuild so the image tracks the latest claude and container identity.
docker build \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "CONTAINER_USER=${CONTAINER_USER}" \
    --build-arg "UID=${CONTAINER_UID}" \
    --build-arg "GID=${CONTAINER_GID}" \
    --build-arg "DOCKER_GID=${DOCKER_GID}" \
    --build-arg "HOME_DIR=${HOME}" \
    --build-arg "CLAUDE_VERSION=${CLAUDE_VERSION}" \
    --build-context "claude-dist=${CLAUDE_CACHE}/${CLAUDE_VERSION}" \
    -t "${IMAGE}" -f "${SCRIPT_DIR}/Dockerfile.claude" "${SCRIPT_DIR}"

# Default to an interactive Remote Control session under NAME when no explicit
# claude args are given; passing any args overrides this default.
RUN_ARGS=("$@")
if [[ ${#RUN_ARGS[@]} -eq 0 ]]; then
    RUN_ARGS=(--remote-control "${NAME}")
fi

# Host-side mounts for the container's /tmp and venv, one dir per container
# name, created group-writable on first run. /tmp is on the host so the client's
# working files (scratchpads, task output) can be read without exec'ing into the
# container; the entrypoint empties it at startup, so a session never inherits
# the previous one's state, and the last session's files stay readable until the
# next run. The venv is mounted separately (/opt/venv) so that wipe leaves
# installed packages alone.
for CONTAINER_DIR in "/scratch/tmp/${NAME}" "/scratch/tmp/venv/${NAME}"; do
    if [[ ! -d "${CONTAINER_DIR}" ]]; then
        mkdir -p "${CONTAINER_DIR}"
        chgrp sw "${CONTAINER_DIR}"
        chmod g+w "${CONTAINER_DIR}"
    fi
done

# known_hosts must be writable so the container can record host keys it hasn't
# seen; ~/.ssh itself stays read-only to protect the private keys. Both must
# exist so docker bind-mounts them rather than materialising root-owned
# directories (see the CREDS block below).
install -d -m 0700 "${HOME}/.ssh"
touch "${HOME}/.ssh/known_hosts"

# Claude Code persists its OAuth login to ~/.claude/.credentials.json, which
# belongs to the identity: the OAuth refresh token rotates on refresh, so two
# containers sharing one file invalidate each other until the client gives up
# and clears it (see docs/container.md#credentials).
#
# The source must exist as a file BEFORE docker sees it: docker silently
# materialises a missing bind-mount source as a root-owned DIRECTORY, which the
# container can then never log in to. Same reasoning as the known_hosts touch
# above and the -f guards on the seed mounts.
CREDS="${HOME}/.claude/.credentials.json"
# Clean up exactly that failure mode if an earlier run left one behind.
# rmdir only succeeds on an empty directory, so a real credentials file (or a
# directory with anything in it) is never at risk here.
if [[ -d "${CREDS}" ]]; then
    rmdir "${CREDS}" 2>/dev/null || {
        echo "!! ${CREDS} is a non-empty directory -- inspect it by hand" >&2
        exit 1
    }
fi
if [[ ! -f "${CREDS}" ]]; then
    install -d -m 0700 "$(dirname "${CREDS}")"
    install -m 0600 /dev/null "${CREDS}"
fi

# Mount the host's apt proxy/cache config read-only, if present, so runtime
# apt-get installs (see entrypoint.sh) go through the same proxy as the host
# -- mirrors the pip.conf mount below for pip.
APT_PROXY_MOUNTS=()
while IFS= read -r -d '' path; do
    APT_PROXY_MOUNTS+=(-v "${path}:${path}:ro")
done < <(find /etc/apt/apt.conf.d -maxdepth 1 -iname '*proxy*' -print0 2>/dev/null)
if [[ -f /etc/apt/apt.conf ]]; then
    APT_PROXY_MOUNTS+=(-v /etc/apt/apt.conf:/etc/apt/apt.conf:ro)
fi

# --init runs Docker's built-in tini as the real PID 1 (entrypoint execs
# claude as its child, not PID 1 itself), so orphaned grandchildren -- e.g.
# workers left behind when claude kills a python multiprocessing pool -- get
# reaped instead of turning into zombies.
exec docker run --rm -it \
    --name "${NAME}" \
    --init \
    --pids-limit "${PIDS_LIMIT}" \
    --memory "${MEMORY_LIMIT}" \
    "${HOST_DOCKER_ARGS[@]}" \
    -v "/scratch/tmp/${NAME}:/tmp" \
    -v "/scratch/tmp/venv/${NAME}:/opt/venv" \
    -v /scratch:/scratch \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /etc/pip.conf:/etc/pip.conf:ro \
    "${APT_PROXY_MOUNTS[@]}" \
    "${CLAUDE_MD_MOUNT[@]}" \
    "${SEED_MOUNT[@]}" \
    "${SETTINGS_MOUNT[@]}" \
    -v "${CREDS}:${CREDS}" \
    -v "${HOME}/.ssh:${HOME}/.ssh:ro" \
    -v "${HOME}/.ssh/known_hosts:${HOME}/.ssh/known_hosts:rw" \
    -v "${HOME}/.config/gh:${HOME}/.config/gh" \
    -v "${HOME}/.gitconfig:${HOME}/.gitconfig:ro" \
    -w "$(pwd)" \
    "${IMAGE}" "${RUN_ARGS[@]}"
