"""Settings the heat pump accepts: the number component, and writing what it is given."""

from decimal import Decimal
import json
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import MagicMock, patch

from ha_services.exceptions import InvalidStateValue
from ha_services.mqtt4homeassistant.device import BaseMqttDevice, MqttDevice

from kronoterm2mqtt.number import Number
from kronoterm2mqtt.tests.test_mqtt_handler import DEFINITIONS, StopLoop, make_handler, make_mqtt_client


# One writable register of each kind the manual describes: a setpoint with a positive
# range, an offset that only goes negative, and a whole-degree correction.
NUMBER_DEFINITIONS = {
    **DEFINITIONS,
    'number': [
        {
            'register': 2023,
            'name': 'Desired DHW temperature',
            'device_class': 'temperature',
            'unit_of_measurement': '°C',
            'scale': 0.1,
            'min': 25.0,
            'max': 55.0,
            'step': 0.5,
        },
        {
            'register': 2030,
            'name': 'DHW ECO offset',
            'device_class': 'temperature',
            'unit_of_measurement': '°C',
            'scale': 0.1,
            'min': -10.0,
            'max': 0.0,
            'step': 0.5,
        },
        {
            'register': 2014,
            'name': 'System temperature correction',
            'device_class': 'temperature',
            'unit_of_measurement': '°C',
            'scale': 1,
            'min': -4,
            'max': 4,
            'step': 1,
        },
    ],
}

DHW_SETPOINT = 2022  # Register 2023, zero-based
DHW_ECO_OFFSET = 2029
SYSTEM_CORRECTION = 2013


def make_number(**kwargs) -> Number:
    # Its own device each time: ha_services refuses two components with one uid, and
    # these tests are about the component, not about the registry.
    device = MqttDevice(name='Heat Pump', uid=kwargs.pop('device_uid', 'kronoterm'))
    return Number(
        device=device,
        name=kwargs.pop('name', 'Desired DHW temperature'),
        uid=kwargs.pop('uid', 'desired_dhw_temperature'),
        min_value=kwargs.pop('min_value', 25.0),
        max_value=kwargs.pop('max_value', 55.0),
        **kwargs,
    )


class NumberComponentTestCase(TestCase):
    def tearDown(self):
        BaseMqttDevice.device_uids = set()
        BaseMqttDevice.components = {}

    def test_the_discovery_config_tells_home_assistant_the_limits(self):
        number = make_number(step=0.5, unit_of_measurement='°C', device_class='temperature')

        payload = number.get_config().payload

        self.assertEqual(payload['component'], 'number')
        self.assertEqual(payload['min'], 25.0)
        self.assertEqual(payload['max'], 55.0)
        self.assertEqual(payload['step'], 0.5)
        self.assertEqual(payload['mode'], 'box')
        self.assertEqual(payload['unit_of_measurement'], '°C')
        self.assertEqual(payload['device_class'], 'temperature')
        self.assertTrue(payload['command_topic'].endswith('/command'))
        self.assertTrue(payload['state_topic'].endswith('/state'))

    def test_a_config_without_a_unit_leaves_the_key_out(self):
        payload = make_number().get_config().payload

        self.assertNotIn('unit_of_measurement', payload)
        self.assertNotIn('device_class', payload)
        self.assertNotIn('suggested_display_precision', payload)

    def test_a_value_outside_the_range_is_refused(self):
        number = make_number()

        number.set_state(30.0)
        self.assertEqual(number.get_state().payload, 30.0)

        for refused in (24.9, 55.1, 'warm', True, None):
            with self.subTest(state=refused), self.assertRaises(InvalidStateValue):
                number.set_state(refused)

    def test_limits_that_are_the_wrong_way_round_are_a_mistake_in_the_definitions(self):
        with self.assertRaises(AssertionError):
            make_number(min_value=55.0, max_value=25.0)

    def test_a_command_reaches_the_callback_as_a_number(self):
        seen = []
        number = make_number(callback=lambda **kwargs: seen.append(kwargs['new_state']))

        number._command_callback(MagicMock(), None, MagicMock(payload=b'47.5'))

        self.assertEqual(seen, [47.5])

    def test_a_command_that_is_not_a_number_is_dropped(self):
        """The topic is open to anything on the broker; a heat pump setting is no place to guess."""
        seen = []
        number = make_number(callback=lambda **kwargs: seen.append(kwargs['new_state']))

        for payload in (b'', b'warm', b'4,5', b'\xff\xfe'):
            with self.subTest(payload=payload):
                number._command_callback(MagicMock(), None, MagicMock(payload=payload))

        self.assertEqual(seen, [])

    def test_a_precision_is_passed_on_when_there_is_one(self):
        payload = make_number(suggested_display_precision=1).get_config().payload

        self.assertEqual(payload['suggested_display_precision'], 1)

    def test_without_a_handler_the_component_just_keeps_the_value(self):
        """The default callback: useful on its own, and what ha-services does elsewhere."""
        number = make_number()
        client = make_mqtt_client()

        number._command_callback(client, None, MagicMock(payload=b'42'))

        self.assertEqual(number.state, 42.0)
        client.publish.assert_called_once()

    def test_publishing_the_config_subscribes_to_the_command_topic(self):
        number = make_number()
        client = make_mqtt_client()

        number.publish_config(client)

        client.subscribe.assert_called_once_with(number.command_topic)
        client.message_callback_add.assert_called_once()


