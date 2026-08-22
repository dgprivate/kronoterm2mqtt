#!/usr/bin/env python3
"""Fuzz what arrives on the Modbus socket.

Everything the heat pump and the gateway in front of it send is untrusted input: this
project already shipped a bug where a gateway greeting desynchronised the framer for the
rest of the connection ("Invalid Modbus protocol id: 3729"). Both the draining of that
greeting and the framing of the bytes after it must end in a Modbus error the retry layer
knows how to handle, never in an unexpected exception.
"""

import socket
import sys

import atheris


with atheris.instrument_imports():
    from pymodbus.exceptions import ModbusException

    from kronoterm2mqtt.api import GreetingTolerantModbusTcpClient


# Handled by the caller: kronoterm2mqtt catches ModbusException around every read.
EXPECTED = (ModbusException, OSError)


def drain(client: GreetingTolerantModbusTcpClient, greeting: bytes) -> None:
    """Let the client read `greeting` off a real socket, as it would from a gateway."""
    ours, theirs = socket.socketpair()
    try:
        theirs.sendall(greeting)
        theirs.close()  # So the drain loop sees the end of the data instead of waiting
        client.socket = ours
        client.discard_greeting()
    finally:
        client.socket = None
        ours.close()


def test_one_input(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    greeting = provider.ConsumeBytes(provider.ConsumeIntInRange(0, 64))
    response = provider.ConsumeBytes(provider.remaining_bytes())

    client = GreetingTolerantModbusTcpClient(host='127.0.0.1', port=502, timeout=0.01)
    client.greeting_timeout = 0.01

    drain(client, greeting)

    try:
        client.framer.handleFrame(response, 20, 0)
    except EXPECTED:
        pass


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == '__main__':
    main()
