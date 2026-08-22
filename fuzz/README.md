# Fuzzing

Coverage-guided fuzz targets for the code that reads input this project does not
control. Everything here uses [Atheris](https://github.com/google/atheris), which
drives libFuzzer against instrumented Python.

| Target | What it feeds, and why |
| --- | --- |
| `fuzz_health_report.py` | Health reports as they arrive from the socket. An older container, a truncated answer or an unrelated service on that port all produce something the `health` command did not write, and a status command is the wrong place to raise a traceback. |
| `fuzz_settings.py` | Arbitrary TOML through the settings reader. The settings file is edited by hand, so it arrives in every state a text editor can produce. |
| `fuzz_modbus_frame.py` | Bytes from a Modbus gateway, through the greeting drain and the frame decoder. This project already shipped a bug where a gateway greeting desynchronised the framer for the rest of the connection. |

## Running them

Atheris publishes wheels for Linux only, so on macOS or Windows use a container:

```bash
docker run --rm -it -v "$PWD":/src:ro -w /tmp/work python:3.14-slim bash -c '
  pip install uv
  export UV_PROJECT_ENVIRONMENT=/opt/venv VIRTUAL_ENV=/opt/venv PYTHONPATH=/src
  cd /src && uv sync --frozen --no-install-project && uv pip install atheris
  cd /tmp/work && /opt/venv/bin/python /src/fuzz/fuzz_health_report.py -max_total_time=60
'
```

On Linux the venv is enough:

```bash
uv sync --frozen
uv pip install atheris
uv run python fuzz/fuzz_health_report.py -max_total_time=60
```

Pass a directory as the first argument to keep a corpus between runs, and everything
after it goes to libFuzzer: `-max_total_time`, `-runs`, `-max_len`,
`-print_final_stats=1`.

## In CI

`.github/workflows/fuzz.yml` runs every target: two minutes each on a pull request
that touches this code, fifteen minutes each every Tuesday, and as long as you ask
for via *Run workflow*. The corpus is cached per target, so the scheduled run
continues where the previous one stopped instead of starting from an empty corpus.

A crash fails the job and the offending input is uploaded as an artifact named
`crash-<target>`. To reproduce it, download the file and run the target against it:

```bash
uv run python fuzz/fuzz_health_report.py ./crash-8267a8e1114b4217aa8f9e2c4497ecbafcb6ac87
```

Then turn it into a normal test case in `kronoterm2mqtt/tests/` before fixing it, so
the crash stays fixed once the corpus is gone. `test_health_command.py` has an
example: `test_a_report_without_the_expected_keys_is_still_rendered` came from this
fuzzer finding that an empty report raised `KeyError`.

## Not ClusterFuzzLite

ClusterFuzzLite would be the obvious way to run this, and would also satisfy the
OpenSSF Scorecard fuzzing check, but its Python base image
(`gcr.io/oss-fuzz-base/base-builder-python`) ships Python 3.11 and this project
requires 3.12 or newer. Revisit when that image is updated.
