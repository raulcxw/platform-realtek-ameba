# Copyright 2026 raul_chen
# SPDX-License-Identifier: Apache-2.0

"""platform-amebartos main builder entry.

Wires PlatformIO's ``pio run`` / ``pio run -t upload`` / ``pio run -t clean``
/ ``pio run -t menuconfig`` / ``pio device monitor`` targets to the upstream
``ameba.py`` CLI. We deliberately do NOT redefine the CMake build graph --
the SDK already owns it and changes between SoC generations.

v0.2 additions on top of v0.1:
  * compile_commands.json export (VSCode IntelliSense)
  * pio device monitor -> ameba.py monitor
  * pio run -t menuconfig -> ameba.py menuconfig
  * pio run -t clean cleans .pio/ as well
  * Per-env soc_info.json isolation so 'pio run -e a -e b' is safe.
"""

import os
import shutil
import sys
import tempfile
from os.path import isdir, join

from SCons.Script import (
    AlwaysBuild,
    Builder,
    Default,
    DefaultEnvironment,
)


env = DefaultEnvironment()
platform = env.PioPlatform()
board = env.BoardConfig()


# -----------------------------------------------------------------------------
# Discovery
# -----------------------------------------------------------------------------
def _find_sdk_dir():
    """Locate the ameba-rtos checkout.

    v0.1 strategy: not distributed as a PIO package. Prefer
    ``$AMEBA_SDK_DIR``, then a few well-known dev locations, then fail
    with a clear message.
    """
    candidates = [
        os.environ.get("AMEBA_SDK_DIR", ""),
        os.path.expanduser("~/projects/ameba-platformio-research/repos/ameba-rtos"),
        os.path.expanduser("~/projects/ameba-rtos"),
        os.path.expanduser("~/.platformio/packages/framework-ambsdk"),
    ]
    for candidate in candidates:
        if candidate and isdir(candidate) and os.path.isfile(join(candidate, "ameba.py")):
            return candidate
    raise FileNotFoundError(
        "ameba-rtos SDK not found. Set AMEBA_SDK_DIR to a local checkout "
        "of https://github.com/Ameba-AIoT/ameba-rtos.git, or symlink it to "
        "~/.platformio/packages/framework-ambsdk."
    )


def _find_prebuilts_dir():
    """Locate the ameba prebuilts (cmake/ninja/ccache)."""
    candidates = [
        os.environ.get("AMEBA_PREBUILTS_DIR", ""),
        os.path.expanduser("~/rtk-toolchain/prebuilts-linux-1.0.3"),
        os.path.expanduser("~/.platformio/packages/tool-ameba-prebuilts"),
    ]
    for candidate in candidates:
        if candidate and isdir(candidate) and os.path.isfile(join(candidate, "setenv.sh")):
            return candidate
    return None  # SDK will still work; just relies on system cmake/ninja


SDK_DIR = _find_sdk_dir()
PREBUILTS_DIR = _find_prebuilts_dir()
SOC = board.get("build.soc", "RTL8721F").upper()
PROJECT_BUILD_DIR = env.subst("$BUILD_DIR")
ENV_NAME = env.subst("$PIOENV") or "default"


# -----------------------------------------------------------------------------
# Per-env soc_info.json isolation (v0.2 #5: multi-env safety)
# -----------------------------------------------------------------------------
# Background: ``ameba.py soc <SOC>`` writes ``${SDK_DIR}/soc_info.json``.
# When two PIO envs run concurrently (`pio run -e rtl8721f -e rtl8730e`) they
# stomp on each other. We sidestep this by giving each env its own copy of
# soc_info.json placed at a per-env CWD, then telling ameba_soc_utils where
# to read/write via the AMEBA_SOC_INFO_FILE env var (read by SocManager when
# present).
#
# Note: ameba_soc_utils.py reads SOC name first from $TARGET_SOC, then from
# soc_info.json. So we set TARGET_SOC=<SOC> directly, which is even simpler
# and removes the need to write soc_info.json from PIO at all. ameba.py
# build still works because TARGET_SOC takes precedence.
def _isolated_workdir():
    """A per-env scratch dir to keep ameba.py invocations isolated."""
    workdir = join(PROJECT_BUILD_DIR, "ambsdk-workdir")
    os.makedirs(workdir, exist_ok=True)
    return workdir