class NumbersFromDefinitionsTestCase(IsolatedAsyncioTestCase):
    def tearDown(self):
        BaseMqttDevice.device_uids = set()
        BaseMqttDevice.components = {}

    async def init_handler(self, registers: dict[int, int] | None = None):
        handler = make_handler(registers=registers)
        with patch.object(type(handler.heat_pump), 'get_definitions', return_value=NUMBER_DEFINITIONS):
            await handler.init_device()
        return handler

    async def test_the_definitions_become_numbers_at_zero_based_addresses(self):
        handler = await self.init_handler()

        self.assertEqual(sorted(handler.numbers), [SYSTEM_CORRECTION, DHW_SETPOINT, DHW_ECO_OFFSET])

        number, scale = handler.numbers[DHW_SETPOINT]
        self.assertEqual(scale, Decimal('0.1'))
        self.assertEqual((number.min_value, number.max_value, number.step), (25.0, 55.0, 0.5))
        self.assertEqual(number.suggested_display_precision, 1)

    async def test_their_registers_are_read_with_the_rest(self):
        handler = await self.init_handler()

        addresses = [address for start, end in handler.address_ranges for address in range(start, end + 1)]
        for address in (DHW_SETPOINT, DHW_ECO_OFFSET, SYSTEM_CORRECTION):
            self.assertIn(address, addresses)

    async def test_a_change_in_home_assistant_is_written_to_the_register(self):
        handler = await self.init_handler()
        number, _ = handler.numbers[DHW_SETPOINT]

        handler.number_callback(client=handler.mqtt_client, component=number, old_state=45.0, new_state=47.5)

        self.assertEqual(handler.modbus_client.writes, [(DHW_SETPOINT, 475)])
        self.assertEqual(number.state, 47.5)

    async def test_a_negative_setting_is_written_in_twos_complement(self):
        """The ECO offsets only go down, and the heat pump reads them back the same way."""
        handler = await self.init_handler()
        number, _ = handler.numbers[DHW_ECO_OFFSET]

        handler.number_callback(client=handler.mqtt_client, component=number, old_state=0.0, new_state=-2.5)

        self.assertEqual(handler.modbus_client.writes, [(DHW_ECO_OFFSET, 0x10000 - 25)])

    async def test_a_whole_degree_setting_is_not_scaled(self):
        handler = await self.init_handler()
        number, _ = handler.numbers[SYSTEM_CORRECTION]

        handler.number_callback(client=handler.mqtt_client, component=number, old_state=0, new_state=3)

        self.assertEqual(handler.modbus_client.writes, [(SYSTEM_CORRECTION, 3)])

    async def test_a_value_outside_the_range_never_reaches_the_heat_pump(self):
        handler = await self.init_handler()
        number, _ = handler.numbers[DHW_SETPOINT]
        number.set_state(45.0)

        handler.number_callback(client=handler.mqtt_client, component=number, old_state=45.0, new_state=80.0)

        self.assertEqual(handler.modbus_client.writes, [])
        self.assertEqual(number.state, 45.0)  # And Home Assistant is told what it still is

    async def test_a_failed_write_leaves_the_value_alone(self):
        handler = await self.init_handler()
        number, _ = handler.numbers[DHW_SETPOINT]
        number.set_state(45.0)

        with patch.object(handler, 'write_register', return_value=False):
            handler.number_callback(client=handler.mqtt_client, component=number, old_state=45.0, new_state=50.0)

        self.assertEqual(number.state, 45.0)

    async def test_a_number_that_is_not_registered_is_reported_not_written(self):
        handler = await self.init_handler()
        stray = make_number(device_uid='elsewhere', uid='not_ours')

        handler.number_callback(client=handler.mqtt_client, component=stray, old_state=1.0, new_state=2.0)

        self.assertEqual(handler.modbus_client.writes, [])

    async def test_a_publish_cycle_reports_what_the_heat_pump_has(self):
        registers = {DHW_SETPOINT: 470, DHW_ECO_OFFSET: 0x10000 - 30, SYSTEM_CORRECTION: 2}
        handler = make_handler(registers=registers)

        with (
            patch.object(type(handler.heat_pump), 'get_definitions', return_value=NUMBER_DEFINITIONS),
            patch('kronoterm2mqtt.mqtt_handler.get_modbus_client', return_value=handler.modbus_client),
            patch('kronoterm2mqtt.mqtt_handler.asyncio.sleep', side_effect=StopLoop),
            self.assertRaises(StopLoop),
        ):
            await handler.publish_loop()

        states = {number.name: number.state for number, _ in handler.numbers.values()}
        self.assertEqual(states['Desired DHW temperature'], 47.0)
        self.assertEqual(states['DHW ECO offset'], -3.0)  # Two's complement, read back as a negative offset
        self.assertEqual(states['System temperature correction'], 2.0)


