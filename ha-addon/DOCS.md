# kronoterm2mqtt

Reads a KRONOTERM heat pump over Modbus and publishes it to MQTT with Home Assistant
discovery, so every sensor, switch and setting appears as an entity without any YAML.

## Installation

1. **Settings → Add-ons → Add-on store → ⋮ → Repositories** and add
   `https://github.com/dgprivate/kronoterm2mqtt`
2. Install **kronoterm2mqtt** from the store.
3. Open the **Configuration** tab and set at least the heat pump port (below).
4. Start the add-on, then watch the **Log** tab for the first reading.

The add-on needs a broker. If you run the **Mosquitto broker** add-on, leave
`use_supervisor` on and there is nothing to fill in — Home Assistant hands over the
address and credentials itself.

## Configuration

Everything is set on the **Configuration** tab. The defaults are the ones the project
ships with; the sections below say what each group is for and which entries you are
likely to change.

### Log level

`error`, `warning`, `info` (default) or `debug`. `info` reports Modbus retries and
reconnects — useful when a gateway is flaky. `debug` logs every register read and every
MQTT message, and is noisy enough that you will want to turn it back down.

### MQTT broker

| Option | Meaning |
| --- | --- |
| `use_supervisor` | Take host, port, user name and password from the Mosquitto add-on. On by default; the four fields below are then ignored. |
| `host`, `port` | The broker, when `use_supervisor` is off. |
| `user_name`, `password` | Broker credentials. Leave empty for an anonymous broker. |
| `main_uid` | Prefix of every entity this add-on creates. Changing it later makes Home Assistant see a second, separate device, so pick it before you start. |
| `publish_config_throttle_seconds` | How often the discovery configuration is repeated. |
| `publish_throttle_seconds` | Shortest gap between two state messages for the same entity. |

### MQTT over TLS

Only for a broker that requires TLS. `ca_certs` verifies the broker, `certfile` plus
`keyfile` authenticate this add-on to it. All three are paths inside the container. Two
directories are mounted read-only for this: `/ssl`, where Home Assistant keeps its own
certificates, and `/share`, for anything else — put the files in one of them and point
at them there, for example `/share/mqtt/ca.crt`.
`insecure` skips the host name check — for testing, not for a broker on a network you
do not control.

### Heat pump

| Option | Meaning |
| --- | --- |
| `port` | `/dev/ttyUSB0` for a direct RS485 adapter, or `192.168.1.50:502` for a network gateway. |
| `definitions_name` | Which register map to use. `kronoterm_ksm` fits the ETERA and KSM series. |
| `device_name`, `model` | What the device is called in Home Assistant. |
| `timeout` | How long a single Modbus read may take, in seconds. Raise it for a slow gateway. |
| `pooling_interval` | Seconds between readings. Ten is a reasonable pace for a heat pump. |

For a serial adapter, plug it into the Home Assistant machine and use the path shown in
**Settings → System → Hardware → All hardware**. The add-on already asks for serial
access, so no further permission is needed.

### Health endpoint

The add-on answers on port 8099 with whether MQTT, Modbus and publishing are working,
and the Supervisor watches it: if the answer stops arriving, or reports trouble, the
add-on is restarted. The port is not published outside Home Assistant.

| Option | Meaning |
| --- | --- |
| `enabled` | Turning this off also turns off the watchdog. |
| `host` | Keep `0.0.0.0`; on `127.0.0.1` the watchdog cannot reach it. |
| `stale_after_seconds` | Data older than this counts as a problem. |
| `restart_after_seconds` | After this long in trouble, the add-on stops itself so the Supervisor starts it again. `0` never restarts on its own. |

### Custom ETERA expander

For the do-it-yourself expander module with DS18S20 thermometers that drives extra
heating loops and solar pumps. Leave `module_enabled` off if you do not have one — the
rest of the section is then ignored.

`exercise_valves_during_dhw` runs each mixing valve fully closed and back to where it
was, once per stretch of sanitary water heating. A valve that spends a mild week at one
position can seize, and while hot water is being made the loops are not circulating, so
the sweep changes no room temperature. Off by default.

The lists are read positionally: the first entry of `loop_operation`, `loop_sensors`
and `loop_temperature` all describe loop 1. `sensor_names` names the thermometers in
the order they are wired, and an empty entry in `relay_names` means that relay is
unused. `number_of_thermometers` is checked at start-up, so a wiring mistake is
reported instead of quietly producing wrong readings.

## What you get

One device per heat pump, with sensors for temperatures, pressures, power and the
current operating mode, switches for the circuits, and settable numbers for the target
temperatures. Everything arrives through MQTT discovery, so it appears by itself once
the add-on is running.

## Troubleshooting

**Nothing appears in Home Assistant.** Check the log for a line reporting the MQTT
connection. If it says the broker refused the connection, the credentials are wrong;
with `use_supervisor` on and the Mosquitto add-on running, that should not happen.

**`Invalid Modbus protocol id`, or reads that fail after a while.** Some RS485-to-TCP
gateways send their MAC address as a greeting when a connection is opened. The add-on
drains that greeting and logs `Discarded N unsolicited bytes`; if you see the reads
recover after that line, this is working as intended.

**The add-on restarts every few minutes.** The watchdog is doing its job: the health
endpoint reports that no data has arrived. Look further up the log for the Modbus
error, and raise `timeout` if the gateway is slow to answer.

**Reads fail only sometimes.** Raise `heat_pump.timeout` and `pooling_interval`. A
heat pump that is asked too often over a slow link answers late rather than not at all.

## Support

Problems and questions: <https://github.com/dgprivate/kronoterm2mqtt/issues>.
A security issue should go through
[private reporting](https://github.com/dgprivate/kronoterm2mqtt/security/advisories/new)
instead — see `SECURITY.md` in the repository.
