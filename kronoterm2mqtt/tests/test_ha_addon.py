"""The Home Assistant add-on: its manifest, and the options it turns into settings."""

import dataclasses
import json
import os
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from cli_base.toml_settings.deserialize import toml2dataclass
import tomlkit
import yaml

from kronoterm2mqtt import __version__, ha_addon
from kronoterm2mqtt.constants import BASE_PATH
from kronoterm2mqtt.user_settings import UserSettings


ADDON_PATH = BASE_PATH.parent / 'ha-addon'

# Options the add-on adds on top of the app's settings, because they only mean
# something inside Home Assistant.
ADDON_ONLY_OPTIONS = {'log_level', ('mqtt', 'use_supervisor')}


def addon_config() -> dict:
    return yaml.safe_load((ADDON_PATH / 'config.yaml').read_text(encoding='UTF-8'))


class AddonManifestTestCase(TestCase):
    def setUp(self):
        self.config = addon_config()

    def test_every_setting_can_be_configured_from_the_interface(self):
        """No setting may be reachable only by editing a file: this is the point of the add-on."""
        for section, section_type in ha_addon.SECTIONS.items():
            options = self.config['options'][section]
            for field in dataclasses.fields(section_type):
                with self.subTest(option=f'{section}.{field.name}'):
                    self.assertIn(field.name, options)
                    self.assertIn(field.name, self.config['schema'][section])

    def test_the_manifest_offers_nothing_the_app_does_not_know(self):
        known = {
            (section, field.name)
            for section, section_type in ha_addon.SECTIONS.items()
            for field in dataclasses.fields(section_type)
        }
        for section, options in self.config['options'].items():
            if not isinstance(options, dict):
                self.assertIn(section, ADDON_ONLY_OPTIONS)
                continue
            for name in options:
                with self.subTest(option=f'{section}.{name}'):
                    self.assertTrue(
                        (section, name) in known or (section, name) in ADDON_ONLY_OPTIONS,
                        f'{section}.{name} is offered in the add-on but is not a setting',
                    )

    def test_options_and_schema_describe_the_same_options(self):
        for section, options in self.config['options'].items():
            if isinstance(options, dict):
                self.assertEqual(sorted(options), sorted(self.config['schema'][section]), f'in [{section}]')

    def test_every_option_group_is_explained_in_the_interface(self):
        translations = yaml.safe_load((ADDON_PATH / 'translations' / 'en.yaml').read_text(encoding='UTF-8'))
        self.assertEqual(sorted(translations['configuration']), sorted(self.config['options']))

    def test_the_add_on_and_the_image_it_builds_on_are_the_released_version(self):
        self.assertEqual(self.config['version'], __version__)

        build = yaml.safe_load((ADDON_PATH / 'build.yaml').read_text(encoding='UTF-8'))
        for arch, image in build['build_from'].items():
            self.assertEqual(image, f'hausbit/kronoterm2mqtt:{__version__}', f'for {arch}')

        dockerfile = (ADDON_PATH / 'Dockerfile').read_text(encoding='UTF-8')
        self.assertIn(f'ARG BUILD_FROM=hausbit/kronoterm2mqtt:{__version__}\n', dockerfile)

    def test_the_health_endpoint_is_reachable_by_the_watchdog(self):
        """127.0.0.1 would leave the Supervisor watching a port it cannot reach."""
        self.assertEqual(self.config['options']['health']['host'], '0.0.0.0')
        self.assertTrue(self.config['options']['health']['enabled'])
        self.assertIn(str(self.config['options']['health']['port']), self.config['watchdog'])


