"""The development commands that drive the ETERA board, without the board."""

import asyncio
from typing import ClassVar
from unittest import TestCase
from unittest.mock import patch

from kronoterm2mqtt.cli_dev import expander as expander_cli
from kronoterm2mqtt.pyetera_uart_bridge import EteraUartBridge
from kronoterm2mqtt.user_settings import UserSettings


class StopLoop(Exception):
    """Ends a command that would otherwise run forever."""


class FakeEtera:
    Direction = EteraUartBridge.Direction
    DeviceException = EteraUartBridge.DeviceException

    instances: ClassVar[list['FakeEtera']] = []

    def __init__(self, port=None, on_device_reset_handler=None, on_device_message_handler=None):
        self.port = port
        self.on_device_reset_handler = on_device_reset_handler
        self.on_device_message_handler = on_device_message_handler
        self.temperatures = [20.0] * 10
        self.sensors = [b'\x10\x20\x30']
        self.relays: dict[int, bool] = {}
        self.moves: list[tuple] = []
        self.temperature_error: Exception | None = None
        self.move_error: Exception | None = None
        FakeEtera.instances.append(self)

    async def run_forever(self):
        await asyncio.Event().wait()  # Cancelled by the command

    async def ready(self):
        return

    async def get_sensors(self):
        return self.sensors

    async def get_temperatures(self):
        if self.temperature_error:
            raise self.temperature_error
        return list(self.temperatures)

    async def set_relay(self, relay_id, state):
        self.relays[relay_id] = state

    async def move_motor(self, motor_id, direction, duration_ms):
        if self.move_error:
            raise self.move_error
        self.moves.append((motor_id, direction, duration_ms))


class ExpanderCliTestCase(TestCase):
    def setUp(self):
        FakeEtera.instances = []
        self.user_settings = UserSettings()
        patches = [
            patch.object(expander_cli, 'EteraUartBridge', FakeEtera),
            patch.object(expander_cli, 'get_user_settings', return_value=self.user_settings),
        ]
        for entry in patches:
            entry.start()
        self.addCleanup(lambda: [entry.stop() for entry in patches])

    @property
    def etera(self) -> FakeEtera:
        return FakeEtera.instances[-1]


class TemperaturesTestCase(ExpanderCliTestCase):
    def test_sensors_and_temperatures_are_read(self):
        expander_cli.expander_temperatures(verbosity=0)

        self.assertEqual(self.etera.port, self.user_settings.custom_expander.port)

    def test_a_device_error_is_reported_not_raised(self):
        with patch.object(FakeEtera, 'get_temperatures', side_effect=EteraUartBridge.DeviceException('no answer')):
            expander_cli.expander_temperatures(verbosity=0)  # Must not raise


class MotorsTestCase(ExpanderCliTestCase):
    def test_opening_moves_all_four_motors_clockwise(self):
        expander_cli.expander_motors(verbosity=0, opening=True, duration=12)

        self.assertEqual([move[0] for move in self.etera.moves], [0, 1, 2, 3])
        self.assertEqual({move[1] for move in self.etera.moves}, {EteraUartBridge.Direction.CLOCKWISE})
        self.assertEqual({move[2] for move in self.etera.moves}, {12_000})

    def test_closing_moves_them_the_other_way(self):
        expander_cli.expander_motors(verbosity=0, opening=False, duration=1)

        self.assertEqual({move[1] for move in self.etera.moves}, {EteraUartBridge.Direction.COUNTER_CLOCKWISE})

    def test_a_move_error_is_reported_not_raised(self):
        with patch.object(FakeEtera, 'move_motor', side_effect=EteraUartBridge.DeviceException('stuck')):
            expander_cli.expander_motors(verbosity=0, duration=1)  # Must not raise


class RelayTestCase(ExpanderCliTestCase):
    def test_the_selected_relay_is_switched(self):
        with patch.object(expander_cli.asyncio, 'sleep', new=fake_sleep):
            expander_cli.expander_relay(verbosity=0, relay=2, on=True)

        self.assertEqual(self.etera.relays, {2: True})

    def test_a_relay_error_is_reported_not_raised(self):
        with (
            patch.object(FakeEtera, 'set_relay', side_effect=EteraUartBridge.DeviceException('no answer')),
            patch.object(expander_cli.asyncio, 'sleep', new=fake_sleep),
        ):
            expander_cli.expander_relay(verbosity=0)  # Must not raise


class SolarLoopTestCase(ExpanderCliTestCase):
    def run_one_pass(self):
        """The loop never ends by itself; stop it in the sleep at the end of a pass."""
        with patch.object(expander_cli.asyncio, 'sleep', side_effect=StopLoop), self.assertRaises(StopLoop):
            expander_cli.expander_loop(verbosity=0)

    def test_a_warm_collector_starts_the_pump(self):
        settings = self.user_settings.custom_expander
        FakeEtera.instances = []
        with patch.object(FakeEtera, '__init__', warm_collector_init(settings)):
            self.run_one_pass()

        self.assertTrue(self.etera.relays[self.user_settings.custom_expander.solar_pump_relay_id])

    def test_a_cold_collector_stops_it(self):
        self.run_one_pass()  # All sensors at 20 °C, so the difference is 0 and below "off"

        self.assertFalse(self.etera.relays[self.user_settings.custom_expander.solar_pump_relay_id])

    def test_a_read_error_does_not_end_the_loop(self):
        with patch.object(FakeEtera, 'get_temperatures', side_effect=EteraUartBridge.DeviceException('no answer')):
            self.run_one_pass()  # Reaches the sleep, which is what raises StopLoop


async def fake_sleep(_seconds):
    return


def warm_collector_init(settings):
    original = FakeEtera.__init__

    def __init__(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.temperatures[settings.solar_sensors[0]] = 60.0  # collector
        self.temperatures[settings.solar_sensors[2]] = 20.0  # tank bottom

    return __init__
