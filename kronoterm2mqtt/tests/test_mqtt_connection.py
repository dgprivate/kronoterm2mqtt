import socket
import ssl
from unittest import TestCase
from unittest.mock import MagicMock, patch

from kronoterm2mqtt.mqtt_connection import get_connected_client
from kronoterm2mqtt.user_settings import UserSettings


def make_settings(**tls) -> UserSettings:
    user_settings = UserSettings()
    user_settings.mqtt.host = 'mqtt.example.com'
    user_settings.mqtt.port = 8883
    user_settings.mqtt.user_name = 'user'
    user_settings.mqtt.password = 'secret'
    for key, value in tls.items():
        setattr(user_settings.mqtt_tls, key, value)
    return user_settings


class WithoutTlsTestCase(TestCase):
    def test_plain_connections_are_left_to_ha_services(self):
        user_settings = make_settings(enabled=False)
        client = MagicMock()

        with patch(
            'kronoterm2mqtt.mqtt_connection._upstream_get_connected_client', return_value=client
        ) as upstream:
            result = get_connected_client(user_settings=user_settings, verbosity=0)

        self.assertIs(result, client)
        upstream.assert_called_once()
        self.assertEqual(upstream.call_args.kwargs['settings'], user_settings.mqtt)


class WithTlsTestCase(TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.address_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 8883))]
        patches = [
            patch('paho.mqtt.client.Client', return_value=self.client),
            patch('socket.getaddrinfo', return_value=self.address_info),
            patch('socket.setdefaulttimeout'),
        ]
        for entry in patches:
            entry.start()
        self.addCleanup(lambda: [entry.stop() for entry in patches])

    def connect(self, **tls):
        settings = dict(
            enabled=True,
            ca_certs='/certs/ca.crt',
            certfile='/certs/client.crt',
            keyfile='/certs/client.key',
        )
        settings.update(tls)
        user_settings = make_settings(**settings)
        return get_connected_client(user_settings=user_settings, verbosity=0), user_settings

    def test_certificates_are_handed_to_the_client(self):
        client, _settings = self.connect()

        self.client.tls_set.assert_called_once()
        kwargs = self.client.tls_set.call_args.kwargs
        self.assertEqual(kwargs['ca_certs'], '/certs/ca.crt')
        self.assertEqual(kwargs['certfile'], '/certs/client.crt')
        self.assertEqual(kwargs['keyfile'], '/certs/client.key')
        self.assertIs(client, self.client)

    def test_the_server_certificate_is_verified_by_default(self):
        self.connect()

        kwargs = self.client.tls_set.call_args.kwargs
        self.assertEqual(kwargs['cert_reqs'], ssl.CERT_REQUIRED)
        self.assertEqual(kwargs['tls_version'], ssl.PROTOCOL_TLS_CLIENT)
        self.client.tls_insecure_set.assert_not_called()

    def test_insecure_is_opt_in_only(self):
        self.connect(insecure=True)

        self.client.tls_insecure_set.assert_called_once_with(True)

    def test_empty_paths_become_none_so_the_system_store_is_used(self):
        self.connect(ca_certs='', certfile='', keyfile='')

        kwargs = self.client.tls_set.call_args.kwargs
        self.assertIsNone(kwargs['ca_certs'])
        self.assertIsNone(kwargs['certfile'])
        self.assertIsNone(kwargs['keyfile'])

    def test_credentials_are_set_and_the_client_connects(self):
        _client, user_settings = self.connect()

        self.client.username_pw_set.assert_called_once_with('user', 'secret')
        self.client.connect.assert_called_once_with(user_settings.mqtt.host, port=8883)

    def test_a_name_that_does_not_resolve_ends_the_command(self):
        with (
            patch('socket.getaddrinfo', side_effect=socket.gaierror('Name or service not known')),
            self.assertRaises(SystemExit),
        ):
            self.connect()
