import asyncio
import time
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from ha_services.mqtt4homeassistant.components.switch import Switch
from ha_services.mqtt4homeassistant.device import BaseMqttDevice, MqttDevice

from kronoterm2mqtt.constants import MIXING_VALVE_HOLD_TIME
from kronoterm2mqtt.expander import ExpanderMqttHandler
from kronoterm2mqtt.pyetera_uart_bridge import EteraUartBridge
from kronoterm2mqtt.user_settings import UserSettings


class FakeEtera:
    """The Arduino expander, without an Arduino.

    Keeps the real Direction and DeviceException, because the module under test
    reaches for them through this class.
    """

    Direction = EteraUartBridge.Direction
    DeviceException = EteraUartBridge.DeviceException

    def __init__(self, port=None, on_device_reset_handler=None, on_device_message_handler=None):
        self.port = port
        self._s = MagicMock()
        self.temperatures = [20.0] * 10
        self.relays: dict[int, bool] = {}
        self.moves: list[tuple[int, object, int, bool]] = []

    async def run_forever(self):
        return  # A real bridge never returns; a test wants its task to finish

    async def ready(self):
        return

    async def get_temperatures(self):
        return list(self.temperatures)

    async def set_relay(self, relay_id: int, state: bool):
        self.relays[relay_id] = state

    async def move_motor(self, motor_id, direction, duration_ms, override=False):
        self.moves.append((motor_id, direction, duration_ms, override))


def make_mqtt_client() -> MagicMock:
    client = MagicMock()
    client.subscribe.return_value = (0, 1)
    return client


class ExpanderTestCase(IsolatedAsyncioTestCase):
    """Every test gets an initialised expander talking to FakeEtera."""

    async def asyncSetUp(self):
        self.user_settings = UserSettings()
        self.user_settings.custom_expander.module_enabled = True
        self.mqtt_client = make_mqtt_client()
        self.handler = ExpanderMqttHandler(
            mqtt_client=self.mqtt_client, user_settings=self.user_settings, verbosity=0
        )
        self.main_device = MqttDevice(name='Heat Pump', uid='kronoterm', manufacturer='KRONOTERM')

        with patch('kronoterm2mqtt.expander.EteraUartBridge', FakeEtera):
            await self.handler.init_device(self.main_device)
        self.etera = self.handler.etera
        # init_device schedules an initial "close every valve" move:
        await asyncio.sleep(0)
        self.etera.moves.clear()

    async def asyncTearDown(self):
        await asyncio.sleep(0)  # Let scheduled motor tasks finish
        BaseMqttDevice.device_uids = set()
        BaseMqttDevice.components = {}

    def settings(self):
        return self.user_settings.custom_expander

    def expire_valve_hold_time(self):
        """Pretend the valves have been standing still long enough to move again."""
        expired = time.monotonic() - MIXING_VALVE_HOLD_TIME - 1
        self.handler.mixing_valve_timer = [expired] * len(self.handler.mixing_valve_timer)

    async def control(self, **kwargs):
        defaults = dict(
            outside_temperature=0.0,
            current_desired_dhw_temperature=45.0,
            additional_source_enabled=False,
            loop_circulation_status=True,
            loop_temperature_offset_in_eco_mode=0.0,
            loop_operation_status_on_schedule=1,
            working_function=0,
        )
        defaults.update(kwargs)
        await self.handler.update_sensors_and_control(**defaults)
        # The control code schedules motor moves as tasks; let them run
        for _ in range(5):
            await asyncio.sleep(0)


