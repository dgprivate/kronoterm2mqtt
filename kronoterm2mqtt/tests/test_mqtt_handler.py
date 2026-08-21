import asyncio
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from ha_services.mqtt4homeassistant.data_classes import NoState
from ha_services.mqtt4homeassistant.device import BaseMqttDevice
from pymodbus.exceptions import ConnectionException, ModbusIOException
from pymodbus.pdu import ExceptionResponse
from pymodbus.pdu.register_message import ReadHoldingRegistersResponse

from kronoterm2mqtt.health import HealthState
from kronoterm2mqtt.mqtt_handler import KronotermMqttHandler
from kronoterm2mqtt.user_settings import UserSettings


# A small stand-in for definitions/kronoterm_ksm.toml: one entry of every kind,
# with the register numbers one-based, as KRONOTERM documents them.
DEFINITIONS = {
    'connection': {'baudrate': 115200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1},
    'sensor': [
        {
            'register': 2101,
            'name': 'Outside temperature',
            'device_class': 'temperature',
            'state_class': 'measurement',
            'unit_of_measurement': '°C',
            'scale': 0.1,
        },
        {
            'register': 2132,
            'name': 'Current power consumption',
            'device_class': 'power',
            'state_class': 'measurement',
            'unit_of_measurement': 'W',
            'scale': 1,
        },
    ],
    'binary_sensor': [
        {'register': 2045, 'name': 'Loop 1 circulation pump status', 'device_class': 'running', 'bit': 0},
        {'register': 2045, 'name': 'Loop 2 circulation pump status', 'device_class': 'running', 'bit': 1},
    ],
    'enum_sensor': [
        {
            'register': 2001,
            'name': 'Working function',
            'options': [{'keys': [0, 5], 'values': ['heating', 'standby']}],
        },
    ],
    'switch': [{'register': 2012, 'name': 'System power'}],
    'select': [
        {
            'register': 2013,
            'name': 'Operating program selection',
            'default_option': 'auto',
            'options': [{'keys': [0, 1, 2], 'values': ['auto', 'ECO', 'comfort']}],
        },
    ],
}

# Addresses are the register numbers minus one:
OUTSIDE_TEMPERATURE = 2100
POWER_CONSUMPTION = 2131
PUMP_STATUS = 2044
WORKING_FUNCTION = 2000
SYSTEM_POWER = 2011
OPERATING_PROGRAM = 2012


class StopLoop(Exception):
    """Ends publish_loop() after one cycle."""


class FakeModbusClient:
    """Answers register reads from a dict and records writes."""

    def __init__(self, values: dict[int, int]):
        self.values = values
        self.writes: list[tuple[int, int]] = []
        self.closed = False

    def read_holding_registers(self, address, count, device_id):
        return ReadHoldingRegistersResponse(registers=[self.values.get(address + i, 0) for i in range(count)])

    def write_register(self, address, value, device_id):
        self.writes.append((address, value))
        return MagicMock(spec=[])  # Neither an ExceptionResponse nor a ModbusIOException

    def connect(self):
        return True

    def close(self):
        self.closed = True


def make_mqtt_client() -> MagicMock:
    """A paho client stand-in: ha_services unpacks what subscribe() returns."""
    client = MagicMock()
    client.subscribe.return_value = (0, 1)
    client.is_connected.return_value = True
    return client


def make_handler(registers: dict[int, int] | None = None, health: HealthState | None = None):
    """A handler with a mocked MQTT client, so nothing leaves the process."""
    user_settings = UserSettings()
    with patch('kronoterm2mqtt.mqtt_handler.get_connected_client', return_value=make_mqtt_client()):
        handler = KronotermMqttHandler(user_settings=user_settings, verbosity=0, health=health)
    handler.modbus_client = FakeModbusClient(registers or {})
    return handler