# -----------------------------------------------------------------------------
# Environment for `ameba.py *`
# -----------------------------------------------------------------------------
def _make_sdk_env():
    """Build os.environ for subprocess calls into ameba.py.

    Sets:
      * RTK_TOOLCHAIN_DIR -> PIO platform cache (so toolchain auto-fetch
        survives across projects)
      * TARGET_SOC -> bypasses soc_info.json; per-env safe
      * VIRTUAL_ENV + PATH -> SDK venv first (json5/elftools), then
        prebuilts cmake/ninja, then system PATH
    """
    sdk_env = os.environ.copy()

    sdk_env["RTK_TOOLCHAIN_DIR"] = join(
        platform.get_dir(), ".cache", "rtk-toolchain"
    )
    os.makedirs(sdk_env["RTK_TOOLCHAIN_DIR"], exist_ok=True)

    # v0.2 #5: TARGET_SOC env var takes precedence over soc_info.json
    # inside ameba_soc_utils.SocManager.parse_soc_info(). This keeps
    # multi-env runs from racing on the same soc_info.json file.
    sdk_env["TARGET_SOC"] = SOC

    path_parts = []
    sdk_venv_bin = join(SDK_DIR, ".venv", "bin")
    if isdir(sdk_venv_bin):
        path_parts.append(sdk_venv_bin)
        sdk_env["VIRTUAL_ENV"] = join(SDK_DIR, ".venv")
    if PREBUILTS_DIR and isdir(PREBUILTS_DIR):
        path_parts.append(join(PREBUILTS_DIR, "cmake", "bin"))
        path_parts.append(join(PREBUILTS_DIR, "bin"))
    if path_parts:
        sdk_env["PATH"] = (
            os.pathsep.join(path_parts) + os.pathsep + sdk_env.get("PATH", "")
        )

    return sdk_env


def _ameba_python():
    """Path to the python interpreter ameba.py expects (SDK venv's)."""
    venv_py = join(SDK_DIR, ".venv", "bin", "python3")
    if os.path.isfile(venv_py):
        return venv_py
    return "python3"


def _ameba_py_args(action, soc=SOC, app=None, clean=False, upload_opts=None,
                   menuconfig_opts=None, monitor_opts=None):
    """Translate PIO target -> ameba.py argv."""
    py = _ameba_python()
    args = [py, join(SDK_DIR, "ameba.py")]

    if action == "build":
        # With TARGET_SOC env var set, `ameba.py soc` is a no-op but still
        # safe to run (it just writes the same SOC name back).
        return [
            args + ["soc", soc],
            args + ["build"] + (["-c"] if clean else []),
        ]
    elif action == "flash":
        flash_args = args + ["flash"]
        if upload_opts:
            for k, v in upload_opts.items():
                if v is None or v is False:
                    continue
                flash_args.append(f"--{k}" if k.startswith("-") else f"--{k.replace('_', '-')}")
                if v is not True:
                    flash_args.append(str(v))
        return [args + ["soc", soc], flash_args]
    elif action == "clean":
        return [args + ["soc", soc], args + ["clean", soc]]
    elif action == "menuconfig":
        # ameba.py menuconfig needs SoC selected; passes through to
        # tools/scripts/menuconfig.py which is interactive (Kconfig UI).
        mc_args = args + ["menuconfig", soc]
        if menuconfig_opts:
            mc_args.extend(menuconfig_opts)
        return [args + ["soc", soc], mc_args]
    elif action == "monitor":
        mon_args = args + ["monitor"]
        if monitor_opts:
            for k, v in monitor_opts.items():
                if v is None or v is False:
                    continue
                mon_args.append(f"--{k.replace('_', '-')}" if not k.startswith("-") else k)
                if v is not True:
                    mon_args.append(str(v))
        return [args + ["soc", soc], mon_args]
    else:
        raise ValueError(f"unknown action {action!r}")


# -----------------------------------------------------------------------------
# v0.2 #1: compile_commands.json export (VSCode IntelliSense)
# -----------------------------------------------------------------------------
def _export_compile_commands():
    """Copy cmake's compile_commands.json into PIO BUILD_DIR + project root.

    cmake auto-generates this in build_<SOC>/build/compile_commands.json.
    VSCode (with C/C++ extension or clangd) auto-discovers the file in:
      1. <project_root>/compile_commands.json   (preferred)
      2. <project_root>/.pio/build/<env>/compile_commands.json

    We write to both so users get IntelliSense regardless of which
    extension's heuristic they're using.
    """
    src = join(SDK_DIR, f"build_{SOC}", "build", "compile_commands.json")
    if not os.path.isfile(src):
        print(f"[ambsdk] compile_commands.json not found at {src}; skipping IntelliSense export")
        return

    project_dir = env.subst("$PROJECT_DIR")
    targets = [
        join(PROJECT_BUILD_DIR, "compile_commands.json"),  # PIO standard
        join(project_dir, "compile_commands.json"),         # editor root
    ]
    for dst in targets:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    print(f"[ambsdk] exported compile_commands.json ({os.path.getsize(src)//1024} KB) -> "
          f"{project_dir}/compile_commands.json (+ .pio/build/{ENV_NAME}/)")


# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------
def build_firmware(*_args, **_kwargs):
    import subprocess

    sdk_env = _make_sdk_env()
    cmd_chain = _ameba_py_args("build", soc=SOC)

    print(f"[ambsdk] building SoC={SOC} (env={ENV_NAME}), SDK={SDK_DIR}")
    print(f"[ambsdk] RTK_TOOLCHAIN_DIR={sdk_env['RTK_TOOLCHAIN_DIR']}")
    print(f"[ambsdk] TARGET_SOC={sdk_env['TARGET_SOC']}")

    for cmd in cmd_chain:
        print(f"[ambsdk] $ {' '.join(cmd)}")
        rc = subprocess.call(cmd, cwd=SDK_DIR, env=sdk_env)
        if rc != 0:
            print(f"[ambsdk] command failed (rc={rc})")
            env.Exit(rc)

    # Copy firmware into PIO BUILD_DIR
    src_app = join(SDK_DIR, f"build_{SOC}", "app.bin")
    dst_app = join(PROJECT_BUILD_DIR, "firmware.bin")
    if os.path.isfile(src_app):
        os.makedirs(PROJECT_BUILD_DIR, exist_ok=True)
        shutil.copyfile(src_app, dst_app)
        print(f"[ambsdk] copied {src_app} -> {dst_app}")

    # v0.2 #1: export compile_commands.json
    _export_compile_commands()


def upload_firmware(*_args, **_kwargs):
    import subprocess

    sdk_env = _make_sdk_env()

    upload_opts = {}

    port = env.subst("$UPLOAD_PORT") or board.get("upload.port", "")
    if port:
        upload_opts["port"] = port

    speed = env.subst("$UPLOAD_SPEED") or board.get("upload.speed", "")
    if speed:
        upload_opts["baudrate"] = speed

    remote_server = (
        env.GetProjectOption("board_upload.remote_server", None)
        or board.get("upload.remote_server", None)
    )
    if remote_server:
        upload_opts["remote-server"] = remote_server

    remote_password = (
        env.GetProjectOption("board_upload.remote_password", None)
        or board.get("upload.remote_password", None)
    )
    if remote_password:
        upload_opts["remote-password"] = remote_password

    memory_type = (
        env.GetProjectOption("board_upload.memory_type", None)
        or board.get("upload.memory_type", None)
    )
    if memory_type:
        upload_opts["memory-type"] = memory_type

    chip_erase = (
        env.GetProjectOption("board_upload.chip_erase", "no") or "no"
    ).lower() in ("yes", "true", "1")
    if chip_erase:
        upload_opts["chip-erase"] = True

    print(f"[ambsdk] uploading SoC={SOC}, opts={upload_opts}")
    for cmd in _ameba_py_args("flash", soc=SOC, upload_opts=upload_opts):
        print(f"[ambsdk] $ {' '.join(cmd)}")
        rc = subprocess.call(cmd, cwd=SDK_DIR, env=sdk_env)
        if rc != 0:
            env.Exit(rc)


