# Security posture

What this image does and does not promise, with a way to check each claim rather
than a badge. Everything here is about the **container**; the notes at the bottom
say which parts are the host's job instead.

## Verify it yourself

```bash
docker compose build

# Unprivileged, no capabilities, nothing writable
docker compose run --rm --entrypoint /bin/sh kronoterm2mqtt -c 'id; grep CapEff /proc/self/status'
docker compose run --rm --entrypoint /bin/sh kronoterm2mqtt -c 'touch /app/x' # Read-only file system

# What is inside, and what a scanner makes of it
docker run --rm --entrypoint cat kronoterm2mqtt:local /usr/share/kronoterm2mqtt/sbom.cdx.json > sbom.json
grype sbom:sbom.json --vex security/kronoterm2mqtt.openvex.json
trivy image --vex security/kronoterm2mqtt.openvex.json kronoterm2mqtt:local
```

## CIS Docker Benchmark

The benchmark covers the host, the daemon and the container. Only the last part is
decided by this repository; the rest is your machine, and no image can do it for you.

| CIS section | Control | Where it is done | Check |
|---|---|---|---|
| 4.1 | Run as a non-root user | `Dockerfile` (`USER 65532`) | `id` inside the container |
| 4.2 | Use trusted base images | Base pinned by SHA256 digest | `ARG PYTHON_IMAGE` |
| 4.3 | No unnecessary packages | Alpine, no package manager left | `command -v apk pip` |
| 4.6 | Add a HEALTHCHECK | `Dockerfile` | `docker ps` shows healthy |
| 4.7 | Do not use update instructions alone | `apk upgrade` runs in the same layer as the install | `Dockerfile` |
| 4.9 | Use COPY, not ADD | `Dockerfile` | `grep ADD Dockerfile` |
| 4.10 | No secrets in the image | `config/`, `*.key`, `*.pem` excluded | `.dockerignore` |
| 5.3 | Drop Linux capabilities | `cap_drop: [ALL]` | `grep CapEff /proc/self/status` |
| 5.4 | No privileged containers | never set | `docker inspect` |
| 5.7 | Do not map privileged ports | health endpoint is 8099, unpublished | `docker-compose.yml` |
| 5.10-5.11 | Memory and CPU limits | `mem_limit`, `cpus` | `docker inspect` |
| 5.12 | Read-only root filesystem | `read_only: true` | `touch /app/x` fails |
| 5.25 | No new privileges | `no-new-privileges:true` | `docker inspect` |
| 5.28 | PID limit | `pids_limit: 128` | `docker inspect` |

Left to the host, and worth doing: user namespace remapping
(`"userns-remap": "default"` in `/etc/docker/daemon.json`), keeping the daemon
socket off the network, and auditing. `docker-bench-security` checks all of it:

```bash
docker run --rm --net host --pid host --userns host --cap-add audit_control \
  -v /var/lib:/var/lib:ro -v /var/run/docker.sock:/var/run/docker.sock:ro \
  docker/docker-bench-security
```

## Vulnerabilities

The published image is rebuilt weekly and the build fails on **fixable** HIGH or
CRITICAL findings, so what ships is what could be fixed at the time. Two things
follow from that, and both are deliberate:

* An advisory published after a build does not retroactively change the image. The
  weekly rebuild is the answer, not a promise that the number stays at zero.
* Findings without a fix do not block the build. Blocking on them would only mean
  nothing ever ships; they are recorded here instead.

Where a finding cannot be fixed *and* does not apply to this application, that
analysis goes into `kronoterm2mqtt.openvex.json` as an OpenVEX statement, which
scanners read. The image carries a copy at
`/usr/share/kronoterm2mqtt/vex.openvex.json`.

Writing a statement, in practice: the product identifiers have to match the purls
in the SBOM **exactly**, qualifiers included
(`pkg:apk/alpine/libssl3@3.5.7-r0?arch=aarch64&distro=alpine-3.24.1`). A statement
with a shortened purl is silently ignored - check with `grype sbom:sbom.json --vex
...` that the finding actually moves to the ignored list. And a statement is a
claim about the code, not a mute button: `libssl` is in the execute path of every
MQTT connection this app makes, so it never belongs here.

## What is deliberately not claimed

* **FIPS**: needs a FIPS 140-3 validated cryptographic module with a certificate
  number. Alpine's OpenSSL is not one, so the claim would be false.
* **STIG**: defined against specific operating system baselines and proven with
  DISA tooling. Self-declaring it means nothing.
* **A patching SLA**: the weekly rebuild is a schedule, not a service level. Nobody
  here is on call.