class InitDeviceTestCase(IsolatedAsyncioTestCase):
    def tearDown(self):
        BaseMqttDevice.device_uids = set()
        BaseMqttDevice.components = {}

    async def init_handler(self, **kwargs) -> KronotermMqttHandler:
        handler = make_handler(**kwargs)
        with patch.object(type(handler.heat_pump), 'get_definitions', return_value=DEFINITIONS):
            await handler.init_device()
        return handler

    async def test_definitions_become_components_at_zero_based_addresses(self):
        handler = await self.init_handler()

        self.assertEqual(sorted(handler.sensors), [OUTSIDE_TEMPERATURE, POWER_CONSUMPTION])
        self.assertEqual(sorted(handler.binary_sensors[PUMP_STATUS]), [0, 1])
        self.assertEqual(sorted(handler.enum_sensors), [WORKING_FUNCTION])
        self.assertEqual(sorted(handler.switches), [SYSTEM_POWER])
        self.assertEqual(sorted(handler.selects), [OPERATING_PROGRAM])

    async def test_scale_decides_the_display_precision(self):
        handler = await self.init_handler()

        temperature, scale = handler.sensors[OUTSIDE_TEMPERATURE]
        self.assertEqual(scale, Decimal('0.1'))
        self.assertEqual(temperature.suggested_display_precision, 1)

        power, scale = handler.sensors[POWER_CONSUMPTION]
        self.assertEqual(scale, Decimal(1))
        self.assertEqual(power.suggested_display_precision, 0)

    async def test_addresses_are_grouped_into_blocks(self):
        handler = await self.init_handler()

        # 2000, 2011, 2012, 2044, 2100, 2131 -> 2011 and 2012 are neighbours
        self.assertEqual(
            handler.address_ranges,
            [(2000, 2000), (2011, 2012), (2044, 2044), (2100, 2100), (2131, 2131)],
        )

    async def test_selects_keep_their_options(self):
        handler = await self.init_handler()

        select, options = handler.selects[OPERATING_PROGRAM]
        self.assertEqual(options['values'], ['auto', 'ECO', 'comfort'])
        self.assertEqual(select.options, ('auto', 'ECO', 'comfort'))


class PublishCycleTestCase(IsolatedAsyncioTestCase):
    def tearDown(self):
        BaseMqttDevice.device_uids = set()
        BaseMqttDevice.components = {}

    async def run_one_cycle(self, registers: dict[int, int], health: HealthState | None = None):
        handler = make_handler(registers=registers, health=health)
        with (
            patch.object(type(handler.heat_pump), 'get_definitions', return_value=DEFINITIONS),
            patch('kronoterm2mqtt.mqtt_handler.get_modbus_client', return_value=handler.modbus_client),
            patch('kronoterm2mqtt.mqtt_handler.asyncio.sleep', side_effect=StopLoop), self.assertRaises(StopLoop)
        ):
            await handler.publish_loop()
        return handler

    def published_state(self, handler, address, index=0):
        component = handler.sensors[address][index] if address in handler.sensors else None
        return component.state if component else None

    async def test_registers_are_scaled_and_published(self):
        handler = await self.run_one_cycle({OUTSIDE_TEMPERATURE: 177, POWER_CONSUMPTION: 1500})

        self.assertEqual(handler.sensors[OUTSIDE_TEMPERATURE][0].state, 17.7)
        self.assertEqual(handler.sensors[POWER_CONSUMPTION][0].state, 1500)

    async def test_negative_temperatures_survive_the_conversion(self):
        # Two's complement: 0xFFFF is -1 raw, so -0.1 °C
        handler = await self.run_one_cycle({OUTSIDE_TEMPERATURE: 0xFFFF - 99})

        self.assertAlmostEqual(handler.sensors[OUTSIDE_TEMPERATURE][0].state, -10.0)

    async def test_binary_sensors_read_their_own_bit(self):
        handler = await self.run_one_cycle({PUMP_STATUS: 0b10})

        loop_1 = handler.binary_sensors[PUMP_STATUS][0]
        loop_2 = handler.binary_sensors[PUMP_STATUS][1]
        self.assertEqual(loop_1.state, loop_1.OFF)
        self.assertEqual(loop_2.state, loop_2.ON)

    async def test_enum_sensor_publishes_the_mapped_value(self):
        handler = await self.run_one_cycle({WORKING_FUNCTION: 5})

        self.assertEqual(handler.enum_sensors[WORKING_FUNCTION][0].state, 'standby')

    async def test_value_missing_from_the_definitions_is_skipped(self):
        with self.assertLogs('kronoterm2mqtt.mqtt_handler', level='WARNING') as logs:
            handler = await self.run_one_cycle({WORKING_FUNCTION: 42})

        self.assertIn('not in the definitions', '\n'.join(logs.output))
        self.assertIsInstance(handler.enum_sensors[WORKING_FUNCTION][0].state, NoState)

    async def test_switch_and_select_publish_their_state(self):
        handler = await self.run_one_cycle({SYSTEM_POWER: 1, OPERATING_PROGRAM: 1})

        switch = handler.switches[SYSTEM_POWER]
        select = handler.selects[OPERATING_PROGRAM][0]
        self.assertEqual(switch.state, switch.ON)
        self.assertEqual(select.state, 'ECO')

    async def test_health_records_the_cycle(self):
        health = HealthState(stale_after_seconds=60)
        health.set_mqtt_client(MagicMock(is_connected=MagicMock(return_value=True)))

        await self.run_one_cycle({OUTSIDE_TEMPERATURE: 177}, health=health)

        state = health.as_dict()
        self.assertTrue(state['healthy'], state['problems'])
        self.assertEqual(state['sensors_published'], 7)  # Every component of the fixture
        self.assertTrue(state['modbus']['last_read_complete'])


