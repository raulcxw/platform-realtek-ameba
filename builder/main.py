# Copyright 2026 raul_chen
# SPDX-License-Identifier: Apache-2.0

"""platform-amebartos main builder entry.

Wires PlatformIO's ``pio run`` / ``pio run -t upload`` / ``pio run -t clean``
targets to the upstream ``ameba.py`` CLI. We deliberately do NOT redefine
the CMake build graph -- the SDK already owns it and changes between SoC
generations.
"""

import os
import sys
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


# -----------------------------------------------------------------------------
# Environment for `ameba.py build`
# -----------------------------------------------------------------------------
def _make_sdk_env():
    """Return os.environ with prebuilts (cmake/ninja/ccache) on PATH and
    RTK_TOOLCHAIN_DIR pointed at PIO's package cache."""
    sdk_env = os.environ.copy()

    # SDK auto-downloads asdk-12.3.1 / asdk-10.3.1 here if absent
    sdk_env["RTK_TOOLCHAIN_DIR"] = join(
        platform.get_dir(), ".cache", "rtk-toolchain"
    )
    os.makedirs(sdk_env["RTK_TOOLCHAIN_DIR"], exist_ok=True)

    # PATH order matters: SDK venv's python must come first so ameba.py's
    # CMake `find_package(Python3)` and its `axf2bin.py` subprocess pick up
    # json5/elftools/etc. installed in .venv. cmake+ninja from prebuilts go
    # next; system PATH last.
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
    # fallback: rely on PATH (configured by _make_sdk_env)
    return "python3"


def _ameba_py_args(action, soc=SOC, app=None, clean=False, upload_opts=None):
    """Translate PIO target -> ameba.py argv."""
    py = _ameba_python()
    args = [py, join(SDK_DIR, "ameba.py")]

    if action == "build":
        # `ameba.py soc <SOC>` writes ./soc_info.json. We do this once per run.
        # `ameba.py build` then uses the active SoC.
        return [
            args + ["soc", soc],
            args + ["build"] + (["-c"] if clean else []),
        ]
    elif action == "flash":
        # SDK requires the SoC to be active before flash; soc_info.json is
        # repo-local state that may have been clobbered by another build.
        flash_args = args + ["flash"]
        if upload_opts:
            for k, v in upload_opts.items():
                if v is None or v is False:
                    continue
                flash_args.append(f"--{k}" if k.startswith("-") else f"--{k.replace('_', '-')}")
                if v is not True:
                    flash_args.append(str(v))
        return [
            args + ["soc", soc],
            flash_args,
        ]
    elif action == "clean":
        return [args + ["clean", soc]]
    else:
        raise ValueError(f"unknown action {action!r}")


# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------
def build_firmware(*_args, **_kwargs):
    import subprocess

    sdk_env = _make_sdk_env()
    cmd_chain = _ameba_py_args("build", soc=SOC)

    print(f"[ambsdk] building SoC={SOC}, SDK={SDK_DIR}")
    print(f"[ambsdk] RTK_TOOLCHAIN_DIR={sdk_env['RTK_TOOLCHAIN_DIR']}")

    for cmd in cmd_chain:
        print(f"[ambsdk] $ {' '.join(cmd)}")
        rc = subprocess.call(cmd, cwd=SDK_DIR, env=sdk_env)
        if rc != 0:
            print(f"[ambsdk] command failed (rc={rc})")
            env.Exit(rc)

    # Copy the produced firmware into PIO's BUILD_DIR so `firmware.bin`
    # exists where PIO expects it.
    import shutil

    src_app = join(SDK_DIR, f"build_{SOC}", "app.bin")
    dst_app = join(PROJECT_BUILD_DIR, "firmware.bin")
    if os.path.isfile(src_app):
        os.makedirs(PROJECT_BUILD_DIR, exist_ok=True)
        shutil.copyfile(src_app, dst_app)
        print(f"[ambsdk] copied {src_app} -> {dst_app}")


def upload_firmware(*_args, **_kwargs):
    import subprocess

    sdk_env = _make_sdk_env()

    # Translate PIO upload_* options into ameba.py flash args.
    # Supported PIO options:
    #   upload_port             -> -p / --port (e.g. COM40, /dev/ttyUSB0)
    #   upload_speed            -> -b / --baudrate
    #   upload_protocol         -> selects path (default: ameba bootrom)
    # Realtek-specific (under [env] as upload_flags or board_upload.*):
    #   board_upload.remote_server     -> --remote-server
    #   board_upload.remote_password   -> --remote-password
    #   board_upload.memory_type       -> --memory-type {nor,nand,ram}
    #   board_upload.chip_erase = yes  -> --chip-erase
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

# `pio run` default
Default(target_firmware)

# Tell PIO where the firmware lives so size/upload tooling work.
env.Replace(
    PROGNAME="firmware",
    PROGSUFFIX=".bin",
)
