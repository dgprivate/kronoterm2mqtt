from pathlib import Path
from unittest import TestCase

from kronoterm2mqtt.user_settings import HeatPump, SystemdServiceInfo, UserSettings


class UserSettingsTestCase(TestCase):
    def test_systemd_service_info(self):
        user_settings = UserSettings()
        systemd_settings = user_settings.systemd
        self.assertIsInstance(systemd_settings, SystemdServiceInfo)

        # Check some samples:
        self.assertEqual(systemd_settings.template_context.verbose_service_name, 'kronoterm2mqtt')
        self.assertEqual(systemd_settings.service_slug, 'kronoterm2mqtt')
        self.assertEqual(systemd_settings.template_context.syslog_identifier, 'kronoterm2mqtt')
        self.assertEqual(systemd_settings.service_file_path, Path('/etc/systemd/system/kronoterm2mqtt.service'))


class DefinitionsTestCase(TestCase):
    """The shipped definitions are data the publish loop depends on."""

    def setUp(self):
        self.heat_pump = HeatPump()
        self.definitions = self.heat_pump.get_definitions(verbosity=0)

    def test_the_shipped_definitions_parse(self):
        self.assertEqual(self.definitions['connection']['baudrate'], 115200)
        self.assertTrue(self.definitions['sensor'])

    def test_every_sensor_carries_what_the_publish_loop_reads(self):
        for parameter in self.definitions['sensor']:
            for key in ('register', 'name', 'device_class', 'state_class', 'unit_of_measurement', 'scale'):
                self.assertIn(key, parameter, parameter.get('name'))

    def test_names_are_unique_so_the_mqtt_uids_are_too(self):
        names = [parameter['name'] for parameter in self.definitions['sensor']]
        self.assertEqual(sorted(set(names)), sorted(names))

    def test_option_lists_line_up_apart_from_one_known_gap(self):
        """A key without a value is skipped at publish time - see mqtt_handler.

        "Error register 2118" documents 17 error bits but only names 15 of them.
        Anything else appearing here is a new gap and worth a look.
        """
        incomplete = []
        for section in ('enum_sensor', 'select'):
            for parameter in self.definitions.get(section, []):
                options = parameter['options'][0]
                self.assertTrue(options['keys'], parameter['name'])
                if len(options['values']) < len(options['keys']):
                    incomplete.append(parameter['name'])

        self.assertEqual(incomplete, ['Error register 2118'])

    def test_verbose_definitions_are_printed(self):
        self.heat_pump.get_definitions(verbosity=2)

    def test_a_missing_definitions_file_is_an_error(self):
        heat_pump = HeatPump(definitions_name='does_not_exist')

        with self.assertRaises(FileNotFoundError):
            heat_pump.get_definitions(verbosity=0)
