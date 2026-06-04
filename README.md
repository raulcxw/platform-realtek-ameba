# platform-realtek-ameba

[English](README.md) | [中文](README_zh.md)

PlatformIO platform for the **Realtek Ameba** family of Wi-Fi + Bluetooth
Low Power IoT SoCs, backed by the official `ameba-rtos` SDK.

## Approach

This platform is a thin glue layer (~600 LoC) on top of the upstream
`ameba.py` driver. CMake, Ninja, the asdk/vsdk toolchain, and per-SoC
build logic all stay inside `ameba-rtos` — PlatformIO calls
`ameba.py build` and `ameba.py flash` and copies the resulting
`app.bin` into the standard `.pio/build/<env>/` location.

That keeps the platform aligned with whatever the SDK ships, instead of
re-implementing the build system separately.

## Supported boards

| Board | SoC | Spec | Status |
|---|---|---|---|
| **PKE8721DAF-C13-F10** | RTL8721Dx  | Cortex-M33 dual-core (KM4 345 MHz + KM0), Wi-Fi 4 + BLE 5.0 | ✅ build / flash / monitor verified |
| **PKE8710ECF-C53-F20** | RTL8710E   | Cortex-M33 (400 MHz) + KR4, Wi-Fi 6 + BLE 5.2 | 🟡 board file present, not yet hardware-verified |
| **PKE8713ECM-VA4-N43** | RTL8713E   | HiFi5 + Cortex-M33 + KR4, Wi-Fi 6 + BLE 5.2 + audio DSP | ✅ build / flash / monitor verified |

## Quick start (clean machine)

Step-by-step from a brand-new Ubuntu/WSL2 machine to a flashed board.

### 1. Install PlatformIO Core

If you don't have `pio` already:

```bash
# Install PIO Core
curl -fsSL -o get-platformio.py \
    https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py
python3 get-platformio.py

# Make `pio` reachable from any shell (PIO recommended way)
mkdir -p ~/.local/bin
ln -sf ~/.platformio/penv/bin/pio ~/.local/bin/pio
ln -sf ~/.platformio/penv/bin/platformio ~/.local/bin/platformio

# Verify (your shell needs ~/.local/bin in PATH; restart shell if not)
pio --version
```

### 2. Install this platform

From source (registry release pending):

```bash
git clone https://github.com/raulcxw/platform-realtek-ameba.git
pio pkg install -g -p "file://$(pwd)/platform-realtek-ameba"
```

> The `-g` flag installs globally to `~/.platformio/platforms/`.
> The Ameba SDK (`framework-ameba-rtos`) is **not** downloaded yet —
> it's fetched lazily on first `pio run`.

### 3. Create a project

```bash
mkdir my-ameba-app && cd my-ameba-app
cat > platformio.ini <<'EOF'
[env:pke8721daf-c13-f10]
platform     = realtek-ameba
framework    = ameba-rtos
board        = pke8721daf-c13-f10

; ── Serial port (REQUIRED for upload / monitor) ───────────────
; Auto-detected if you only have one USB serial device. Set
; explicitly if you have multiple boards or it's not /dev/ttyUSB0:
;
;   Linux/WSL: /dev/ttyUSB0   (Prolific PL2303, common Ameba dev kit)
;              /dev/ttyACM0   (CDC-ACM, e.g. CMSIS-DAP USB)
;   macOS:     /dev/cu.usbserial-XXXXX
;   Windows:   COM3, COM4, ...
;
; upload_port  = /dev/ttyUSB0
; monitor_port = /dev/ttyUSB0
EOF
```

> **Available boards** (replace `pke8721daf-c13-f10` above):
> - `pke8721daf-c13-f10` — RTL8721Dx (most common dev board)
> - `pke8710ecf-c53-f20` — RTL8710E
> - `pke8713ecm-va4-n43` — RTL8713E

### 4. Build

```bash
pio run
```

First-time build downloads:
- The Ameba **base SDK** (~30 MB shallow clone — Wi-Fi + BT, no submodules)
- The asdk/vsdk toolchain (~280 MB per family) into `~/rtk-toolchain/`

Cold first build: ~5 minutes. After that incremental builds: ~15 seconds.