class ShippedDefinitionsTestCase(TestCase):
    """The registers we let Home Assistant write are the ones the manual marks RW."""

    def setUp(self):
        from kronoterm2mqtt.user_settings import HeatPump

        self.definitions = HeatPump().get_definitions(verbosity=0)

    def test_every_number_has_the_limits_a_number_needs(self):
        for parameter in self.definitions['number'] + self.definitions['number_disabled']:
            with self.subTest(register=parameter['register']):
                self.assertLess(parameter['min'], parameter['max'])
                self.assertGreater(parameter['step'], 0)
                self.assertIn('scale', parameter)

    def test_no_register_is_both_a_reading_and_a_setting(self):
        numbers = {parameter['register'] for parameter in self.definitions['number']}
        sensors = {parameter['register'] for parameter in self.definitions['sensor']}

        self.assertEqual(numbers & sensors, set(), 'Home Assistant would show two entities for one register')

    def test_the_setpoints_from_the_manual_are_settable(self):
        numbers = {parameter['register'] for parameter in self.definitions['number']}

        # MA_2023 sanitary water, MA_2032 buffer, MA_2187 loop 1, MA_2014 system correction
        for register in (2023, 2032, 2187, 2014):
            self.assertIn(register, numbers)

    def test_the_dhw_setpoint_keeps_the_range_the_manual_gives(self):
        (dhw,) = [p for p in self.definitions['number'] if p['register'] == 2023]

        self.assertEqual((dhw['min'], dhw['max']), (25.0, 55.0))

    def test_the_definitions_stay_json_serialisable(self):
        """print-values and the tests read them; a stray type here breaks both."""
        json.dumps(self.definitions['number'])
