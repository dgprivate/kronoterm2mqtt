from typing import ClassVar
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ha_services.exceptions import InvalidStateValue

from kronoterm2mqtt.cli_app import publish_loop as publish_loop_module
from kronoterm2mqtt.user_settings import UserSettings


class FakeHandler:
    """Stands in for KronotermMqttHandler: records how it was built, then fails."""

    instances: ClassVar[list['FakeHandler']] = []

    def __init__(self, user_settings, verbosity, health=None):
        self.user_settings = user_settings
        self.verbosity = verbosity
        self.health = health
        self.exited = False
        FakeHandler.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.exited = True

    async def publish_loop(self):
        raise KeyboardInterrupt  # Ends the command's while loop


class TestMqttConnectionCommandTestCase(TestCase):
    def test_the_command_connects_and_closes_again(self):
        client = MagicMock()
        user_settings = UserSettings()

        with (
            patch.object(publish_loop_module, 'get_user_settings', return_value=user_settings),
            patch.object(publish_loop_module, 'get_connected_client', return_value=client) as connect,
        ):
            publish_loop_module.test_mqtt_connection(verbosity=0)

        connect.assert_called_once()
        client.loop_start.assert_called_once()
        client.loop_stop.assert_called_once()
        client.disconnect.assert_called_once()


class PublishLoopCommandTestCase(TestCase):
    def setUp(self):
        FakeHandler.instances = []
        self.user_settings = UserSettings()

    def run_command(self, handler=FakeHandler):
        with (
            patch.object(publish_loop_module, 'get_user_settings', return_value=self.user_settings),
            patch.object(publish_loop_module, 'KronotermMqttHandler', handler),
            patch.object(publish_loop_module, 'HealthServer') as server,
            patch.object(publish_loop_module, 'HealthWatchdog') as watchdog,
            patch.object(publish_loop_module.time, 'sleep'),
        ):
            try:
                publish_loop_module.publish_loop(verbosity=0)
            except KeyboardInterrupt:
                pass
        return server, watchdog

    def test_the_health_endpoint_and_watchdog_are_started(self):
        server, watchdog = self.run_command()

        server.assert_called_once()
        self.assertEqual(server.call_args.kwargs['port'], self.user_settings.health.port)
        server.return_value.start.assert_called_once()
        watchdog.return_value.start.assert_called_once()
        self.assertEqual(
            watchdog.call_args.kwargs['restart_after_seconds'],
            self.user_settings.health.restart_after_seconds,
        )

    def test_the_handler_is_given_the_same_health_state(self):
        server, _watchdog = self.run_command()

        handler = FakeHandler.instances[0]
        self.assertIs(handler.health, server.call_args.kwargs['state'])
        self.assertEqual(handler.health.modbus_port, self.user_settings.heat_pump.port)
        self.assertTrue(handler.exited)

    def test_nothing_is_started_when_the_endpoint_is_disabled(self):
        self.user_settings.health.enabled = False

        server, watchdog = self.run_command()

        server.assert_not_called()
        watchdog.assert_not_called()

    def test_a_device_problem_is_logged_and_the_loop_starts_over(self):
        class FailingHandler(FakeHandler):
            calls = 0

            async def publish_loop(self):
                FailingHandler.calls += 1
                if FailingHandler.calls == 1:
                    raise InvalidStateValue(component=MagicMock(), error_msg='USB gone')
                raise KeyboardInterrupt

        with self.assertLogs('kronoterm2mqtt.cli_app.publish_loop', level='ERROR'):
            self.run_command(handler=FailingHandler)

        self.assertEqual(FailingHandler.calls, 2)

    def test_an_unexpected_error_ends_the_process(self):
        class BrokenHandler(FakeHandler):
            async def publish_loop(self):
                raise RuntimeError('something unforeseen')

        with (
            patch.object(publish_loop_module, 'get_user_settings', return_value=self.user_settings),
            patch.object(publish_loop_module, 'KronotermMqttHandler', BrokenHandler),
            patch.object(publish_loop_module, 'HealthServer'),
            patch.object(publish_loop_module, 'HealthWatchdog'),
            self.assertRaises(SystemExit) as context,
        ):
            publish_loop_module.publish_loop(verbosity=0)

        self.assertEqual(context.exception.code, 1)
