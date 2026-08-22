ARG BUILD_FROM=hausbit/kronoterm2mqtt:0.1.20
FROM ${BUILD_FROM}

# The published image runs as UID 65532 and cannot write /data, which belongs to root
# and is where Home Assistant keeps the add-on options. The entrypoint therefore starts
# as root, writes the settings, and drops back to 65532 before starting the app - so
# the process that talks to the network is as unprivileged here as in the plain
# container.
USER root

# Home Assistant keeps /data between restarts, so that is where the settings belong.
# On the image itself, not only in the entrypoint's environment: a command started
# later with "docker exec" - the health status, for one - has to find them too.
ENV HOME=/data

LABEL io.hass.type="addon" \
      io.hass.name="kronoterm2mqtt" \
      io.hass.description="Publish a KRONOTERM heat pump to MQTT"

ENTRYPOINT ["/opt/venv/bin/python", "-m", "kronoterm2mqtt.ha_addon"]
CMD []
