import io
from unittest import TestCase
from unittest.mock import patch

from pymodbus.pdu import ExceptionResponse
from pymodbus.pdu.register_message import ReadHoldingRegistersResponse
from rich.console import Console

from kronoterm2mqtt.cli_app import print_values as print_values_module
from kronoterm2mqtt.tests.test_mqtt_handler import DEFINITIONS
from kronoterm2mqtt.user_settings import UserSettings


class FakeModbusClient:
    """Answers every read from a dict of register values."""

    def __init__(self, values: dict[int, int], error: bool = False):
        self.values = values
        self.error = error
        self.reads: list[int] = []

    def read_holding_registers(self, address, count, device_id):
        self.reads.append(address)
        if self.error:
            return ExceptionResponse(3, 2)  # Illegal data address
        return ReadHoldingRegistersResponse(registers=[self.values.get(address + i, 0) for i in range(count)])


def capture(func, *args, **kwargs) -> str:
    """Run something that prints with rich and return what it wrote."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=200, no_color=True, highlight=False)
    with patch.object(print_values_module, 'print', console.print):
        func(*args, **kwargs)
    return buffer.getvalue()


class PrintHelpersTestCase(TestCase):
    def test_sensor_values_are_scaled_and_signed(self):
        client = FakeModbusClient({2100: 0xFFFF - 99})  # -100 raw, scale 0.1

        output = capture(
            print_values_module.print_parameter_values, client, DEFINITIONS['sensor'], verbosity=0
        )

        self.assertIn('Outside temperature', output)
        self.assertIn('-10.0', output)
        self.assertIn('°C', output)

    def test_verbose_output_names_the_register(self):
        client = FakeModbusClient({2100: 200})

        output = capture(
            print_values_module.print_parameter_values, client, DEFINITIONS['sensor'], verbosity=1
        )

        self.assertIn('Register dec: 2100', output)
        self.assertIn('hex: 0834', output)

    def test_an_error_response_is_reported_not_raised(self):
        client = FakeModbusClient({}, error=True)

        output = capture(
            print_values_module.print_parameter_values, client, DEFINITIONS['sensor'], verbosity=0
        )

        self.assertIn('Error:', output)

    def test_binary_sensors_show_the_state_of_their_bit(self):
        client = FakeModbusClient({2044: 0b10})

        output = capture(
            print_values_module.print_binary_sensor_values, client, DEFINITIONS['binary_sensor'], verbosity=1
        )

        lines = [line for line in output.splitlines() if 'circulation pump' in line]
        self.assertIn('OFF', lines[0])  # bit 0 is not set
        self.assertIn('ON', lines[1])  # bit 1 is
        self.assertIn('raw: 2', lines[0])

    def test_enum_sensors_show_the_mapped_name(self):
        client = FakeModbusClient({2000: 5})

        output = capture(
            print_values_module.print_enum_sensor_values, client, DEFINITIONS['enum_sensor'], verbosity=0
        )

        self.assertIn('standby', output)

    def test_switches_show_on_or_off(self):
        client = FakeModbusClient({2011: 1})

        output = capture(print_values_module.print_switch_values, client, DEFINITIONS['switch'], verbosity=0)

        self.assertIn('ON', output)

    def test_selects_show_the_selected_option(self):
        client = FakeModbusClient({2012: 2})

        output = capture(print_values_module.print_select_values, client, DEFINITIONS['select'], verbosity=0)

        self.assertIn('comfort', output)


class CommandTestCase(TestCase):
    def setUp(self):
        self.client = FakeModbusClient({2100: 177, 2044: 1, 2000: 0, 2011: 1, 2012: 0})
        user_settings = UserSettings()
        self.patches = [
            patch.object(print_values_module, 'get_user_settings', return_value=user_settings),
            patch.object(type(user_settings.heat_pump), 'get_definitions', return_value=DEFINITIONS),
            patch.object(print_values_module, 'get_modbus_client', return_value=self.client),
        ]
        for entry in self.patches:
            entry.start()
        self.addCleanup(lambda: [entry.stop() for entry in self.patches])

    def test_print_values_walks_every_kind_of_definition(self):
        output = capture(print_values_module.print_values, verbosity=0)

        for heading in ('Sensors', 'Binary Sensors', 'Enum Sensors', 'Switches', 'Selects'):
            self.assertIn(heading, output)

    def test_print_registers_stops_after_five_errors(self):
        self.client.error = True

        output = capture(print_values_module.print_registers, verbosity=0)

        self.assertEqual(len(self.client.reads), 5)
        self.assertIn('Error:', output)

    def test_print_registers_walks_the_documented_range(self):
        output = capture(print_values_module.print_registers, verbosity=0)

        self.assertEqual(self.client.reads[0], 2000)
        self.assertEqual(self.client.reads[-1], 3030)
        self.assertIn('Result', output)

    def test_probe_usb_ports_tries_every_port_and_survives_failures(self):
        with patch.object(print_values_module, 'probe_one_port', side_effect=OSError('no such device')) as probe:
            output = capture(print_values_module.probe_usb_ports, verbosity=0, max_port=3)

        self.assertEqual(probe.call_count, 3)
        self.assertEqual(output.count('ERROR: no such device'), 3)

    def test_probe_one_port_prints_the_sensor_values(self):
        user_settings = UserSettings()

        output = capture(
            print_values_module.probe_one_port, user_settings.heat_pump, DEFINITIONS, verbosity=0
        )

        self.assertIn('Outside temperature', output)
