import socket
from unittest import TestCase
from unittest.mock import MagicMock, patch

from kronoterm2mqtt.api import GreetingTolerantModbusTcpClient, get_modbus_client
from kronoterm2mqtt.constants import MODBUS_TCP_MIN_TIMEOUT
from kronoterm2mqtt.tests.loopback import loopback_connection
from kronoterm2mqtt.tests.test_mqtt_handler import DEFINITIONS
from kronoterm2mqtt.user_settings import HeatPump


class ClientChoiceTestCase(TestCase):
    def test_a_device_path_gives_a_serial_client(self):
        heat_pump = HeatPump(port='/dev/ttyUSB0', timeout=0.5)

        with patch('kronoterm2mqtt.api.ModbusSerialClient') as serial_client:
            client = get_modbus_client(heat_pump=heat_pump, definitions=DEFINITIONS, verbosity=0)

        self.assertIs(client, serial_client.return_value)
        args, kwargs = serial_client.call_args
        self.assertEqual(args[0], '/dev/ttyUSB0')
        self.assertEqual(kwargs['baudrate'], DEFINITIONS['connection']['baudrate'])
        self.assertEqual(kwargs['parity'], DEFINITIONS['connection']['parity'])
        self.assertEqual(kwargs['timeout'], 0.5)

    def test_serial_settings_are_printed_when_asked(self):
        heat_pump = HeatPump(port='/dev/ttyUSB0')

        with patch('kronoterm2mqtt.api.ModbusSerialClient'):
            get_modbus_client(heat_pump=heat_pump, definitions=DEFINITIONS, verbosity=2)

    def test_a_host_gives_a_tcp_client_with_room_to_answer(self):
        heat_pump = HeatPump(port='192.168.1.2:502', timeout=0.5)

        client = get_modbus_client(heat_pump=heat_pump, definitions=DEFINITIONS, verbosity=0)

        self.assertIsInstance(client, GreetingTolerantModbusTcpClient)
        self.assertEqual(client.comm_params.host, '192.168.1.2')
        self.assertEqual(client.comm_params.port, 502)
        # A serial timeout is too tight for a gateway:
        self.assertEqual(client.comm_params.timeout_connect, MODBUS_TCP_MIN_TIMEOUT)

    def test_the_default_modbus_port_is_used_when_none_is_given(self):
        client = get_modbus_client(heat_pump=HeatPump(port='192.168.1.2'), definitions={}, verbosity=0)

        self.assertEqual(client.comm_params.port, 502)


class DiscardGreetingTestCase(TestCase):
    def make_client(self) -> GreetingTolerantModbusTcpClient:
        return GreetingTolerantModbusTcpClient(host='127.0.0.1', port=502)

    def test_nothing_to_do_without_a_socket(self):
        client = self.make_client()

        self.assertEqual(client.discard_greeting(), b'')

    def test_a_closed_socket_ends_the_drain(self):
        client = self.make_client()
        client.socket = MagicMock()
        client.socket.recv.return_value = b''  # Peer closed

        with patch('select.select', return_value=([client.socket], [], [])):
            self.assertEqual(client.discard_greeting(), b'')

    def test_a_socket_error_ends_the_drain(self):
        client = self.make_client()
        client.socket = MagicMock()
        client.socket.recv.side_effect = OSError('connection reset')

        with patch('select.select', return_value=([client.socket], [], [])):
            self.assertEqual(client.discard_greeting(), b'')

    def test_a_gateway_that_never_stops_talking_is_cut_off(self):
        client = self.make_client()
        client.socket = MagicMock()
        client.socket.recv.return_value = b'x' * 256

        with (
            patch('select.select', return_value=([client.socket], [], [])),
            self.assertLogs('kronoterm2mqtt.api', level='WARNING') as logs,
        ):
            greeting = client.discard_greeting()

        self.assertEqual(len(greeting), client.greeting_max_bytes)
        self.assertIn('keeps sending data unasked', '\n'.join(logs.output))

    def test_an_established_connection_is_drained_once(self):
        client = self.make_client()
        drained = []

        with (
            patch.object(GreetingTolerantModbusTcpClient, 'discard_greeting', lambda self: drained.append(1)),
            patch('pymodbus.client.ModbusTcpClient.connect', return_value=True),
        ):
            client.socket = None
            client.connect()  # Fresh connection -> drain
            client.socket = MagicMock()
            client.connect()  # Already connected -> no second drain

        self.assertEqual(len(drained), 1)


@patch('socket.create_connection', loopback_connection)
class GreetingSocketTestCase(TestCase):
    def test_the_greeting_is_read_from_a_real_socket(self):
        server = socket.create_server(('127.0.0.1', 0))
        self.addCleanup(server.close)
        client = GreetingTolerantModbusTcpClient(host='127.0.0.1', port=server.getsockname()[1])

        sock = socket.create_connection(server.getsockname(), timeout=5)
        self.addCleanup(sock.close)
        connection, _address = server.accept()
        self.addCleanup(connection.close)
        connection.sendall(bytes.fromhex('287a0e915ea9'))

        client.socket = sock
        with self.assertLogs('kronoterm2mqtt.api', level='WARNING') as logs:
            greeting = client.discard_greeting()

        self.assertEqual(greeting, bytes.fromhex('287a0e915ea9'))
        self.assertIn('28 7a 0e 91 5e a9', '\n'.join(logs.output))
