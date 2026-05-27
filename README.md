# platform-amebartos

PlatformIO platform for the **Realtek Ameba** family of Wi-Fi + Bluetooth
IoT MCUs, backed by the official `ameba-rtos` SDK.

> **Status:** v0.1-dev — Linux only, RTL8721F (AmebaGreen2) verified to
> compile end-to-end via `ameba.py build`. Flash / debug pipelines drafted
> but not yet smoke-tested.

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
pio platform install file:///path/to/platform-amebartos

# 2. New project
mkdir my-ameba-app && cd my-ameba-app
cat > platformio.ini <<'EOF'
[env:rtl8721f]
platform = amebartos
framework = ambsdk
board = rtl8721f
EOF

# 3. Build
pio run

# 4. Upload (once flash pipeline lands)
pio run -t upload
```

## Internals

- `platform.json`: PlatformIO platform manifest, declares `ambsdk` framework
  and toolchain packages.
- `platform.py`: Sub-classes `PlatformBase`, picks the right asdk version
  per SoC, registers OpenOCD/JLink debug tools.
- `builder/main.py`: SCons entry. Shells out to `ameba.py build` /
  `ameba.py flash`, copies `build_<SOC>/app.bin` into PIO's `BUILD_DIR`.
- `builder/frameworks/ambsdk.py`: Framework-discovery stub.
- `boards/<soc>.json`: Per-SoC board manifests.

## License

Apache-2.0 (matches the upstream PlatformIO ecosystem).
