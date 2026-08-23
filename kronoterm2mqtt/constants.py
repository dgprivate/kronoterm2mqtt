from pathlib import Path


CLI_EPILOG = 'Project Homepage: https://github.com/kosl/kronoterm2mqtt'

BASE_PATH = Path(__file__).parent


DEFAULT_DEVICE_MANUFACTURER = 'KRONOTERM'

MODBUS_SLAVE_ID = 20  # Kronoterm System Module Modbus address

# Modbus resilience: the connection to the heat pump can drop, so retry instead of
# letting the exception kill the publish loop.
MODBUS_READ_ATTEMPTS = 3  # Attempts per register block before skipping this cycle
MODBUS_RETRY_DELAY = 1.0  # Seconds to wait before retrying a failed Modbus read
MODBUS_WRITE_ATTEMPTS = 3  # Attempts per register write before giving up
# The configured `timeout` default suits a serial line; a TCP gateway needs more headroom.
MODBUS_TCP_MIN_TIMEOUT = 3.0
# Some Modbus/TCP gateways announce themselves with a few unsolicited bytes (their MAC
# address as a "registration packet") right after the connection is established. Time to
# wait for those bytes so they can be discarded before the first request is sent.
MODBUS_TCP_GREETING_TIMEOUT = 0.5

# Etera expander module constants

MIXING_VALVE_HOLD_TIME = 120  # time between motor movements in seconds
MIXING_VALVE_TRAVEL_TIME = 120  # seconds from fully closed to fully open
# MA_2001 "Funkcija delovanja": the heat pump is heating sanitary water, so the heating
# loops are not circulating.
WORKING_FUNCTION_SANITARY_WATER = 1
