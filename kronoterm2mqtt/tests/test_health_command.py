import io
import json
from unittest import TestCase
from unittest.mock import patch
import urllib.error

from rich.console import Console

from kronoterm2mqtt.cli_app import health as health_module
from kronoterm2mqtt.user_settings import UserSettings


HEALTHY = {
    'healthy': True,
    'uptime_seconds': 32.0,
    'mqtt': {'connected': True, 'host': 'mqtt.example.com', 'last_publish_seconds_ago': 1.2},
    'modbus': {
        'port': '192.168.1.2:502',
        'last_read_seconds_ago': 1.3,
        'last_read_complete': True,
        'failed_reads': 0,
        'last_error': None,
    },
    'sensors_published': 107,
    'problems': [],
}

UNHEALTHY = {
    **HEALTHY,
    'healthy': False,
    'problems': ['last Modbus read was 310.4s ago'],
    'modbus': {**HEALTHY['modbus'], 'last_read_complete': None, 'failed_reads': 7, 'last_error': 'Giving up'},
}


def run_command(**patches) -> tuple[int, str]:
    """Run the health command and return its exit code and output."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=200, no_color=True, highlight=False)
    user_settings = UserSettings()
    for key, value in patches.pop('settings', {}).items():
        setattr(user_settings.health, key, value)

    with (
        patch.object(health_module, 'print', console.print),
        patch.object(health_module, 'get_user_settings', return_value=user_settings),
        patch.object(health_module, 'fetch_health', **patches),
    ):
        try:
            health_module.health(verbosity=0)
        except SystemExit as err:
            return err.code, buffer.getvalue()
    return 0, buffer.getvalue()


class HealthCommandTestCase(TestCase):
    def test_a_healthy_loop_exits_zero(self):
        code, output = run_command(return_value=HEALTHY)

        self.assertEqual(code, 0)
        self.assertIn('HEALTHY', output)
        self.assertIn('mqtt.example.com', output)
        self.assertIn('107 entities', output)
        self.assertIn('1.3s ago', output)

    def test_an_unhealthy_loop_exits_one_and_names_the_problems(self):
        code, output = run_command(return_value=UNHEALTHY)

        self.assertEqual(code, 1)
        self.assertIn('UNHEALTHY', output)
        self.assertIn('last Modbus read was 310.4s ago', output)
        self.assertIn('Giving up', output)
        self.assertIn('unknown', output)  # last_read_complete is None

    def test_no_answer_is_reported_as_a_stopped_loop(self):
        code, output = run_command(side_effect=urllib.error.URLError('connection refused'))

        self.assertEqual(code, 1)
        self.assertIn('No answer', output)
        self.assertIn('Is the publish loop running?', output)

    def test_a_disabled_endpoint_says_so(self):
        code, output = run_command(return_value=HEALTHY, settings={'enabled': False})

        self.assertEqual(code, 2)
        self.assertIn('disabled', output)


class FetchHealthTestCase(TestCase):
    def test_a_503_answer_is_a_result_not_an_error(self):
        body = json.dumps(UNHEALTHY).encode()
        error = urllib.error.HTTPError('http://127.0.0.1:8099/health', 503, 'Service Unavailable', {}, io.BytesIO(body))

        with patch('urllib.request.urlopen', side_effect=error):
            state = health_module.fetch_health(host='127.0.0.1', port=8099)

        self.assertFalse(state['healthy'])
        self.assertEqual(state['problems'], UNHEALTHY['problems'])

    def test_a_200_answer_is_parsed(self):
        response = io.BytesIO(json.dumps(HEALTHY).encode())
        response.__enter__ = lambda self=response: self
        response.__exit__ = lambda *args: None

        with patch('urllib.request.urlopen', return_value=response):
            state = health_module.fetch_health(host='127.0.0.1', port=8099)

        self.assertTrue(state['healthy'])
