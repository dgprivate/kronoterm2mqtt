import json
import logging
import sys
import urllib.error
import urllib.request

from cli_base.cli_tools.verbosity import setup_logging
from cli_base.tyro_commands import TyroVerbosityArgType
from rich import print
from rich.table import Table

from kronoterm2mqtt.cli_app import app
from kronoterm2mqtt.user_settings import UserSettings, get_user_settings


logger = logging.getLogger(__name__)


def fetch_health(host: str, port: int, timeout: float = 5.0) -> dict:
    """Ask the running publish loop how it is doing. Raises URLError if it is not running."""
    url = f'http://{host}:{port}/health'
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        # An unhealthy loop answers with 503 and the same payload - that is a result, not an error.
        return json.loads(err.read())


@app.command
def health(verbosity: TyroVerbosityArgType):
    """
    Show the status of the running publish loop (MQTT, Modbus, publishing)
    """
    setup_logging(verbosity=verbosity)
    user_settings: UserSettings = get_user_settings(verbosity=verbosity)
    settings = user_settings.health

    if not settings.enabled:
        print('[yellow]The health endpoint is disabled in the settings ([health] enabled = false)')
        sys.exit(2)

    try:
        state = fetch_health(host=settings.host, port=settings.port)
    except (urllib.error.URLError, OSError) as err:
        print(f'[red]No answer from http://{settings.host}:{settings.port}/health: {err}')
        print('[yellow]Is the publish loop running?')
        sys.exit(1)

    if not isinstance(state, dict):
        print(f'[red]http://{settings.host}:{settings.port}/health did not answer with a health report')
        sys.exit(1)

    published = field(state, 'sensors_published', default='?')

    table = Table(show_header=False, box=None)
    table.add_row('MQTT', status(field(state, 'mqtt', 'connected')), text(field(state, 'mqtt', 'host')))
    table.add_row(
        'Last publish', seconds_ago(field(state, 'mqtt', 'last_publish_seconds_ago')), f'{published} entities'
    )
    table.add_row('Modbus', status(field(state, 'modbus', 'last_read_complete')), text(field(state, 'modbus', 'port')))
    table.add_row('Last read', seconds_ago(field(state, 'modbus', 'last_read_seconds_ago')), '')
    table.add_row(
        'Failed reads', text(field(state, 'modbus', 'failed_reads'), '?'), text(field(state, 'modbus', 'last_error'))
    )
    table.add_row('Uptime', uptime(field(state, 'uptime_seconds')), '')

    healthy = bool(state.get('healthy'))

    print()
    if healthy:
        print('[green]HEALTHY[/green]')
    else:
        print('[red]UNHEALTHY[/red]')
        problems = field(state, 'problems', default=[])
        if isinstance(problems, list):
            for problem in problems:
                print(f'  [red]-[/red] {text(problem)}')
    print(table)

    sys.exit(0 if healthy else 1)


def field(state: dict, *path: str, default=None):
    """Read a value out of the health report, tolerating anything that is not there.

    The report arrives over a socket, so it can come from an older container, from a
    half-written answer or from something else listening on that port. A status command
    is the wrong place to raise a traceback: whatever it cannot find reads as unknown.
    """
    value = state
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def text(value, default: str = '') -> str:
    if value is None:
        return default
    return str(value)


def uptime(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return '[yellow]unknown'
    return f'{value:.0f}s'


def status(value) -> str:
    if value is None:
        return '[yellow]unknown'
    return '[green]OK' if value else '[red]FAILED'


def seconds_ago(value) -> str:
    if value is None:
        return '[yellow]never'
    return f'{value}s ago'
