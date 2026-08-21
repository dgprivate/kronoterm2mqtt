# Contributing

Patches are welcome. This file says what a change has to satisfy before it can be
merged, so that the answer is knowable in advance rather than discovered in review.

## Where to send a change

This repository is a fork of [kosl/kronoterm2mqtt](https://github.com/kosl/kronoterm2mqtt).
Anything that is not specific to the published container image belongs upstream, and
changes merged here are offered back there. Open the pull request wherever it fits;
if it lands in the wrong place, it will be forwarded rather than closed.

## Setting up

```bash
git clone --recursive https://github.com/dgprivate/kronoterm2mqtt.git
cd kronoterm2mqtt
./dev-cli.py --help          # creates the development virtualenv on first call
```

The `--recursive` matters: `kronoterm2mqtt/pyetera_uart_bridge` is a symlink into the
`etera-uart-bridge` submodule, and the package will not import without it.

Python **3.12 or newer** (`requires-python` in `pyproject.toml`). The test matrix runs
3.12, 3.13 and 3.14, so a change has to work on all three.

## What a change has to satisfy

| Requirement | How to check |
|---|---|
| Tests pass | `./dev-cli.py test` |
| Coverage stays at or above 95% | `./dev-cli.py coverage` (`fail_under` in `pyproject.toml`) |
| Code style is clean | `./dev-cli.py lint` runs `ruff check --fix` |
| No known vulnerable dependencies | `./dev-cli.py pip-audit` |

CI runs all of it on every pull request, and `test_code_style` fails the suite on a
style violation, so `lint` is not optional.

### Style

Configured in `pyproject.toml`, not by preference:

* line length 120, single quotes (`[tool.ruff.format]`)
* `select = ["F", "E", "I"]` - pyflakes, pycodestyle errors, import sorting
* imports sorted with `force-sort-within-sections`, two blank lines after the import block
* `ruff` is pinned through `uv.lock`, so it behaves the same locally and in CI

Install the git hooks once and the README history block stays generated for you:

```bash
./dev-cli.py install
```

### Tests

New behaviour needs a test, and a bug fix needs the test that fails without it. The
suite runs offline: `deny_any_real_request()` blocks outbound sockets, so tests that
need a peer start one on loopback - see `kronoterm2mqtt/tests/loopback.py` and the fake
Modbus gateway in `test_modbus_greeting.py`, or the `FakeEtera` stand-in for the Arduino
expander in `test_expander.py`.

Do not write a test that depends on wall-clock behaviour of the machine running it. One
did, using `time.monotonic()` as if it were seconds since boot, and it passed locally
while failing on a freshly started CI runner.

### Adding heat pump registers

Sensors, switches and selects are data, not code: add them to
`kronoterm2mqtt/definitions/kronoterm_ksm.toml`, with the register number as KRONOTERM
documents it (one-based; the code subtracts one). Register numbers and their meaning
come from the manufacturer documentation kept in the repository - see
[References](README.md#references). `test_user_settings.py` checks that every sensor
carries the keys the publish loop reads and that names stay unique.

### Commits

Explain why the change is needed, not what the diff shows. If a fix corrects earlier
behaviour, say what that behaviour was - the commit message is where the next person
looks when the same problem comes back.

## Security

Do not report a vulnerability in a pull request or a public issue. [`SECURITY.md`](SECURITY.md)
says how, and [`security/README.md`](security/README.md) describes what the project claims
about the container it publishes and what it deliberately does not.
