# platform-realtek-ameba

PlatformIO platform for the **Realtek Ameba** family of Wi-Fi + Bluetooth
IoT MCUs, backed by the official `ameba-rtos` SDK.

> **Status:** v0.2.0-dev — Linux only. RTL8721F (AmebaGreen2) verified
> end-to-end via `ameba.py build` + remote-serial flash. v0.2 brings
> VSCode IntelliSense, `pio run -t monitor_ameba`, `pio run -t menuconfig`,
> `pio run -t ameba-clean`, and safe multi-env parallel builds.

## Why a new platform?

The existing community options are:

- **`platform-realtek-ameba`** (8devices, 2017): targets RTL8710 only, links
  to dead Bintray binaries, uses PlatformIO v3 conventions.
- **`LibreTiny`**: covers RTL8710BN / AmebaZ2 well, but does not track the
  upstream Realtek SDK and has no path forward to RTL8721F / RTL8730E.

This platform takes a different approach: **delegate everything to the
upstream `ameba.py`** (CMake + Ninja + asdk toolchain are SDK-managed). The
glue layer is ~200 LoC instead of LibreTiny's full reimplementation.

## Supported SoCs (planned)

| SoC | Series | Toolchain | Status |
|---|---|---|---|
| RTL8721F | AmebaGreen2 | asdk-12.3.1 | ✅ build works |
| RTL8730E | AmebaSmart | asdk-12.3.1 | ⏳ not started |
| RTL8720F | AmebaPro2 | asdk-12.3.1 | ⏳ not started |
| RTL8710BN/F | AmebaZ2 | asdk-10.3.1 | ⏳ not started |
| RTL8711F | AmebaLite | asdk-12.3.1 | ⏳ not started |

## Quick start (developer mode)

```bash
# 1. Install platform from local checkout
pio platform install file:///path/to/platform-realtek-ameba

# 2. New project
mkdir my-ameba-app && cd my-ameba-app
cat > platformio.ini <<'EOF'
[env:rtl8721f]
platform = realtek-ameba
framework = ameba-rtos
board = rtl8721f
EOF

# 3. Build
pio run

# 4. Upload (once flash pipeline lands)
pio run -t upload
```

## Internals

- `platform.json`: PlatformIO platform manifest, declares `ameba-rtos` framework
  and toolchain packages.
- `platform.py`: Sub-classes `PlatformBase`, picks the right asdk version
  per SoC, registers OpenOCD/JLink debug tools.
- `builder/main.py`: SCons entry. Shells out to `ameba.py build` /
  `ameba.py flash`, copies `build_<SOC>/app.bin` into PIO's `BUILD_DIR`.
- `builder/frameworks/ameba-rtos.py`: Framework-discovery stub.
- `boards/<soc>.json`: Per-SoC board manifests.

## PIO feature support — at a glance

| Feature | v0.1 | v0.2 | Notes |
|---|:---:|:---:|---|
| `pio run` (build) | ✅ | ✅ | RTL8721F verified end-to-end |
| `pio run -t upload` | ✅ | ✅ | Local + remote-serial (`board_upload.remote_server`) |
| `pio run -t ameba-clean` | 🟡 | ✅ | v0.2: cleans SDK + `.pio/` + project-root `compile_commands.json` |
| `pio run -t monitor_ameba` | 🔴 | ✅ | v0.2: `ameba.py monitor`, supports remote serial (verified on real COM40) |
| `pio run -t menuconfig` | 🔴 | ✅ | v0.2: hands off to `ameba.py menuconfig <SOC>` curses UI |
| **VSCode IntelliSense** | 🔴 | ✅ | v0.2: auto-exports `compile_commands.json` (627 entries, 7.1 MB) |
| `pio debug` (GDB) | 🟡 | 🟡 | Stubbed in `platform.py`, not yet hardware-verified |
| Multi-env parallel (`pio run -e a -e b`) | 🟡 | ✅ | v0.2: per-env `TARGET_SOC` env var, no more `soc_info.json` race |
| `pio test` (unity) | 🔴 | 🔴 | v1.0+ |
| OTA upload | 🔴 | 🔴 | v0.3 planned |
| `pio lib install` / `lib_deps` | ❌ | ❌ | **Intentionally not supported** — see [ARCH.md §3.4](./ARCH.md) |

For the full architecture-boundary comparison against `platform-espressif32`, including the design rationale, the IntelliSense fix, and the v0.3+ roadmap, **read [`ARCH.md`](./ARCH.md)**.

## License

Apache-2.0 (matches the upstream PlatformIO ecosystem).
