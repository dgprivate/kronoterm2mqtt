"""Property-based tests: the same code paths, driven by generated input.

The example-based tests elsewhere pin down the values that matter in practice.
These run the decoding and the health logic over whole ranges of input, which is
how a case nobody thought of shows up.
"""

from decimal import Decimal
import string
from unittest import TestCase
from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from kronoterm2mqtt.health import HealthState
from kronoterm2mqtt.mqtt_handler import KronotermMqttHandler


def to_signed(value: int) -> int:
    """The conversion the publish loop applies to every register it reads."""
    return value - (value >> 15 << 16)


class RegisterDecodingTestCase(TestCase):
    @given(raw=st.integers(min_value=0, max_value=0xFFFF))
    def test_every_register_value_decodes_into_the_signed_16_bit_range(self, raw):
        self.assertGreaterEqual(to_signed(raw), -32768)
        self.assertLessEqual(to_signed(raw), 32767)

    @given(raw=st.integers(min_value=0, max_value=0x7FFF))
    def test_values_below_the_sign_bit_are_left_alone(self, raw):
        self.assertEqual(to_signed(raw), raw)

    @given(value=st.integers(min_value=-32768, max_value=32767))
    def test_decoding_undoes_the_encoding(self, value):
        self.assertEqual(to_signed(value & 0xFFFF), value)

    @given(
        raw=st.integers(min_value=0, max_value=0xFFFF),
        scale=st.sampled_from(['1', '0.1', '0.01', '10']),
    )
    def test_scaling_keeps_the_sign_and_the_precision(self, raw, scale):
        signed = to_signed(raw)
        published = float(Decimal(scale) * Decimal(signed))

        self.assertEqual(published >= 0, signed >= 0)
        self.assertAlmostEqual(published / float(scale), signed, places=6)


class AddressRangeTestCase(TestCase):
    @given(addresses=st.sets(st.integers(min_value=2000, max_value=2400), min_size=1, max_size=60))
    @settings(max_examples=200)
    def test_blocks_cover_every_address_and_nothing_else(self, addresses):
        handler = object.__new__(KronotermMqttHandler)

        blocks = list(handler.ranges(sorted(addresses)))

        covered = {address for start, end in blocks for address in range(start, end + 1)}
        self.assertEqual(covered, addresses)
        for start, end in blocks:
            self.assertLessEqual(start, end)
        starts = [start for start, _ in blocks]
        self.assertEqual(starts, sorted(starts))


class HealthStateTestCase(TestCase):
    @given(
        connected=st.booleans(),
        read=st.booleans(),
        published=st.booleans(),
        stale_after=st.integers(min_value=1, max_value=3600),
    )
    def test_healthy_exactly_when_nothing_is_missing(self, connected, read, published, stale_after):
        state = HealthState(stale_after_seconds=stale_after, mqtt_host='h', modbus_port='p')
        state.set_mqtt_client(MagicMock(is_connected=MagicMock(return_value=connected)))
        if read:
            state.record_modbus_read(complete=True)
        if published:
            state.record_publish(published_count=1)

        report = state.as_dict()

        self.assertEqual(report['healthy'], connected and read and published)
        self.assertEqual(report['healthy'], not report['problems'])

    @given(error=st.text(alphabet=string.printable, max_size=200), count=st.integers(min_value=1, max_value=20))
    def test_failures_are_counted_and_the_last_one_is_kept(self, error, count):
        state = HealthState(stale_after_seconds=60)
        for _ in range(count):
            state.record_modbus_failure(error)

        report = state.as_dict()

        self.assertEqual(report['modbus']['failed_reads'], count)
        self.assertEqual(report['modbus']['last_error'], error)
        self.assertIsNone(report['modbus']['last_read_seconds_ago'])