class InitDeviceTestCase(ExpanderTestCase):
    def test_a_sensor_exists_for_every_configured_thermometer(self):
        self.assertEqual(len(self.handler.sensors), len(self.settings().sensor_names))

    def test_unused_relay_names_leave_a_gap(self):
        # An empty name means "no relay here", and the list keeps the position
        for index, name in enumerate(self.settings().relay_names):
            if name:
                self.assertIsNotNone(self.handler.relays[index], index)
            else:
                self.assertIsNone(self.handler.relays[index], index)

    def test_loops_with_a_relay_get_a_selector_and_a_valve_sensor(self):
        expected = sum(1 for name in self.settings().relay_names[:4] if name)
        self.assertEqual(len(self.handler.loop_states), expected)
        self.assertEqual(len(self.handler.mixing_valve_sensors), expected)

    def test_valves_are_closed_on_startup(self):
        for sensor in self.handler.mixing_valve_sensors:
            self.assertEqual(sensor.state, 0)

    def test_stop_closes_the_serial_port(self):
        self.handler.stop()
        self.etera._s.close.assert_called_once()


class MixingValveTestCase(ExpanderTestCase):
    async def test_opening_moves_the_motor_clockwise_and_raises_the_position(self):
        await self.handler.mixing_valve_motor_open(0, 60)

        motor_id, direction, duration_ms, _override = self.etera.moves[-1]
        self.assertEqual((motor_id, duration_ms), (0, 60_000))
        self.assertEqual(direction, EteraUartBridge.Direction.CLOCKWISE)
        self.assertEqual(self.handler.mixing_valve_sensors[0].state, 50.0)  # 60 s of the 120 s range

    async def test_closing_moves_the_motor_the_other_way(self):
        await self.handler.mixing_valve_motor_open(0, 120)
        await self.handler.mixing_valve_motor_close(0, 60)

        _motor_id, direction, _duration_ms, _override = self.etera.moves[-1]
        self.assertEqual(direction, EteraUartBridge.Direction.COUNTER_CLOCKWISE)
        self.assertEqual(self.handler.mixing_valve_sensors[0].state, 50.0)

    async def test_the_position_stays_between_0_and_100(self):
        await self.handler.mixing_valve_motor_open(0, 600)
        self.assertEqual(self.handler.mixing_valve_sensors[0].state, 100)

        await self.handler.mixing_valve_motor_close(0, 600)
        self.assertEqual(self.handler.mixing_valve_sensors[0].state, 0)

    async def test_a_device_error_does_not_escape(self):
        self.etera.move_motor = MagicMock(side_effect=EteraUartBridge.DeviceException('no answer'))

        await self.handler.mixing_valve_motor_open(0, 10)  # Must not raise

    async def test_an_unknown_motor_is_reported_not_raised(self):
        await self.handler.mixing_valve_motor_close(99, 10)  # Must not raise


class TargetTemperatureTestCase(ExpanderTestCase):
    def target(self, **kwargs):
        defaults = dict(
            loop_number=0,
            temp_at_zero=25.0,
            outside_temperature=0.0,
            heating_curve_coefficient=0.25,
            loop_temperature_offset_in_eco_mode=0.0,
            loop_operation_status_on_schedule=1,
        )
        defaults.update(kwargs)
        return self.handler.get_loop_target_temperature(**defaults)

    def test_colder_outside_means_a_warmer_loop(self):
        self.assertEqual(self.target(outside_temperature=0.0), 25.0)
        self.assertEqual(self.target(outside_temperature=-10.0), 27.5)  # 25 + 10 * 0.25
        self.assertEqual(self.target(outside_temperature=20.0), 20.0)

    def test_eco_schedule_applies_the_offset(self):
        self.assertEqual(
            self.target(loop_operation_status_on_schedule=2, loop_temperature_offset_in_eco_mode=-2.0),
            23.0,
        )

    def test_expedited_and_standby_override_the_curve(self):
        self.handler.loop_states[0].set_state(self.handler.WorkingMode.EXPEDITED.value)
        self.assertEqual(self.target(outside_temperature=-20.0), 31.0)

        self.handler.loop_states[0].set_state(self.handler.WorkingMode.STANDBY.value)
        self.assertEqual(self.target(outside_temperature=-20.0), 10.0)