Need the high-level **XDK** features (AI voice, TensorFlow Lite, UI/LVGL,
audio)? Set `AMEBA_SDK_EDITION=xdk` **before the first build** — see
[SDK editions](#sdk-editions-base-sdk-vs-xdk) below.

The first build also auto-creates `src/main.c` (a hello-world template
showing the correct `xTaskCreate` pattern) and the SDK's required
`app_example/` directory.

### 5. Connect the board and identify the serial port

Plug your dev board into USB, then:

```bash
pio device list
# Look for the board's serial port. Typical entries:
#   /dev/ttyUSB0  ← Prolific PL2303 (common Ameba LogUART bridge)
#   /dev/ttyACM0  ← CMSIS-DAP onboard debugger
```

If `pio device list` shows multiple ports, set `upload_port` and
`monitor_port` explicitly in `platformio.ini` (see the commented
section in step 3).

### 6. Flash

```bash
pio run -t upload
```

If you see "board not in download mode" errors, try pressing the
board's RESET button as `pio run -t upload` starts. USB-UART chips
without DTR/RTS auto-reset wiring (PL2303) need this manual nudge;
CP2102 / CH340 / CMSIS-DAP usually auto-reset.

### 7. Monitor

```bash
pio device monitor
```

Default baudrate 1.5 Mbps (Ameba LogUART convention) is preset on the
supplied boards. Exit with `Ctrl-C`.

## SDK editions (base SDK vs XDK)

Realtek splits ameba-rtos into two editions:

| Edition | Contents | First-time download |
|---|---|---|
| **SDK** (default) | Wi-Fi, Bluetooth — the base development platform | ~30 MB |
| **XDK** (extended) | Everything in SDK **plus** AI voice, TensorFlow Lite (tflite_micro), UI (LVGL), audio | ~1.1 GB |

Most projects only need the base SDK, so it is cloned by default. To get the
extended edition, set the environment variable **before the first build**
(the choice is consumed once, when the SDK is cloned):

```bash
# Linux / macOS
AMEBA_SDK_EDITION=xdk pio run

# Windows (PowerShell)
$env:AMEBA_SDK_EDITION="xdk"; pio run
```

```yaml
# CI (GitHub Actions)
- run: pio run
  env:
    AMEBA_SDK_EDITION: xdk
```

**Already cloned the base SDK and want one extra component later?** The
edition variable only acts at first clone, so add the submodule directly
(shallow):

```bash
cd ~/.platformio/packages/framework-ameba-rtos
git submodule update --init --depth 1 component/audio    # or component/ui, etc.
```

To switch the whole package from SDK to XDK after the fact, reinstall:

```bash
pio pkg uninstall -g -p framework-ameba-rtos
AMEBA_SDK_EDITION=xdk pio run
```

> Pointing at a local checkout with `$AMEBA_SDK_DIR`? Then the edition
> variable is ignored — you manage submodules in that checkout yourself.

## Updating the SDK

```bash
pio pkg update -p framework-ameba-rtos
```

That's it — your next `pio run` will automatically refresh any new
Python dependencies the SDK requires. No `source env.sh`, no manual
`pip install`. The platform tracks `tools/requirements.txt` by
content hash and resyncs the SDK's `.venv` whenever the file changes.

If you ever need to force a venv rebuild (e.g. after manually running
`pip uninstall` inside the SDK venv):

```bash
rm ~/.platformio/packages/framework-ameba-rtos/.venv/.pio_requirements_sha256
pio run
```

## PlatformIO command support

| Command | Supported | Notes |
|---|:---:|---|
| `pio run` (build) | ✅ | Delegated to `ameba.py build` |
| `pio run -t upload` | ✅ | Delegated to `ameba.py flash` |
| `pio run -t erase` | ✅ | Wipes entire SPI flash + reflashes the current project |
| `pio run -t clean` | ✅ | Wipes both `.pio/build/<env>/` AND `<PROJECT>/build_<SOC>/` |
| `pio run -t buildfs` | ✅ | Pack `data/` into LittleFS image sized to VFS1 partition |
| `pio run -t uploadfs` | ✅ | Flash the LittleFS image to VFS1 (does not touch app/boot) |
| `pio run -t menuconfig` | ✅ | Hands off to `ameba.py menuconfig <SOC>` curses UI |
| `pio run -t size` | ✅ | Multi-core program size (KM4 + KM0/KR4 aggregated) |
| `pio device monitor` | ✅ | Standard PIO miniterm; defaults to 1.5 Mbps LogUART |
| `pio check` | ✅ | cppcheck / clang-tidy can resolve SDK headers |
| VSCode IntelliSense | ✅ | Auto-exports `compile_commands.json` from the SDK build |
| Multi-env parallel (`pio run -e a -e b`) | ✅ | Per-env `TARGET_SOC`, no shared-state races |
| `pio debug` (GDB) | 🟡 | OpenOCD/J-Link wired in board manifests; not yet hardware-verified |
| `pio test` (unity) | 🔴 | Planned |
| OTA upload | 🔴 | Planned |
| `pio lib install` / `lib_deps` | ❌ | Not supported by design — Ameba components live in the SDK source tree, not as PIO libraries |

## Layout

```
platform.json              PIO platform manifest
platform.py                RealtekamebaPlatform(PlatformBase) — pulls SDK + venv, registers debug tools
builder/main.py            SCons entry; shells out to ameba.py, copies build_<SOC>/app.bin into PIO BUILD_DIR
builder/frameworks/        Framework discovery
boards/<board>.json        Per-board manifests (MCU series, debug, upload defaults)
tests/                     Regression suite (lint + unit + integration)
.github/workflows/ci.yml   GitHub Actions: runs lint + unit + integration on every push
```

## Requirements

- Linux (tested on Ubuntu / WSL2)
- Python 3.9+
- PlatformIO Core 6.x
- A working clone of `ameba-rtos` is fetched automatically on first build

## Testing

Regression tests live in `tests/` (lint + unit + integration + hardware
smoke). See [`tests/README.md`](tests/README.md) for how to run locally;
GitHub Actions runs lint + unit + integration on every push.

## License

Apache-2.0 (matches the upstream PlatformIO ecosystem).
