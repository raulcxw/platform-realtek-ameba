# platform-realtek-ameba

PlatformIO platform for the **Realtek Ameba** family of Wi-Fi + Bluetooth
Low Power IoT SoCs, backed by the official `ameba-rtos` SDK.

## Approach

This platform is a thin glue layer (~200 LoC) on top of the upstream
`ameba.py` driver. CMake, Ninja, the asdk toolchain, and per-SoC build
logic all stay inside `ameba-rtos` — PlatformIO calls `ameba.py build`
and `ameba.py flash` and copies the resulting `app.bin` into the standard
`.pio/build/<env>/` location.

That keeps the platform aligned with whatever the SDK ships, instead of
re-implementing the build system separately.

## Supported boards

| Board | SoC | Spec | Status |
|---|---|---|---|
| **PKE8721DAF-C13-F10** | RTL8721Dx  | Cortex-M33 dual-core (KM4 345 MHz + KM0), Wi-Fi 4 + BLE 5.0 | ✅ build / flash / monitor verified |
| **PKE8710ECF-C53-F20** | RTL8710E   | Cortex-M33 (400 MHz) + KR4, Wi-Fi 6 + BLE 5.2 | 🟡 board file present, not yet hardware-verified |
| **PKE8713ECM-VA4-N43** | RTL8713E   | HiFi5 + Cortex-M33 + KR4, Wi-Fi 6 + BLE 5.2 + audio DSP | 🟡 board file present, not yet hardware-verified |

## Quick start

```bash
# 1. Install platform from local checkout (registry release pending)
pio platform install file:///path/to/platform-realtek-ameba

# 2. New project
mkdir my-ameba-app && cd my-ameba-app
cat > platformio.ini <<'EOF'
[env:pke8721daf-c13-f10]
platform = realtek-ameba
framework = ameba-rtos
board = pke8721daf-c13-f10
EOF

# 3. Build
pio run

# 4. Flash (USB)
pio run -t upload

# 5. Flash via remote serial server (Windows COM relayed to Linux)
#    Add to platformio.ini under [env:...]:
#      board_upload.remote_server   = 127.0.0.1
#      board_upload.remote_password = ********
#      board_upload.remote_port     = COM40
pio run -t upload

# 6. Open serial monitor (1.5 Mbps LogUART by default; remote-serial aware)
pio run -t monitor_ameba
```

## PIO feature support

| Feature | Supported | Notes |
|---|:---:|---|
| `pio run` (build) | ✅ | Delegated to `ameba.py build` |
| `pio run -t upload` | ✅ | Local USB and remote-serial server |
| `pio run -t monitor_ameba` | ✅ | Wraps `ameba.py monitor`, remote-serial aware |
| `pio run -t menuconfig` | ✅ | Hands off to `ameba.py menuconfig <SOC>` curses UI |
| `pio run -t ameba-clean` | ✅ | Cleans SDK build, `.pio/`, and stale `compile_commands.json` |
| VSCode IntelliSense | ✅ | Auto-exports `compile_commands.json` from the SDK build |
| Multi-env parallel (`pio run -e a -e b`) | ✅ | Per-env `TARGET_SOC`, no shared-state races |
| `pio debug` (GDB) | 🟡 | OpenOCD/JLink wired in `platform.py`, not yet hardware-verified |
| OTA upload | 🔴 | Planned |
| `pio test` (unity) | 🔴 | Planned |
| `pio lib install` / `lib_deps` | ❌ | Not supported by design — Ameba components live in the SDK source tree, not as PIO libraries |

## Layout

```
platform.json              PIO platform manifest
platform.py                RealtekamebaPlatform(PlatformBase) — picks asdk version per SoC, registers debug tools
builder/main.py            SCons entry; shells out to ameba.py, copies build_<SOC>/app.bin into PIO BUILD_DIR
builder/frameworks/        Framework discovery
boards/<board>.json        Per-board manifests (mcu series, debug, upload defaults)
```

## Requirements

- Linux (tested on Ubuntu / WSL2)
- Python 3.9+
- PlatformIO Core 6.x
- A working clone of `ameba-rtos` is fetched automatically on first build

## License

Apache-2.0 (matches the upstream PlatformIO ecosystem).