class SolarPumpTestCase(ExpanderTestCase):
    def set_temperatures(self, collector: float, tank_bottom: float):
        settings = self.settings()
        self.etera.temperatures[settings.solar_sensors[0]] = collector
        self.etera.temperatures[settings.solar_sensors[2]] = tank_bottom

    async def test_pump_starts_when_the_collector_is_warmer_than_the_tank(self):
        self.set_temperatures(collector=40.0, tank_bottom=20.0)  # difference 20 > 8

        await self.control()

        self.assertTrue(self.etera.relays[self.settings().solar_pump_relay_id])

    async def test_pump_stops_once_the_difference_collapses(self):
        self.set_temperatures(collector=40.0, tank_bottom=20.0)
        await self.control()
        self.set_temperatures(collector=21.0, tank_bottom=20.0)  # difference 1 < 3

        await self.control()

        self.assertFalse(self.etera.relays[self.settings().solar_pump_relay_id])

    async def test_frost_protection_runs_the_pump(self):
        self.set_temperatures(collector=-20.0, tank_bottom=20.0)

        await self.control()

        self.assertTrue(self.etera.relays[self.settings().solar_pump_relay_id])


class LoopControlTestCase(ExpanderTestCase):
    async def test_running_circulation_switches_the_loop_pumps_on(self):
        await self.control(loop_circulation_status=True)

        for loop, relay in enumerate(self.handler.relays[:4]):
            if relay is not None:
                self.assertTrue(self.etera.relays.get(loop), loop)

    async def test_stopped_circulation_switches_them_off_again(self):
        await self.control(loop_circulation_status=True)

        await self.control(loop_circulation_status=False)

        for loop, relay in enumerate(self.handler.relays[:4]):
            if relay is not None:
                self.assertFalse(self.etera.relays.get(loop), loop)

    async def test_an_overheated_loop_is_shut_down(self):
        self.etera.temperatures[self.settings().loop_sensors[0]] = 45.0  # Over the 40 °C limit

        await self.control()

        self.assertEqual(self.handler.loop_states[0].state, self.handler.WorkingMode.OFF.value)
        self.assertFalse(self.etera.relays[0])

    async def test_start_of_heating_closes_the_valves_first(self):
        self.handler.last_working_function = 5  # Standby ...

        await self.control(working_function=0)  # ... and now heating

        self.assertTrue(any(move[0] == 0 for move in self.etera.moves))
        self.assertEqual(self.handler.last_working_function, 0)

    async def test_a_cold_loop_opens_its_valve(self):
        self.handler.last_working_function = 0  # No start-of-heating special case
        self.expire_valve_hold_time()
        self.etera.temperatures[self.settings().loop_sensors[0]] = 18.0  # Below the 25 °C target

        await self.control(outside_temperature=0.0)

        directions = [move[1] for move in self.etera.moves if move[0] == 0]
        self.assertIn(EteraUartBridge.Direction.CLOCKWISE, directions)

    async def test_a_warm_loop_closes_its_valve(self):
        self.handler.last_working_function = 0
        self.expire_valve_hold_time()
        self.etera.temperatures[self.settings().loop_sensors[0]] = 30.0  # Above the 25 °C target

        await self.control(outside_temperature=0.0)

        directions = [move[1] for move in self.etera.moves if move[0] == 0]
        self.assertIn(EteraUartBridge.Direction.COUNTER_CLOCKWISE, directions)

    async def test_a_disabled_loop_keeps_its_pump_off(self):
        self.handler.loop_states[0].set_state(self.handler.WorkingMode.OFF.value)

        await self.control(loop_circulation_status=True)

        self.assertNotEqual(self.etera.relays.get(0), True)