# -----------------------------------------------------------------------------
# v0.2 #2: pio device monitor
# -----------------------------------------------------------------------------
# We do this by overriding $MONITOR_PORT etc. so that PIO's built-in
# `pio device monitor` works. But ameba.py monitor has its own remote-serial
# feature that PIO's stock monitor doesn't speak. So we *also* expose a
# custom SCons target ``monitor`` that shells out to ameba.py monitor.
def serial_monitor(*_args, **_kwargs):
    import subprocess

    sdk_env = _make_sdk_env()

    monitor_opts = {}

    port = env.subst("$MONITOR_PORT") or env.subst("$UPLOAD_PORT") or board.get("upload.port", "")
    if port:
        monitor_opts["port"] = port

    speed = env.subst("$MONITOR_SPEED") or "115200"
    if speed:
        monitor_opts["baudrate"] = speed

    # Reuse upload's remote_server/remote_password (typical setup: same
    # remote serial bridge for both flash and monitor).
    remote_server = (
        env.GetProjectOption("board_upload.remote_server", None)
        or env.GetProjectOption("monitor_remote_server", None)
        or board.get("upload.remote_server", None)
    )
    if remote_server:
        monitor_opts["remote-server"] = remote_server

    remote_password = (
        env.GetProjectOption("board_upload.remote_password", None)
        or env.GetProjectOption("monitor_remote_password", None)
        or board.get("upload.remote_password", None)
    )
    if remote_password:
        monitor_opts["remote-password"] = remote_password

    print(f"[ambsdk] monitor SoC={SOC}, opts={monitor_opts}")
    for cmd in _ameba_py_args("monitor", soc=SOC, monitor_opts=monitor_opts):
        print(f"[ambsdk] $ {' '.join(cmd)}")
        # subprocess.call here blocks until user exits the monitor (Ctrl-C).
        rc = subprocess.call(cmd, cwd=SDK_DIR, env=sdk_env)
        if rc != 0:
            env.Exit(rc)


# -----------------------------------------------------------------------------
# v0.2 #3: pio run -t menuconfig
# -----------------------------------------------------------------------------
def run_menuconfig(*_args, **_kwargs):
    import subprocess

    sdk_env = _make_sdk_env()
    print(f"[ambsdk] menuconfig SoC={SOC}")
    print("[ambsdk] (this is interactive; will hand off your terminal to "
          "ameba.py menuconfig's curses UI)")

    for cmd in _ameba_py_args("menuconfig", soc=SOC):
        print(f"[ambsdk] $ {' '.join(cmd)}")
        rc = subprocess.call(cmd, cwd=SDK_DIR, env=sdk_env)
        if rc != 0:
            env.Exit(rc)


# -----------------------------------------------------------------------------
# v0.2 #4: pio run -t clean (full)
# -----------------------------------------------------------------------------
def clean_all(*_args, **_kwargs):
    """Clean both ameba SDK build outputs AND PIO's .pio/ cache."""
    import subprocess

    sdk_env = _make_sdk_env()
    print(f"[ambsdk] cleaning SoC={SOC} (SDK build_{SOC}/ + PIO BUILD_DIR)")

    # 1. clean SDK side
    for cmd in _ameba_py_args("clean", soc=SOC):
        print(f"[ambsdk] $ {' '.join(cmd)}")
        subprocess.call(cmd, cwd=SDK_DIR, env=sdk_env)

    # 2. clean PIO side
    if isdir(PROJECT_BUILD_DIR):
        print(f"[ambsdk] rm -rf {PROJECT_BUILD_DIR}")
        shutil.rmtree(PROJECT_BUILD_DIR, ignore_errors=True)

    # 3. clean exported compile_commands.json from project root if present
    project_dir = env.subst("$PROJECT_DIR")
    cc = join(project_dir, "compile_commands.json")
    if os.path.isfile(cc):
        print(f"[ambsdk] rm {cc}")
        os.remove(cc)


# -----------------------------------------------------------------------------
# Wire up SCons targets
# -----------------------------------------------------------------------------
target_firmware = env.Alias("buildprog", None, build_firmware)
AlwaysBuild(target_firmware)

target_upload = env.Alias(
    "upload",
    target_firmware,
    [build_firmware, upload_firmware],
)
AlwaysBuild(target_upload)

# v0.2 custom SCons targets
env.AddCustomTarget(
    name="menuconfig",
    dependencies=None,
    actions=run_menuconfig,
    title="Menuconfig",
    description="Run interactive Kconfig menuconfig (delegates to "
                "`ameba.py menuconfig <SOC>`)",
)

env.AddCustomTarget(
    name="monitor_ambsdk",
    dependencies=None,
    actions=serial_monitor,
    title="Serial Monitor (ambsdk)",
    description="Open serial monitor via `ameba.py monitor` "
                "(supports board_upload.remote_server)",
)

env.AddCustomTarget(
    name="ambsdk-clean",
    dependencies=None,
    actions=clean_all,
    title="Clean All (ambsdk + .pio/)",
    description="Delete both ameba-rtos build_<SOC>/ and PIO's .pio/ cache",
)

# `pio run` default
Default(target_firmware)

env.Replace(
    PROGNAME="firmware",
    PROGSUFFIX=".bin",
)