class OptionsToSettingsTestCase(TestCase):
    def test_the_defaults_produce_settings_the_app_accepts(self):
        document = ha_addon.settings_document(addon_config()['options'])

        settings = UserSettings()
        toml2dataclass(document=tomlkit.loads(tomlkit.dumps(document)), instance=settings)

        self.assertEqual(settings.mqtt.main_uid, 'kronoterm')
        self.assertEqual(settings.heat_pump.definitions_name, 'kronoterm_ksm')
        self.assertEqual(settings.health.host, '0.0.0.0')
        self.assertEqual(settings.custom_expander.sensor_names[0], 'Spalnice')
        self.assertFalse(settings.custom_expander.module_enabled)

    def test_what_was_set_in_the_interface_reaches_the_settings(self):
        options = {
            'mqtt': {'host': 'broker.lan', 'port': 8883, 'main_uid': 'attic'},
            'heat_pump': {'port': '192.168.1.50:502', 'pooling_interval': 30},
            'custom_expander': {'module_enabled': True, 'loop_sensors': [2, 3]},
        }

        document = ha_addon.settings_document(options)
        settings = UserSettings()
        toml2dataclass(document=document, instance=settings)

        self.assertEqual(settings.mqtt.host, 'broker.lan')
        self.assertEqual(settings.mqtt.port, 8883)
        self.assertEqual(settings.heat_pump.port, '192.168.1.50:502')
        self.assertEqual(settings.heat_pump.pooling_interval, 30)
        self.assertTrue(settings.custom_expander.module_enabled)
        self.assertEqual(settings.custom_expander.loop_sensors, [2, 3])
        # Not set in the interface, so the app's own default:
        self.assertEqual(settings.heat_pump.timeout, 0.5)

    def test_the_broker_home_assistant_knows_about_wins_over_the_form(self):
        options = {'mqtt': {'use_supervisor': True, 'host': 'left-over.lan', 'main_uid': 'attic'}}
        service = {'host': 'core-mosquitto', 'port': 1883, 'user_name': 'addons', 'password': 'secret'}

        document = ha_addon.settings_document(options, mqtt_service=service)

        self.assertEqual(document['mqtt']['host'], 'core-mosquitto')
        self.assertEqual(document['mqtt']['user_name'], 'addons')
        self.assertEqual(document['mqtt']['main_uid'], 'attic')
        self.assertNotIn('use_supervisor', document['mqtt'])  # Not a setting of the app

    def test_the_form_is_used_when_the_supervisor_is_not_asked(self):
        options = {'mqtt': {'use_supervisor': False, 'host': 'broker.lan'}}

        document = ha_addon.settings_document(options, mqtt_service={'host': 'core-mosquitto'})

        self.assertEqual(document['mqtt']['host'], 'broker.lan')

    def test_the_file_says_where_it_came_from(self):
        text = tomlkit.dumps(ha_addon.settings_document({}))

        self.assertIn('Home Assistant add-on', text)
        self.assertIn('overwritten on the next start', text)

    def test_the_settings_are_written_where_the_app_looks_for_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.config' / 'kronoterm2mqtt' / 'kronoterm2mqtt.toml'

            self.assertTrue(ha_addon.write_settings(ha_addon.settings_document({}), path=path))

            self.assertTrue(path.is_file())
            self.assertIn('[heat_pump]', path.read_text(encoding='UTF-8'))

    def test_unchanged_options_leave_the_settings_file_alone(self):
        """Rewriting it every start would make the app back it up and rebuild it every start."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'kronoterm2mqtt.toml'
            options = addon_config()['options']
            ha_addon.write_settings(ha_addon.settings_document(options), path=path)
            written = path.read_text(encoding='UTF-8')

            self.assertFalse(ha_addon.write_settings(ha_addon.settings_document(options), path=path))
            self.assertEqual(path.read_text(encoding='UTF-8'), written)

    def test_settings_the_app_added_itself_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'kronoterm2mqtt.toml'
            ha_addon.write_settings(ha_addon.settings_document({}), path=path)
            # What the app completes the file with on its first start:
            path.write_text(path.read_text(encoding='UTF-8') + '\n[systemd]\nservice_slug = "kronoterm2mqtt"\n')

            changed = ha_addon.write_settings(ha_addon.settings_document({'mqtt': {'host': 'broker.lan'}}), path=path)

            self.assertTrue(changed)
            settings = tomlkit.loads(path.read_text(encoding='UTF-8'))
            self.assertEqual(settings['systemd']['service_slug'], 'kronoterm2mqtt')
            self.assertEqual(settings['mqtt']['host'], 'broker.lan')


class SupervisorMqttTestCase(TestCase):
    def answer(self, payload: dict):
        class Response:
            def read(self):
                return json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        return patch('urllib.request.urlopen', return_value=Response())

    def test_the_service_is_translated_into_mqtt_settings(self):
        payload = {
            'result': 'ok',
            'data': {'host': 'core-mosquitto', 'port': 1883, 'username': 'addons', 'password': 'secret', 'ssl': False},
        }

        with self.answer(payload):
            settings = ha_addon.supervisor_mqtt(token='token')

        self.assertEqual(
            settings, {'host': 'core-mosquitto', 'port': 1883, 'user_name': 'addons', 'password': 'secret'}
        )

    def test_no_broker_configured_is_not_an_error(self):
        with self.answer({'result': 'ok', 'data': {}}):
            self.assertEqual(ha_addon.supervisor_mqtt(token='token'), {})

    def test_without_a_token_nothing_is_asked(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(ha_addon.supervisor_mqtt(), {})

    def test_a_supervisor_that_does_not_answer_is_not_an_error(self):
        with patch('urllib.request.urlopen', side_effect=OSError('no route to host')):
            self.assertEqual(ha_addon.supervisor_mqtt(token='token'), {})


class CommandTestCase(TestCase):
    def test_the_home_assistant_log_level_becomes_the_app_verbosity(self):
        self.assertEqual(ha_addon.command({'log_level': 'error'})[-1], 'publish-loop')
        self.assertEqual(ha_addon.command({'log_level': 'warning'})[-1], '-v')
        self.assertEqual(ha_addon.command({'log_level': 'debug'})[-1], '-vvv')

    def test_an_unknown_level_falls_back_to_info(self):
        self.assertEqual(ha_addon.command({'log_level': 'chatty'}), ha_addon.command({'log_level': 'info'}))


class StartUpTestCase(TestCase):
    """The part that runs as a process: ownership, privileges and what is started."""

    def test_data_is_handed_to_the_user_the_app_runs_as(self):
        chowned = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'options.json').write_text('{}', encoding='UTF-8')

            with patch('os.chown', lambda path, uid, gid: chowned.append((Path(path).name, uid, gid))):
                ha_addon.own_data_directory(root)

        self.assertIn((root.name, ha_addon.APP_UID, ha_addon.APP_GID), chowned)
        self.assertIn(('options.json', ha_addon.APP_UID, ha_addon.APP_GID), chowned)

    def test_a_file_that_cannot_be_handed_over_does_not_stop_the_start(self):
        with tempfile.TemporaryDirectory() as directory, patch('os.chown', side_effect=PermissionError('read-only')):
            ha_addon.own_data_directory(Path(directory))  # Logs a warning, does not raise

    def test_privileges_are_dropped_to_the_unprivileged_user(self):
        with (
            patch('os.geteuid', return_value=0),
            patch('os.setgroups') as setgroups,
            patch('os.setgid') as setgid,
            patch('os.setuid') as setuid,
        ):
            ha_addon.drop_privileges()

        setgid.assert_called_once_with(ha_addon.APP_GID)
        setuid.assert_called_once_with(ha_addon.APP_UID)
        setgroups.assert_called_once()

    def test_there_is_nothing_to_drop_when_not_root(self):
        with patch('os.geteuid', return_value=1000), patch('os.setuid') as setuid:
            ha_addon.drop_privileges()

        setuid.assert_not_called()

    def test_groups_that_cannot_be_reset_do_not_stop_the_start(self):
        with (
            patch('os.geteuid', return_value=0),
            patch('os.setgroups', side_effect=OSError('not permitted')),
            patch('os.setgid'),
            patch('os.setuid'),
        ):
            ha_addon.drop_privileges()

    def test_a_start_renders_the_options_and_hands_over_to_the_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = {'mqtt': {'host': 'broker.lan', 'use_supervisor': False}, 'log_level': 'warning'}
            (root / 'options.json').write_text(json.dumps(options), encoding='UTF-8')
            settings_path = root / '.config' / 'kronoterm2mqtt' / 'kronoterm2mqtt.toml'

            with (
                patch.object(ha_addon, 'OPTIONS_PATH', root / 'options.json'),
                patch.object(ha_addon, 'SETTINGS_PATH', settings_path),
                patch.object(ha_addon, 'DATA_PATH', root),
                patch.object(ha_addon, 'own_data_directory'),
                patch.object(ha_addon, 'drop_privileges') as drop_privileges,
                patch('os.execve') as execve,
            ):
                ha_addon.main()

            self.assertIn('broker.lan', settings_path.read_text(encoding='UTF-8'))
            drop_privileges.assert_called_once()
            execve.assert_called_once()
            _path, argv, environment = execve.call_args[0]
            self.assertEqual(argv[1:], ['publish-loop', '-v'])
            # The app looks for its settings under HOME, which is not where the image put it
            self.assertEqual(environment['HOME'], str(root))
            self.assertNotEqual(os.environ.get('HOME'), str(root))

    def test_the_broker_is_looked_up_only_when_asked_for(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'options.json').write_text(json.dumps({'mqtt': {'use_supervisor': True}}), encoding='UTF-8')

            with (
                patch.object(ha_addon, 'OPTIONS_PATH', root / 'options.json'),
                patch.object(ha_addon, 'SETTINGS_PATH', root / 'kronoterm2mqtt.toml'),
                patch.object(ha_addon, 'supervisor_mqtt', return_value={'host': 'core-mosquitto'}) as lookup,
                patch.object(ha_addon, 'DATA_PATH', root),
                patch.object(ha_addon, 'own_data_directory'),
                patch.object(ha_addon, 'drop_privileges'),
                patch('os.execve'),
            ):
                ha_addon.main()

            lookup.assert_called_once()
            self.assertIn('core-mosquitto', (root / 'kronoterm2mqtt.toml').read_text(encoding='UTF-8'))

    def test_started_outside_home_assistant_it_says_so(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(ha_addon, 'OPTIONS_PATH', Path(directory) / 'options.json'),
        ):
            self.assertEqual(ha_addon.main(), 1)