class IntertankTestCase(ExpanderTestCase):
    async def test_the_switch_drives_the_intertank_pump(self):
        relay_id = self.settings().inter_tank_pump_relay_id
        self.handler.switch_intertank.set_state(Switch.ON)

        await self.control()

        self.assertTrue(self.etera.relays[relay_id])

        self.handler.switch_intertank.set_state(Switch.OFF)
        await self.control()

        self.assertFalse(self.etera.relays[relay_id])


class LoopSwitchCallbackTestCase(ExpanderTestCase):
    async def test_expedited_mode_starts_a_timer(self):
        select = self.handler.loop_states[0]

        self.handler.loop_switch_callback(
            client=self.mqtt_client, component=select, old_state='Vklop', new_state='Komfortno'
        )

        self.assertEqual(select.state, 'Komfortno')
        self.assertIsNotNone(self.handler.expedited_heating_timer[0])

    async def test_switching_off_clears_the_timer(self):
        select = self.handler.loop_states[0]
        self.handler.loop_switch_callback(
            client=self.mqtt_client, component=select, old_state='Vklop', new_state='Komfortno'
        )

        self.handler.loop_switch_callback(
            client=self.mqtt_client, component=select, old_state='Komfortno', new_state='Izklop'
        )

        self.assertIsNone(self.handler.expedited_heating_timer[0])

    async def test_an_unknown_mode_is_logged(self):
        select = self.handler.loop_states[0]

        with self.assertLogs('kronoterm2mqtt.expander', level='ERROR'):
            self.handler.loop_switch_callback(
                client=self.mqtt_client, component=select, old_state='Vklop', new_state='nonsense'
            )

    async def test_expedited_heating_ends_after_five_hours(self):
        select = self.handler.loop_states[0]
        self.handler.loop_switch_callback(
            client=self.mqtt_client, component=select, old_state='Vklop', new_state='Komfortno'
        )
        self.handler.expedited_heating_timer[0] -= 5 * 3600 + 1  # Pretend it started five hours ago

        await self.control()

        self.assertEqual(select.state, self.handler.WorkingMode.OFF.value)
        self.assertIsNone(self.handler.expedited_heating_timer[0])


class ExpanderErrorPathTestCase(ExpanderTestCase):
    """What happens when the board answers with an error, or not at all."""

    async def test_a_device_error_during_a_cycle_is_raised_after_logging(self):
        self.etera.get_temperatures = MagicMock(side_effect=EteraUartBridge.DeviceException('no answer'))

        with (
            self.assertLogs('kronoterm2mqtt.expander', level='ERROR'),
            self.assertRaises(EteraUartBridge.DeviceException),
        ):
            await self.control()

    async def test_an_invalid_state_is_logged_and_the_cycle_ends_quietly(self):
        # A temperature outside the sensor range makes ha_services refuse the state
        self.etera.temperatures = [999.0] * 10

        with self.assertLogs('kronoterm2mqtt.expander', level='WARNING') as logs:
            await self.control()

        self.assertIn('invalid state', '\n'.join(logs.output).lower())

    async def test_relays_are_published_even_after_a_refused_state(self):
        self.etera.temperatures = [999.0] * 10

        with self.assertLogs('kronoterm2mqtt.expander', level='WARNING'):
            await self.control()

        for relay in self.handler.relays:
            if relay is not None:
                self.assertIsNotNone(relay.state)

    async def test_a_loop_that_is_off_closes_its_valve_when_circulation_stops(self):
        self.handler.loop_states[0].set_state(self.handler.WorkingMode.OFF.value)
        await self.control(loop_circulation_status=True)  # Pumps on for the other loops
        self.etera.moves.clear()

        await self.control(loop_circulation_status=False)

        self.assertFalse(self.etera.relays.get(0, False))

    async def test_verbose_control_reports_what_it_decided(self):
        self.handler.verbosity = 2
        self.expire_valve_hold_time()
        self.handler.last_working_function = 0

        await self.control()  # Must not raise with the extra output enabled

        self.assertIsNotNone(self.handler.mixing_valve_sensors[0].state)
