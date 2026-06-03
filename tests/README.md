# Tests

Regression test suite for `platform-realtek-ameba`. Three layers:

```
Layer 1: lint         ~10s    static checks (Python syntax, JSON, board manifests)
Layer 2a: unit        ~1min   mock-based Python tests, no SDK needed
Layer 2b: integration ~5-10m  real SDK + toolchain, runs across all 3 boards
Layer 3: hw_smoke     manual  hardware-in-the-loop, run before each release
```

Layers 1+2 run on every push/PR via GitHub Actions
(`.github/workflows/ci.yml`). Layer 3 is for the platform author
to spot-check before tagging a release.

## Run locally

### Layer 1 — lint (anyone, anywhere)

No deps beyond `python3`:

```bash
./tests/lint.sh
```

Optional: install `ruff` for unused-import / undefined-name checks
(`pip install ruff`); skipped silently if not present.

### Layer 2a — Python unit tests

Needs `pytest` + `pytest-mock`. Easiest is to use the SDK's own venv:

```bash
~/.platformio/packages/framework-ameba-rtos/.venv/bin/pip install --quiet pytest pytest-mock

cd tests/    # important: avoid platform.py vs stdlib `platform` clash
~/.platformio/packages/framework-ameba-rtos/.venv/bin/python -m pytest unit/ -v
```

Or in your own venv:

```bash
python3 -m venv .test-venv
.test-venv/bin/pip install pytest pytest-mock
cd tests/
../.test-venv/bin/python -m pytest unit/ -v
```

### Layer 2b — integration tests

Requires PIO Core installed and reachable on `$PATH`:

```bash
# All boards × all tests
for board in pke8721daf-c13-f10 pke8710ecf-c53-f20 pke8713ecm-va4-n43; do
    TEST_BOARD=$board ./tests/integration/01_install.sh
done

# Single test on a specific board
TEST_BOARD=pke8721daf-c13-f10 ./tests/integration/01_install.sh
```

First run (cold cache): ~10 minutes per board for SDK clone + toolchain
download. Subsequent runs reuse `~/.platformio/packages/framework-ameba-rtos`
and `~/rtk-toolchain`, so each test ~30 seconds.

### Layer 3 — hardware smoke (manual)

Plug in a board (USB-UART → `/dev/ttyUSB0`), then:

```bash
./tests/hw_smoke.sh
```

The script walks you through erase / upload / monitor verification
interactively. Run before tagging a release.

## Layout

```
tests/
├── README.md                       # this file
├── lint.sh                         # Layer 1
├── unit/
│   └── test_erase_fail_detection.py   # T07: silent-failure detection
└── integration/
    └── 01_install.sh               # T01: pio platform install + first build
```

More tests will arrive as Phase B / Phase C of the regression plan
ships (see `.hermes/plans/2026-06-03_ci-regression-tests.md`).

## What each test prevents

| ID  | Bug it prevents                                                   | Test type   |
|-----|--------------------------------------------------------------------|-------------|
| T07 | erase silently passes when board not in download mode             | unit (mock) |
| T01 | `pio platform install` doesn't trigger SDK + venv + auto-skeleton | integration |

(More rows to come as we ship Phase B / C.)
