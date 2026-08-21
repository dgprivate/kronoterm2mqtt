"""The thin commands: they exist to wire a CLI call to a library call.

The tests check that wiring - that each command reaches the right function with the
settings it is supposed to pass - rather than the behaviour of the libraries behind it.
"""

import sys
from unittest import TestCase
from unittest.mock import MagicMock, patch

from kronoterm2mqtt.cli_app import settings as settings_cli
from kronoterm2mqtt.cli_app import systemd as systemd_cli
import kronoterm2mqtt.cli_dev as cli_dev_init
from kronoterm2mqtt.cli_dev import firmware as firmware_cli
from kronoterm2mqtt.cli_dev import packaging as packaging_cli
from kronoterm2mqtt.cli_dev import testing as testing_cli
from kronoterm2mqtt.cli_dev import update_readme_history as history_cli
from kronoterm2mqtt.user_settings import UserSettings


class SystemdCommandsTestCase(TestCase):
    """Every systemd command builds a ServiceControl from the settings and calls one method."""

    def setUp(self):
        self.user_settings = UserSettings()
        self.service_control = MagicMock()
        patches = [
            patch.object(systemd_cli, 'get_user_settings', return_value=self.user_settings),
            patch.object(systemd_cli, 'ServiceControl', return_value=self.service_control),
        ]
        for entry in patches:
            entry.start()
        self.addCleanup(lambda: [entry.stop() for entry in patches])

    def test_each_command_calls_its_own_method(self):
        for command, method in (
            (systemd_cli.systemd_debug, 'debug_systemd_config'),
            (systemd_cli.systemd_setup, 'setup_and_restart_systemd_service'),
            (systemd_cli.systemd_remove, 'remove_systemd_service'),
            (systemd_cli.systemd_status, 'status'),
            (systemd_cli.systemd_stop, 'stop'),
        ):
            with self.subTest(command=command.__name__):
                self.service_control.reset_mock()
                command(verbosity=0)
                getattr(self.service_control, method).assert_called_once_with()

    def test_the_settings_are_the_ones_from_the_settings_file(self):
        systemd_cli.systemd_status(verbosity=0)

        self.assertIs(
            systemd_cli.get_systemd_settings(verbosity=0),
            self.user_settings.systemd,
        )


class SettingsCommandsTestCase(TestCase):
    def test_edit_settings_opens_the_file(self):
        toml_settings = MagicMock()
        with patch.object(settings_cli, 'get_toml_settings', return_value=toml_settings):
            settings_cli.edit_settings(verbosity=0)

        toml_settings.open_in_editor.assert_called_once_with()

    def test_print_settings_prints_the_anonymised_version(self):
        toml_settings = MagicMock()
        with patch.object(settings_cli, 'get_toml_settings', return_value=toml_settings):
            settings_cli.print_settings(verbosity=0)

        toml_settings.print_settings.assert_called_once_with()


class PackagingCommandsTestCase(TestCase):
    def test_install_syncs_and_installs_editable(self):
        executor = MagicMock()
        with patch.object(packaging_cli, 'ToolsExecutor', return_value=executor):
            packaging_cli.install()

        calls = [call.args for call in executor.verbose_check_call.call_args_list]
        self.assertIn(('uv', 'sync'), calls)
        self.assertIn(('pip', 'install', '--no-deps', '-e', '.'), calls)

    def test_update_upgrades_the_lock_and_the_hooks(self):
        executor = MagicMock()
        with (
            patch.object(packaging_cli, 'ToolsExecutor', return_value=executor),
            patch.object(packaging_cli, 'run_pip_audit') as pip_audit,
        ):
            packaging_cli.update(verbosity=0)

        calls = [call.args for call in executor.verbose_check_call.call_args_list]
        self.assertIn(('uv', 'lock', '--upgrade'), calls)
        self.assertIn(('pre-commit', 'autoupdate'), calls)
        pip_audit.assert_called_once()

    def test_pip_audit_runs_against_the_project(self):
        with patch.object(packaging_cli, 'run_pip_audit') as pip_audit:
            packaging_cli.pip_audit(verbosity=0)

        self.assertEqual(pip_audit.call_args.kwargs['base_path'], packaging_cli.PACKAGE_ROOT)

    def test_publish_runs_the_tests_before_uploading(self):
        with (
            patch.object(packaging_cli, 'run_unittest_cli') as run_tests,
            patch.object(packaging_cli, 'publish_package') as publish,
        ):
            packaging_cli.publish()

        run_tests.assert_called_once()
        publish.assert_called_once()


