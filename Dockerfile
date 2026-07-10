# Claude Code interactive container.
# Runs the native claude binary as a non-root user whose UID/GID match the
# host, so bind-mounted ~/.ssh and /scratch keep correct ownership. The host's
# ~/.claude credentials, settings and global CLAUDE.md are bind-mounted in, and
# ~/.claude.json is seeded (copied, not mounted) by the entrypoint so the client
# recognises the existing authenticated install; all session state
# (conversations, history, tasks) stays in the container and is discarded on
# exit. Includes the docker CLI (no daemon) for driving the host socket.
FROM ubuntu:24.04

ARG USERNAME=josh
ARG UID=1000
ARG GID=1000
# Must match the host's docker group so the mounted socket is writable.
ARG DOCKER_GID=998

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Use http for apt transport so a caching proxy can serve hits; the signed-by
# keyrings below still verify Release signatures, so packages stay authenticated.
RUN sed -i 's|https://|http://|g' \
        /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git openssh-client \
        ripgrep less jq python3 python3-pip python3-venv python3-dev \
        build-essential sudo \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI only (talks to the bind-mounted host daemon socket).
# Stays on https: download.docker.com (CloudFront) 301-redirects http to https,
# so a caching proxy can't serve hits here regardless; http just adds a redirect.
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI (uses the bind-mounted ~/.config/gh credentials).
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] http://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Create user/group matching the host. Reuse the GID if it already exists.
# Ubuntu 24.04 ships a default "ubuntu" user at UID/GID 1000; remove it first
# so it doesn't collide with the host-matched UID/GID below.
RUN userdel -r ubuntu 2>/dev/null || true; \
    if ! getent group "${GID}" >/dev/null; then groupadd -g "${GID}" "${USERNAME}"; fi \
    && useradd -l -m -u "${UID}" -g "${GID}" -s /bin/bash "${USERNAME}" \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/"${USERNAME}" \
    && chmod 0440 /etc/sudoers.d/"${USERNAME}" \
    && install -d -o "${UID}" -g "${GID}" -m 0700 "/home/${USERNAME}/.claude"

# docker group with the host's GID, so the user can use the mounted socket.
RUN if getent group "${DOCKER_GID}" >/dev/null; then \
        usermod -aG "$(getent group "${DOCKER_GID}" | cut -d: -f1)" "${USERNAME}"; \
    else \
        groupadd -g "${DOCKER_GID}" docker && usermod -aG docker "${USERNAME}"; \
    fi

USER ${USERNAME}
WORKDIR /scratch/anarkiwi/infra/claude

ENV PATH="/home/${USERNAME}/.local/bin:${PATH}"

# Native claude install lands in ~/.local (outside the mounted ~/.claude).
# CACHEBUST forces a fresh download each run when changed (set by run.sh).
ARG CACHEBUST=0
RUN curl -fsSL https://claude.ai/install.sh | bash

COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh

# PreToolUse guard: blocks writes of self-admitted guessing code (settings.json
# references it by this absolute path). Runs on the system python3.
COPY --chmod=0755 hooks/no_guess.py /usr/local/bin/no-guess-hook

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
