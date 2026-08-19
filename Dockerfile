# syntax=docker/dockerfile:1

# Hardened, least-privilege image for kronoterm2mqtt.
#
#  * multi-stage: compilers and the package manager stay in the builder
#  * base image pinned by digest so builds are reproducible
#  * runs as an unprivileged user (UID/GID 65532, the "nonroot" convention)
#  * no runtime bootstrap: the venv is fully built at image build time, so the
#    container needs neither a writable /app nor network access to PyPI
#
# Alpine rather than Debian slim on purpose: the Debian base ships packages this
# app never executes (perl, ncurses, gzip) whose open advisories Debian marks as
# "affected" or "fix_deferred", so they cannot be patched away. On the same build,
# Trivy reported 14 HIGH/CRITICAL for the Debian image and 0 for this one.
#
# Rebuild the digest with:
#   docker pull python:3.14-alpine && docker image inspect python:3.14-alpine \
#     --format '{{index .RepoDigests 0}}'
ARG PYTHON_IMAGE=python@sha256:05b2b8b732ecd268fee8727a369f936f022d1321b59befd13c30ede22769dcdc
# syft 1.51.0, used to record what ends up in the image (see the "sbom" stage)
ARG SYFT_IMAGE=anchore/syft@sha256:678bfa565b60f747aac0f8e964fe5588a24445b8d0a480e91f6efd70020dfbb0
ARG UV_VERSION=0.12.5
ARG APP_UID=65532
ARG APP_GID=65532
# Traceability: pass these in to record where the image came from, e.g.
#   docker compose build --build-arg VCS_REF=$(git rev-parse --short HEAD)
ARG VCS_REF=unknown
ARG APP_VERSION=unknown
ARG BUILD_DATE=unknown


# --------------------------------------------------------------------------
# Builder: creates /opt/venv with all runtime dependencies
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ARG UV_VERSION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

# Every current dependency ships a musllinux wheel, so nothing is compiled here.
# The toolchain stays for the day one of them does not; it never reaches the
# final image either way.
RUN apk add --no-cache gcc musl-dev linux-headers

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app

# Project metadata first, so the dependency layer is cached independently
COPY uv.lock pyproject.toml README.md healthcheck.py ./

# Submodule that kronoterm2mqtt/pyetera_uart_bridge symlinks into
COPY etera-uart-bridge/pyetera-uart-bridge/pyetera_uart_bridge/ \
     etera-uart-bridge/pyetera-uart-bridge/pyetera_uart_bridge/

# Source code plus the symlink that git tracks but the build context flattens
COPY kronoterm2mqtt/ kronoterm2mqtt/
RUN rm -rf kronoterm2mqtt/pyetera_uart_bridge && \
    ln -s ../etera-uart-bridge/pyetera-uart-bridge/pyetera_uart_bridge \
          kronoterm2mqtt/pyetera_uart_bridge

# Locked, reproducible install of runtime deps + the project itself
RUN uv sync --frozen --no-dev


# --------------------------------------------------------------------------
# Runtime base: no compilers, no package manager, no root
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime-base

ARG APP_UID
ARG APP_GID

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/nonroot

# Latest OS security patches, an unprivileged account, and the config directory
# the settings loader expects (~/.config/kronoterm2mqtt).
RUN apk upgrade --no-cache && \
    addgroup -g "${APP_GID}" nonroot && \
    adduser -u "${APP_UID}" -G nonroot -h /home/nonroot -s /sbin/nologin -D nonroot && \
    mkdir -p /home/nonroot/.config/kronoterm2mqtt && \
    chown -R "${APP_UID}:${APP_GID}" /home/nonroot && \
    # Strip setuid/setgid bits: nothing in here needs privilege escalation
    find / -xdev -perm /6000 -type f -exec chmod a-s {} + || true && \
    # Remove the package managers so an attacker cannot install tooling. The apk
    # database stays, so image scanners (Trivy, Grype, syft) still work.
    rm -rf /usr/local/lib/python*/site-packages/pip* /usr/local/bin/pip* \
           /sbin/apk /etc/apk/keys

# Application and venv stay root-owned and read-only for the app user, so the
# process cannot rewrite its own code.
COPY --from=builder --chown=root:root /opt/venv /opt/venv
COPY --from=builder --chown=root:root /app /app

# "docker compose exec kronoterm2mqtt health" - exec skips the ENTRYPOINT, so the
# status command needs a name of its own on PATH.
RUN printf '#!/bin/sh\nexec /opt/venv/bin/kronoterm2mqtt_app health "$@"\n' > /usr/local/bin/health && \
    chmod 0755 /usr/local/bin/health


# --------------------------------------------------------------------------
# SBOM: an inventory of everything the runtime image contains
# --------------------------------------------------------------------------
# Generated here rather than as a BuildKit attestation, because attestations
# need the containerd image store; a file in the image works with any builder.
# On the build platform, not the target one: syft reads the copied filesystem, it
# does not run anything from it, so there is no reason to emulate another
# architecture for the scan.
FROM --platform=$BUILDPLATFORM ${SYFT_IMAGE} AS sbom

COPY --from=runtime-base / /scan

# "installed" keeps the catalogers that report what is actually present (the apk
# database, installed Python distributions) and leaves out the "declared" ones,
# which would read uv.lock and list dev dependencies that are not in the image -
# false positives for anything scanning this SBOM. File metadata is off: this is
# an inventory of packages, not of every file.
ENV SYFT_FILE_METADATA_SELECTION=none
RUN ["/syft", "scan", "dir:/scan", "--source-name", "kronoterm2mqtt", \
     "--select-catalogers", "installed", \
     "-o", "cyclonedx-json@1.6=/sbom.cdx.json"]


# --------------------------------------------------------------------------
# Runtime: the base image plus its own bill of materials
# --------------------------------------------------------------------------
FROM runtime-base AS runtime

ARG APP_UID
ARG APP_GID
ARG VCS_REF
ARG APP_VERSION
ARG BUILD_DATE

COPY --from=sbom --chown=root:root /sbom.cdx.json /usr/share/kronoterm2mqtt/sbom.cdx.json

LABEL org.opencontainers.image.title="kronoterm2mqtt" \
      org.opencontainers.image.description="Sends MQTT events from a KRONOTERM heat pump" \
      org.opencontainers.image.source="https://github.com/kosl/kronoterm2mqtt" \
      org.opencontainers.image.documentation="https://github.com/kosl/kronoterm2mqtt#docker" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      si.kronoterm2mqtt.sbom.path="/usr/share/kronoterm2mqtt/sbom.cdx.json" \
      si.kronoterm2mqtt.sbom.format="CycloneDX JSON"

WORKDIR /app
USER ${APP_UID}:${APP_GID}

# The endpoint listens on localhost inside the container and is not published.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["/opt/venv/bin/python", "/app/healthcheck.py"]

ENTRYPOINT ["/opt/venv/bin/kronoterm2mqtt_app"]
# -v sets the log level to WARNING so Modbus retries and reconnects show up in
# the container logs. Override with `command:` in compose for more/less output.
CMD ["publish-loop", "-v"]