class DevToolCommandsTestCase(TestCase):
    def test_mypy_runs_against_the_package(self):
        with patch.object(testing_cli, 'verbose_check_call') as check_call:
            testing_cli.mypy(verbosity=0)

        self.assertEqual(check_call.call_args.args[0], 'mypy')
        self.assertEqual(check_call.call_args.kwargs['cwd'], testing_cli.PACKAGE_ROOT)

    def test_the_dummy_runners_hand_through(self):
        for command, target in (
            (testing_cli.test, 'run_unittest_cli'),
            (testing_cli.coverage, 'run_coverage'),
        ):
            with self.subTest(command=command.__name__):
                with patch.object(testing_cli, target) as runner:
                    command()
                runner.assert_called_once()

    def test_snapshot_files_are_recreated_by_running_the_tests(self):
        with (
            patch.object(testing_cli, 'UpdateTestSnapshotFiles'),
            patch.object(testing_cli, 'run_unittest_cli') as run_tests,
        ):
            testing_cli.update_test_snapshot_files(verbosity=0)

        self.assertEqual(run_tests.call_args.kwargs['extra_env'], {'RAISE_SNAPSHOT_ERRORS': '0'})

    def test_nox_is_handed_through(self):
        with patch.object(testing_cli, 'run_nox') as run_nox:
            testing_cli.nox()

        run_nox.assert_called_once()

    def test_firmware_compile_calls_platformio(self):
        with patch.object(firmware_cli, 'verbose_check_call') as check_call:
            firmware_cli.firmware_compile()

        self.assertEqual(check_call.call_args.args[0].name, 'pio')
        self.assertEqual(check_call.call_args.kwargs['cwd'], 'etera-uart-bridge/pio-eub-firmware')

    def test_firmware_flash_uses_the_configured_port(self):
        user_settings = UserSettings()
        with (
            patch.object(firmware_cli, 'get_user_settings', return_value=user_settings),
            patch.object(firmware_cli, 'verbose_check_call') as check_call,
        ):
            firmware_cli.firmware_flash(verbosity=0)

        args = check_call.call_args.args
        self.assertEqual(args[0], 'avrdude')
        self.assertIn(user_settings.custom_expander.port, args)

    def test_update_readme_history_writes_the_block(self):
        with patch.object(history_cli, 'update_readme_history', return_value=True) as update:
            history_cli.update_readme_history(verbosity=0)

        update.assert_called_once()


class DevCliEntryPointTestCase(TestCase):
    def test_version_exits_cleanly(self):
        with self.assertRaises(SystemExit) as context:
            cli_dev_init.version()

        self.assertEqual(context.exception.code, 0)

    def test_test_and_coverage_are_passed_to_their_runners(self):
        for command, target in (('test', 'run_unittest_cli'), ('coverage', 'run_coverage'), ('nox', 'run_nox')):
            with self.subTest(command=command):
                with (
                    patch.object(sys, 'argv', ['dev-cli.py', command]),
                    patch.object(cli_dev_init, 'print_version'),
                    patch.object(cli_dev_init, target) as runner,
                    # main() falls through to the parser afterwards; without this the
                    # parser would run the real command - including the test suite,
                    # from inside the test suite.
                    patch.object(cli_dev_init.app, 'cli'),
                ):
                    cli_dev_init.main()

                runner.assert_called_once()
                self.assertTrue(runner.call_args.kwargs['exit_after_run'])

    def test_anything_else_reaches_the_command_line_parser(self):
        with (
            patch.object(sys, 'argv', ['dev-cli.py', 'version']),
            patch.object(cli_dev_init, 'print_version'),
            patch.object(cli_dev_init.app, 'cli') as cli,
        ):
            cli_dev_init.main()

        cli.assert_called_once()
