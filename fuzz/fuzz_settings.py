#!/usr/bin/env python3
"""Fuzz the settings reader with arbitrary TOML.

The settings file is edited by hand, so it arrives in every state a text editor can
produce. Reading it must end in a clear error or a usable settings object, never in an
unhandled exception from deep inside the deserialiser.
"""

import sys

import atheris


with atheris.instrument_imports():
    from cli_base.toml_settings.deserialize import toml2dataclass
    import tomlkit

    from kronoterm2mqtt.user_settings import UserSettings


def test_one_input(data: bytes) -> None:
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return
    try:
        document = tomlkit.loads(text)
    except Exception:  # noqa: BLE001 - malformed TOML is the caller's problem, not a finding
        return

    # No handler: anything raised here is a settings file crashing the reader, which is
    # the whole point of this target.
    toml2dataclass(document=document, instance=UserSettings())


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == '__main__':
    main()
