# kronoterm2mqtt

Connects a **KRONOTERM heat pump** to **Home Assistant**. It reads the pump's Modbus registers, over a USB RS485 adapter or a Modbus/TCP gateway, and publishes them to an MQTT broker using Home Assistant's discovery protocol, so temperatures, pressures, pump states and error codes appear as entities without manual configuration. Switches and selects write back, so heating programs and hot water are controlled from Home Assistant.

* **Source:** https://github.com/dgprivate/kronoterm2mqtt
* **Upstream project:** https://github.com/kosl/kronoterm2mqtt
* **Releases:** https://github.com/dgprivate/kronoterm2mqtt/releases
* **Latest release:** `IMAGE_VERSION`

## Tags

| Tag | What it is |
|---|---|
| `latest` | the newest build of the main branch |
| `X.Y.Z` | a release, and the tag to pin in production |
| `X.Y` | the newest patch of that minor release |
| `sha-<commit>` | one exact commit |

Built for `linux/amd64` and `linux/arm64`: a server and a Raspberry Pi 4/5 take the same tag.

## Quick start

```yaml
services:
  kronoterm2mqtt:
    image: IMAGE_NAME:IMAGE_VERSION
    container_name: kronoterm2mqtt
    restart: unless-stopped
    user: "65532:65532"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    tmpfs: ["/tmp:rw,noexec,nosuid,nodev,size=16m"]
    init: true
    volumes:
      - ./config:/home/nonroot/.config/kronoterm2mqtt:ro
      - ./config/certs:/certs:ro   # only if MQTT uses TLS
```

```bash
docker compose up -d
docker compose exec kronoterm2mqtt health
```

Settings live in `config/kronoterm2mqtt.toml` next to the compose file. To generate a default one:

```bash
docker compose run --rm -v "$PWD/config:/home/nonroot/.config/kronoterm2mqtt" \
  kronoterm2mqtt edit-settings
```

At a minimum it needs the MQTT broker and where the heat pump is:

```toml
[mqtt]
host = "mqtt.example.com"
port = 1883
user_name = "user"
password = "secret"

[heat_pump]
port = "192.168.1.2:502"   # Modbus/TCP gateway, or /dev/ttyUSB0 for RS485
```

A USB RS485 adapter needs the device passed in, and the container joins the group that owns it, because it does not run as root:

```yaml
devices: ["/dev/ttyUSB0:/dev/ttyUSB0"]
group_add: ["20"]   # stat -c '%g' /dev/ttyUSB0
```

## Running Home Assistant OS?

Then you do not need this image directly: the same release is packaged as an add-on, with every setting on its Configuration tab and the Supervisor watching the health endpoint. Add
`https://github.com/dgprivate/kronoterm2mqtt` under **Settings -> Add-ons -> Add-on store -> ⋮ -> Repositories** and install **kronoterm2mqtt** from the store.

## Is it working?

The publish loop reports on itself, and the container's `HEALTHCHECK` uses the same endpoint, so `docker ps` shows `healthy` or `unhealthy`:

```
$ docker compose exec kronoterm2mqtt health

HEALTHY
 MQTT          OK        mqtt.example.com
 Last publish  1.2s ago  107 entities
 Modbus        OK        192.168.1.2:502
 Last read     1.3s ago
 Failed reads  0
 Uptime        32s
```

After a long outage the process ends itself so the restart policy starts it again; set `[health] restart_after_seconds = 0` to switch that off.

## What is in the image

Alpine, running as an unprivileged user (UID 65532) with every Linux capability dropped, a read-only root filesystem and no package manager left inside. The base is pinned by digest and the image is rebuilt weekly, so patches actually ship.

Each build carries an inventory of itself:

```bash
docker run --rm --entrypoint cat IMAGE_NAME:IMAGE_VERSION \
  /usr/share/kronoterm2mqtt/sbom.cdx.json > sbom.json
grype sbom:sbom.json
```

and can be traced back to the workflow that produced it:

```bash
cosign verify IMAGE_NAME:IMAGE_VERSION \
  --certificate-identity-regexp '^https://github.com/.+/.github/workflows/publish-image.yml@.+' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

What is claimed, what is verified and what is deliberately not claimed is written down in
[security/README.md](https://github.com/dgprivate/kronoterm2mqtt/blob/main/security/README.md).

## Licence

GPL-3.0-or-later. Original project by Leon Kos and contributors.