class CallbackTestCase(IsolatedAsyncioTestCase):
    def tearDown(self):
        BaseMqttDevice.device_uids = set()
        BaseMqttDevice.components = {}

    async def make_initialised_handler(self):
        handler = make_handler()
        with patch.object(type(handler.heat_pump), 'get_definitions', return_value=DEFINITIONS):
            await handler.init_device()
        return handler

    async def test_switch_callback_writes_the_register(self):
        handler = await self.make_initialised_handler()
        switch = handler.switches[SYSTEM_POWER]

        handler.switch_callback(client=MagicMock(), component=switch, old_state='OFF', new_state='ON')

        self.assertEqual(handler.modbus_client.writes, [(SYSTEM_POWER, 1)])
        self.assertEqual(switch.state, switch.ON)

    async def test_select_callback_writes_the_register_value_of_the_option(self):
        handler = await self.make_initialised_handler()
        select = handler.selects[OPERATING_PROGRAM][0]

        handler.select_callback(client=MagicMock(), component=select, old_state='auto', new_state='comfort')

        self.assertEqual(handler.modbus_client.writes, [(OPERATING_PROGRAM, 2)])
        self.assertEqual(select.state, 'comfort')

    async def test_unknown_option_is_refused_without_writing(self):
        handler = await self.make_initialised_handler()
        select = handler.selects[OPERATING_PROGRAM][0]

        with self.assertLogs('kronoterm2mqtt.mqtt_handler', level='ERROR'):
            handler.select_callback(client=MagicMock(), component=select, old_state='auto', new_state='nonsense')

        self.assertEqual(handler.modbus_client.writes, [])

    async def test_closing_releases_both_clients(self):
        handler = await self.make_initialised_handler()
        mqtt_client = handler.mqtt_client

        with handler:
            pass

        self.assertTrue(handler.modbus_client.closed)
        mqtt_client.loop_stop.assert_called_once()
        mqtt_client.disconnect.assert_called_once()
        self.assertEqual(BaseMqttDevice.device_uids, set())


class RangesTestCase(TestCase):
    def test_consecutive_addresses_become_one_block(self):
        handler = object.__new__(KronotermMqttHandler)

        self.assertEqual(list(handler.ranges([1, 2, 3, 7, 8, 20])), [(1, 3), (7, 8), (20, 20)])


