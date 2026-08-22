"""Run kronoterm2mqtt as a Home Assistant add-on.

Home Assistant configures an add-on through its own UI and hands the result to the
container as /data/options.json. This module turns that into the TOML settings file the
app reads, then starts the publish loop - so everything this project can be configured
with is reachable from the Home Assistant interface, and nobody has to edit a file over
SSH.

Two things happen here that the normal container does not need:

* MQTT credentials can come from the Mosquitto add-on instead of the form. Home
  Assistant already knows them, and retyping a broker password is the most common way
  to end up with an add-on that silently publishes nowhere.
* The entrypoint starts as root, because /data belongs to root and the settings file
  has to be written into it, and then drops to the same unprivileged user the plain
  container runs as before starting the app.
"""

import dataclasses
import grp
import json
import logging
import os
from pathlib import Path
import pwd
import sys
import urllib.error
import urllib.request

import tomlkit

from kronoterm2mqtt.user_settings import CustomEteraExpander, HealthCheck, HeatPump, MqttTlsSettings, UserSettings


logger = logging.getLogger(__name__)

# The directory Home Assistant keeps between restarts, and the two files in it.
DATA_PATH = Path('/data')
OPTIONS_PATH = DATA_PATH / 'options.json'
SETTINGS_PATH = DATA_PATH / '.config' / 'kronoterm2mqtt' / 'kronoterm2mqtt.toml'
SUPERVISOR_MQTT_URL = 'http://supervisor/services/mqtt'

APP_UID = 65532
APP_GID = 65532

# The sections the add-on renders, and the dataclass that defines what may go in each.
# Anything the UI sends that is not a field of these is ignored: the add-on schema and
# the app's settings are two files that have to agree, and this is where they meet.
SECTIONS = {
    'mqtt': type(UserSettings().mqtt),
    'mqtt_tls': MqttTlsSettings,
    'heat_pump': HeatPump,
    'health': HealthCheck,
    'custom_expander': CustomEteraExpander,
}

# Home Assistant's own log level, translated into the app's -v flags.
VERBOSITY_FLAGS = {'error': [], 'warning': ['-v'], 'info': ['-vv'], 'debug': ['-vvv']}


def field_default(field: dataclasses.Field):
    if field.default_factory is not dataclasses.MISSING:
        return field.default_factory()
    if field.default is not dataclasses.MISSING:
        return field.default
    return None


def supervisor_mqtt(token: str | None = None, timeout: float = 5.0) -> dict:
    """Ask the Supervisor for the broker Home Assistant itself uses."""
    token = token or os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        logger.warning('No Supervisor token, cannot look up the MQTT service')
        return {}

    request = urllib.request.Request(SUPERVISOR_MQTT_URL, headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answer = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError) as err:
        logger.warning(f'Could not ask the Supervisor for the MQTT service: {err}')
        return {}

    service = answer.get('data') if isinstance(answer, dict) else None
    if not isinstance(service, dict) or not service.get('host'):
        logger.warning('The Supervisor did not report an MQTT service - is the Mosquitto add-on running?')
        return {}

    settings = {
        'host': service['host'],
        'port': service.get('port', 1883),
        'user_name': service.get('username', ''),
        'password': service.get('password', ''),
    }
    logger.info(f'Using the MQTT broker Home Assistant knows about: {settings["host"]}:{settings["port"]}')
    return settings


def settings_document(options: dict, mqtt_service: dict | None = None) -> tomlkit.TOMLDocument:
    """Render the add-on options as the settings document the app reads."""
    document = tomlkit.document()
    document.add(
        tomlkit.comment(
            'Written by the Home Assistant add-on from the options set in its UI.'
            ' Changes made here are overwritten on the next start.'
        )
    )

    for name, section_type in SECTIONS.items():
        chosen = options.get(name)
        chosen = dict(chosen) if isinstance(chosen, dict) else {}

        if name == 'mqtt' and chosen.pop('use_supervisor', False):
            chosen.update(mqtt_service or {})

        table = tomlkit.table()
        for field in dataclasses.fields(section_type):
            value = chosen.get(field.name, field_default(field))
            if value is None:
                continue  # Nothing to say about this one; the app keeps its own default
            table[field.name] = value
        document[name] = table

    return document


def write_settings(document: tomlkit.TOMLDocument, path: Path = SETTINGS_PATH) -> bool:
    """Put the rendered sections into the settings file. Returns whether it changed.

    Only the sections this module renders are replaced: the app completes the file with
    settings of its own on first start, and overwriting the whole thing would make it
    do that again - with a backup copy each time - on every restart.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    before = path.read_text(encoding='UTF-8') if path.is_file() else ''
    merged = tomlkit.loads(before) if before else document
    if merged is not document:
        for name, table in document.items():
            merged[name] = table

    after = tomlkit.dumps(merged)
    if after == before:
        return False

    path.write_text(after, encoding='UTF-8')
    return True


def own_data_directory(path: Path, uid: int = APP_UID, gid: int = APP_GID) -> None:
    """Hand /data to the user the app runs as, so it can be written after the drop."""
    for target in (path, *path.rglob('*')):
        try:
            os.chown(target, uid, gid)
        except OSError as err:
            logger.warning(f'Cannot change the owner of {target}: {err}')


def drop_privileges(uid: int = APP_UID, gid: int = APP_GID) -> None:
    """Continue as the unprivileged user, as the plain container does."""
    if os.geteuid() != 0:
        return  # Already unprivileged: nothing to drop, and setuid would fail

    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        name = None
    try:
        os.setgroups([gid] if name is None else [group.gr_gid for group in grp.getgrall() if name in group.gr_mem])
    except OSError as err:
        logger.warning(f'Cannot reset the supplementary groups: {err}')
    os.setgid(gid)
    os.setuid(uid)


def command(options: dict) -> list[str]:
    log_level = str(options.get('log_level', 'info')).lower()
    flags = VERBOSITY_FLAGS.get(log_level, VERBOSITY_FLAGS['info'])
    return ['/opt/venv/bin/kronoterm2mqtt_app', 'publish-loop', *flags]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s', stream=sys.stdout)

    if not OPTIONS_PATH.is_file():
        logger.error(f'{OPTIONS_PATH} does not exist - this entrypoint only runs as a Home Assistant add-on')
        return 1

    options = json.loads(OPTIONS_PATH.read_text(encoding='UTF-8'))
    wants_supervisor = bool((options.get('mqtt') or {}).get('use_supervisor'))
    document = settings_document(options, mqtt_service=supervisor_mqtt() if wants_supervisor else None)
    if write_settings(document, path=SETTINGS_PATH):
        logger.info(f'Wrote {SETTINGS_PATH} from the add-on options')
    else:
        logger.info(f'{SETTINGS_PATH} already matches the add-on options')

    own_data_directory(DATA_PATH)
    drop_privileges()

    # The image sets HOME to the nonroot home, but the settings are in /data. Handed to
    # the new process rather than set here, so this one is left as it was found.
    environment = {**os.environ, 'HOME': str(DATA_PATH)}
    argv = argv or command(options)
    logger.info(f'Starting: {" ".join(argv)}')
    os.execve(argv[0], argv, environment)
    return 0  # Not reached: execve replaces this process


if __name__ == '__main__':
    sys.exit(main())
