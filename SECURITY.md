# Security

## Reporting a vulnerability

Report anything you find privately, through GitHub's
[private vulnerability reporting](https://github.com/dgprivate/kronoterm2mqtt/security/advisories/new),
rather than in a public issue. Include what you did, what happened, and the version or image digest you
were running - `docker image inspect <image> --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`
names the commit an image was built from.

This is a spare-time project, so there is no response time to promise. Expect an acknowledgement rather
than a fix on a schedule.

## What the project does about security

[`security/README.md`](security/README.md) is the detailed version: which CIS Docker Benchmark controls the
container satisfies and the command that verifies each one, how findings that cannot be fixed and do not apply
are recorded as OpenVEX statements, and - just as important - what is deliberately **not** claimed, such as FIPS,
STIG or any patching service level.

In short:

* the image runs unprivileged with no capabilities, a read-only root filesystem and no package manager
* it carries an SBOM of itself, and published images carry SBOM and provenance attestations plus a keyless signature
* the base image and tools are pinned by digest, Renovate moves those pins, and the image is rebuilt weekly
* the build fails on fixable HIGH or CRITICAL findings

## Supported versions

The newest release is the supported one. Older tags stay on Docker Hub for reproducibility, but they are not
patched - `latest` and the current `X.Y.Z` tag get the weekly rebuild.