class ExpanderIntegrationTestCase(IsolatedAsyncioTestCase):
    """The publish loop feeds the ETERA expander from heat pump registers."""

    def tearDown(self):
        BaseMqttDevice.device_uids = set()
        BaseMqttDevice.components = {}

    async def run_cycle(self, registers, preset: dict[int, int] | None = None):
        handler = make_handler(registers=registers)
        # The expander reads registers outside the blocks this fixture defines:
        handler.registers.update(preset or {})
        handler.expander = MagicMock()
        handler.expander.init_device = AsyncMock()
        handler.expander.update_sensors_and_control = AsyncMock()
        with (
            patch.object(type(handler.heat_pump), 'get_definitions', return_value=DEFINITIONS),
            patch('kronoterm2mqtt.mqtt_handler.get_modbus_client', return_value=handler.modbus_client),
            patch('kronoterm2mqtt.mqtt_handler.asyncio.sleep', side_effect=StopLoop), self.assertRaises(StopLoop)
        ):
            await handler.publish_loop()
        return handler

    async def test_the_expander_is_updated_with_the_registers_it_needs(self):
        # 2044 is read from the device in this fixture, the rest is outside its blocks:
        expander_registers = {2102: 177, 2023: 450, 2015: 1, 2046: -20, 2043: 2}

        handler = await self.run_cycle({2000: 0, PUMP_STATUS: 1}, preset=expander_registers)

        handler.expander.init_device.assert_awaited_once()
        kwargs = handler.expander.update_sensors_and_control.await_args.kwargs
        self.assertAlmostEqual(kwargs['outside_temperature'], 17.7)
        self.assertAlmostEqual(kwargs['current_desired_dhw_temperature'], 45.0)
        self.assertTrue(kwargs['additional_source_enabled'])
        self.assertTrue(kwargs['loop_circulation_status'])
        self.assertAlmostEqual(kwargs['loop_temperature_offset_in_eco_mode'], -2.0)
        self.assertEqual(kwargs['loop_operation_status_on_schedule'], 2)
        self.assertEqual(kwargs['working_function'], 0)

    async def test_a_missing_register_skips_the_expander_update(self):
        handler = make_handler(registers={})
        handler.expander = MagicMock()
        handler.expander.init_device = AsyncMock()
        handler.expander.update_sensors_and_control = AsyncMock()

        with (
            patch.object(type(handler.heat_pump), 'get_definitions', return_value=DEFINITIONS),
            patch('kronoterm2mqtt.mqtt_handler.get_modbus_client', return_value=handler.modbus_client),
            patch('kronoterm2mqtt.mqtt_handler.asyncio.sleep', side_effect=StopLoop),
            self.assertLogs('kronoterm2mqtt.mqtt_handler', level='WARNING') as logs, self.assertRaises(StopLoop)
        ):
            await handler.publish_loop()

        self.assertIn('Skipping expander update', '\n'.join(logs.output))
        handler.expander.update_sensors_and_control.assert_not_awaited()


class WriteFailureTestCase(TestCase):
    def make_handler_with_client(self, client):
        handler = object.__new__(KronotermMqttHandler)
        handler.verbosity = 0
        handler.health = None
        handler.modbus_client = client
        return handler

    def test_an_io_error_is_retried_and_then_given_up_on(self):
        client = MagicMock()
        client.write_register.return_value = ModbusIOException('no answer')

        handler = self.make_handler_with_client(client)
        with patch('kronoterm2mqtt.mqtt_handler.time.sleep'):
            self.assertFalse(handler.write_register(address=2011, value=1))

        self.assertEqual(client.write_register.call_count, 3)

    def test_a_device_error_response_is_not_retried(self):
        client = MagicMock()
        client.write_register.return_value = ExceptionResponse(6, 2)

        handler = self.make_handler_with_client(client)
        self.assertFalse(handler.write_register(address=2011, value=1))

        self.assertEqual(client.write_register.call_count, 1)


