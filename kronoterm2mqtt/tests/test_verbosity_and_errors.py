"""The branches that only run when something is verbose or something went wrong."""

import io
import socket
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import MagicMock, patch

from ha_services.mqtt4homeassistant.device import BaseMqttDevice

from kronoterm2mqtt import expander as expander_module
from kronoterm2mqtt.cli_dev import update_readme_history as history_cli
from kronoterm2mqtt.tests.test_mqtt_handler import DEFINITIONS, StopLoop, make_handler
from kronoterm2mqtt.user_settings import UserSettings


class EteraCallbackTestCase(IsolatedAsyncioTestCase):
    """The handlers the board calls when it resets or says something."""

    async def test_a_reset_is_logged_as_an_error(self):
        with self.assertLogs('kronoterm2mqtt.expander', level='ERROR') as logs:
            await expander_module.etera_reset_handler()

        self.assertIn('just reset', '\n'.join(logs.output))

    async def test_a_message_is_printed_as_text(self):
        with patch('sys.stdout', new=io.StringIO()) as out:
            await expander_module.etera_message_handler(b'hello board')

        self.assertIn('hello board', out.getvalue())

    async def test_a_message_that_is_not_text_is_printed_raw(self):
        with patch('sys.stdout', new=io.StringIO()) as out:
            await expander_module.etera_message_handler(b'\xff\xfe not utf-8')

        self.assertIn('xff', out.getvalue())


class VerboseHandlerTestCase(IsolatedAsyncioTestCase):
    """Verbosity adds output; it must not change what happens."""

    def tearDown(self):
        BaseMqttDevice.device_uids = set()
        BaseMqttDevice.components = {}

    async def test_a_verbose_cycle_publishes_the_same_states(self):
        handler = make_handler(registers={2100: 177})
        handler.verbosity = 2

        with (
            patch.object(type(handler.heat_pump), 'get_definitions', return_value=DEFINITIONS),
            patch('kronoterm2mqtt.mqtt_handler.get_modbus_client', return_value=handler.modbus_client),
            patch('kronoterm2mqtt.mqtt_handler.asyncio.sleep', side_effect=StopLoop), self.assertRaises(StopLoop)
        ):
            await handler.publish_loop()

        self.assertEqual(handler.sensors[2100][0].state, 17.7)

    async def test_closing_a_verbose_handler_stops_the_expander_too(self):
        handler = make_handler()
        handler.verbosity = 1
        handler.expander = MagicMock()

        with handler:
            pass

        handler.expander.stop.assert_called_once()


class VerboseConnectionTestCase(TestCase):
    """The TLS path prints what it is doing when asked."""

    def setUp(self):
        self.client = MagicMock()
        patches = [
            patch('paho.mqtt.client.Client', return_value=self.client),
            patch('socket.setdefaulttimeout'),
        ]
        for entry in patches:
            entry.start()
        self.addCleanup(lambda: [entry.stop() for entry in patches])

        self.user_settings = UserSettings()
        self.user_settings.mqtt.host = 'mqtt.example.com'
        self.user_settings.mqtt.port = 8883
        self.user_settings.mqtt.user_name = 'user'
        self.user_settings.mqtt.password = 'secret'
        self.user_settings.mqtt_tls.enabled = True

    def connect(self, address_info):
        from kronoterm2mqtt.mqtt_connection import get_connected_client

        with patch('socket.getaddrinfo', return_value=address_info):
            return get_connected_client(user_settings=self.user_settings, verbosity=2)

    def test_a_resolvable_host_connects(self):
        info = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 8883))]

        client = self.connect(info)

        self.assertIs(client, self.client)
        self.client.connect.assert_called_once()

    def test_a_host_that_resolves_to_nothing_is_reported(self):
        client = self.connect([])  # No addresses, but not an error either

        self.assertIs(client, self.client)


class ReadmeHistoryCommandTestCase(TestCase):
    def run_command(self, updated: bool, verbosity: int = 0) -> int:
        """The command exits 1 when it rewrote the README, 0 when it did not."""
        with (
            patch.object(history_cli, 'git_history', MagicMock()) as git_history,
            patch.object(history_cli, 'setup_logging'),
        ):
            git_history.update_readme_history.return_value = updated
            with self.assertRaises(SystemExit) as context:
                history_cli.update_readme_history(verbosity=verbosity)
        git_history.update_readme_history.assert_called_once()
        return context.exception.code

    def test_a_rewritten_readme_exits_one(self):
        self.assertEqual(self.run_command(updated=True), 1)

    def test_an_unchanged_readme_exits_zero(self):
        self.assertEqual(self.run_command(updated=False, verbosity=1), 0)
