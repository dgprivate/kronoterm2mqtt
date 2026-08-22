#!/usr/bin/env python3
"""Fuzz the health report rendering: whatever the endpoint returns, `health` must not crash.

The report is read from a socket, so the command cannot assume it wrote it: an older
container, a truncated answer or an unrelated service on that port all produce something
else. A loop that is misbehaving is exactly when the report is least likely to look the
way the code expects, so rendering has to survive missing keys, wrong types and nonsense
values rather than adding a traceback to whatever is already wrong.
"""

import io
import sys

import atheris


with atheris.instrument_imports():
    from unittest.mock import patch

    from rich.console import Console

    from kronoterm2mqtt.cli_app import health as health_command
    from kronoterm2mqtt.user_settings import UserSettings


TOP_LEVEL_KEYS = ('healthy', 'uptime_seconds', 'sensors_published', 'problems', 'mqtt', 'modbus')
MQTT_KEYS = ('connected', 'host', 'last_publish_seconds_ago')
MODBUS_KEYS = ('port', 'last_read_seconds_ago', 'last_read_complete', 'failed_reads', 'last_error')


def value(provider: atheris.FuzzedDataProvider):
    """One report value, of a type the endpoint might plausibly produce - or might not."""
    match provider.ConsumeIntInRange(0, 7):
        case 0:
            return None
        case 1:
            return provider.ConsumeBool()
        case 2:
            return provider.ConsumeInt(4)
        case 3:
            return provider.ConsumeFloat()
        case 4:
            return provider.ConsumeUnicodeNoSurrogates(32)
        case 5:
            return [provider.ConsumeUnicodeNoSurrogates(8) for _ in range(provider.ConsumeIntInRange(0, 3))]
        case 6:
            return {provider.ConsumeUnicodeNoSurrogates(8): provider.ConsumeInt(2)}
        case _:
            return provider.ConsumeBytes(4)


def section(provider: atheris.FuzzedDataProvider, keys: tuple[str, ...]) -> dict:
    return {key: value(provider) for key in keys if provider.ConsumeBool()}


def render(state: dict) -> None:
    console = Console(file=io.StringIO(), width=200, no_color=True)
    with (
        patch.object(health_command, 'print', console.print),
        patch.object(health_command, 'get_user_settings', return_value=UserSettings()),
        patch.object(health_command, 'fetch_health', return_value=state),
    ):
        try:
            health_command.health(verbosity=0)
        except SystemExit:
            pass  # The command reports its verdict through the exit code


def test_one_input(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    state = section(provider, TOP_LEVEL_KEYS)
    if provider.ConsumeBool():
        state['mqtt'] = section(provider, MQTT_KEYS)
    if provider.ConsumeBool():
        state['modbus'] = section(provider, MODBUS_KEYS)
    render(state)


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == '__main__':
    main()