class CallbackFailureTestCase(IsolatedAsyncioTestCase):
    """What the callbacks do when they cannot find or cannot write the register."""

    def tearDown(self):
        BaseMqttDevice.device_uids = set()
        BaseMqttDevice.components = {}

    async def make_handler(self):
        handler = make_handler()
        with patch.object(type(handler.heat_pump), 'get_definitions', return_value=DEFINITIONS):
            await handler.init_device()
        return handler

    async def test_an_unknown_switch_is_reported(self):
        handler = await self.make_handler()
        stranger = MagicMock(name='not-registered')

        with self.assertLogs('kronoterm2mqtt.mqtt_handler', level='ERROR') as logs:
            handler.switch_callback(client=MagicMock(), component=stranger, old_state='OFF', new_state='ON')

        self.assertIn('Could not find address', '\n'.join(logs.output))
        self.assertEqual(handler.modbus_client.writes, [])

    async def test_an_unknown_select_is_reported(self):
        handler = await self.make_handler()
        stranger = MagicMock(name='not-registered')

        with self.assertLogs('kronoterm2mqtt.mqtt_handler', level='ERROR') as logs:
            handler.select_callback(client=MagicMock(), component=stranger, old_state='auto', new_state='ECO')

        self.assertIn('Could not find address', '\n'.join(logs.output))

    async def test_a_failed_write_leaves_the_state_alone(self):
        handler = await self.make_handler()
        switch = handler.switches[SYSTEM_POWER]
        before = switch.state
        handler.modbus_client.write_register = MagicMock(return_value=ExceptionResponse(6, 2))

        with self.assertLogs('kronoterm2mqtt.mqtt_handler', level='ERROR') as logs:
            handler.switch_callback(client=MagicMock(), component=switch, old_state='OFF', new_state='ON')

        self.assertIn('Failed to write register', '\n'.join(logs.output))
        self.assertEqual(switch.state, before)

    async def test_a_failed_select_write_leaves_the_state_alone(self):
        handler = await self.make_handler()
        select = handler.selects[OPERATING_PROGRAM][0]
        handler.modbus_client.write_register = MagicMock(return_value=ExceptionResponse(6, 2))

        with self.assertLogs('kronoterm2mqtt.mqtt_handler', level='ERROR') as logs:
            handler.select_callback(client=MagicMock(), component=select, old_state='auto', new_state='ECO')

        self.assertIn('Failed to write register', '\n'.join(logs.output))


class ReconnectTestCase(TestCase):
    """reconnect_modbus() has to survive whatever the client does."""

    def make_handler_with(self, client):
        handler = object.__new__(KronotermMqttHandler)
        handler.verbosity = 1
        handler.health = None
        handler.modbus_client = client
        return handler

    def test_a_successful_reconnect_is_announced(self):
        client = MagicMock()
        client.connect.return_value = True

        self.assertTrue(self.make_handler_with(client).reconnect_modbus())
        client.close.assert_called_once()

    def test_a_client_that_does_not_come_back_is_reported(self):
        client = MagicMock()
        client.connect.return_value = False

        with self.assertLogs('kronoterm2mqtt.mqtt_handler', level='WARNING') as logs:
            self.assertFalse(self.make_handler_with(client).reconnect_modbus())

        self.assertIn('client is not connected', '\n'.join(logs.output))

    def test_an_error_while_closing_does_not_stop_the_reconnect(self):
        client = MagicMock()
        client.close.side_effect = OSError('already gone')
        client.connect.return_value = True

        self.assertTrue(self.make_handler_with(client).reconnect_modbus())

    def test_incomplete_reads_are_announced_once_per_cycle(self):
        handler = object.__new__(KronotermMqttHandler)
        handler.verbosity = 0
        handler.health = None
        handler.registers = {}
        handler.address_ranges = [(2100, 2100)]
        handler.modbus_client = MagicMock()
        handler.modbus_client.read_holding_registers.side_effect = ConnectionException('gone')

        with (
            patch('kronoterm2mqtt.mqtt_handler.MODBUS_RETRY_DELAY', 0),
            self.assertLogs('kronoterm2mqtt.mqtt_handler', level='WARNING'),
        ):
            complete = asyncio.run(handler.read_heat_pump_register_blocks())

        self.assertFalse(complete)
