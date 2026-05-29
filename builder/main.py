# Copyright 2026 raul_chen
# SPDX-License-Identifier: Apache-2.0

"""platform-realtek-ameba main builder entry.

Wires PlatformIO's ``pio run`` / ``pio run -t upload`` / ``pio run -t clean``
/ ``pio run -t menuconfig`` / ``pio device monitor`` targets to the upstream
``ameba.py`` CLI.

v0.3 architectural shift (EXTERN_DIR mode):
  ``ameba.py build`` is now invoked with ``cwd=$PROJECT_DIR`` instead of
  ``cwd=$SDK_DIR``. The SDK auto-detects this as an "external project" and
  passes ``-DEXTERN_DIR=<PROJECT_DIR>`` to its cmake invocation. This:

  * Makes user code in ``app_example/`` (the SDK's required user code dir)
    AND optional ``src/`` actually compile -- previously only SDK examples
    compiled.
  * Routes GCC compile errors with absolute paths under PROJECT_DIR so
    IDEs (VSCode/CLion) can jump to the correct line.
  * Puts ``build_<SOC>/`` under PROJECT_DIR, never touching the SDK tree
    (preserves the "SDK 0 modifications" hard contract).
  * Allows ``compile_commands.json`` for IntelliSense to live next to the
    user's code.

The PIO project layout for v0.3 looks like:

    my-pio-project/
    ├── platformio.ini
    ├── CMakeLists.txt          # 1 line: ameba_add_subdirectory(app_example)
    ├── prj.conf
    ├── Kconfig
    ├── src/                    # optional, PIO-standard user code
    │   └── main.c
    └── app_example/            # required by SDK; provides app_example()
        ├── CMakeLists.txt
        └── app_main.c

We deliberately do NOT redefine the cmake build graph -- the SDK already
owns it and changes between SoC generations.

v0.2 features carried forward:
  * compile_commands.json export (VSCode IntelliSense)
  * pio device monitor -> ameba.py monitor
  * pio run -t menuconfig -> ameba.py menuconfig
  * pio run -t clean cleans .pio/ + build_<SOC>/ as well
  * Per-env TARGET_SOC isolation so 'pio run -e a -e b' is safe.
"""

import os
import shutil
import sys
from os.path import isdir, isfile, join

from SCons.Script import (
    AlwaysBuild,
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

    v0.3.1 strategy: SDK is now distributed as a PIO package via git URL
    (see platform.json `packages.framework-ameba-rtos`). PIO clones it
    automatically into ~/.platformio/packages/framework-ameba-rtos/ on
    first `pio run`.

    Lookup priority:
      1. ``$AMEBA_SDK_DIR`` env var (developer override, e.g. local fork)
      2. PIO-managed package path via PioPlatform().get_package_dir()
      3. Well-known dev locations (legacy / convenience for in-tree work)

    Falls back with a clear error message if none found.
    """
    candidates = [os.environ.get("AMEBA_SDK_DIR", "")]

    # PIO-managed package path (the "clean install" default route)
    try:
        from platformio.public import PioPlatform
        pkg_dir = PioPlatform().get_package_dir("framework-ameba-rtos")
        if pkg_dir:
            candidates.append(pkg_dir)
    except Exception:
        # PIO not available in this context (e.g. unit tests); fall through
        pass

    # Legacy / dev convenience paths
    candidates += [
        os.path.expanduser("~/projects/ameba-platformio-research/repos/ameba-rtos"),
        os.path.expanduser("~/projects/ameba-rtos"),
        os.path.expanduser("~/.platformio/packages/framework-ameba-rtos"),
    ]

    for candidate in candidates:
        if candidate and isdir(candidate) and isfile(join(candidate, "ameba.py")):
            return candidate

    raise FileNotFoundError(
        "ameba-rtos SDK not found. PIO normally fetches it automatically "
        "from https://github.com/Ameba-AIoT/ameba-rtos.git on first `pio run` "
        "(see platform.json packages). If that failed, either:\n"
        "  - Run `pio pkg install -p framework-ameba-rtos` manually, or\n"
        "  - Set AMEBA_SDK_DIR to a local checkout."
    )


def _find_prebuilts_dir():
    """Locate the ameba prebuilts (cmake/ninja/ccache)."""
    candidates = [
        os.environ.get("AMEBA_PREBUILTS_DIR", ""),
        os.path.expanduser("~/rtk-toolchain/prebuilts-linux-1.0.3"),
        os.path.expanduser("~/.platformio/packages/tool-ameba-prebuilts"),
    ]
    for candidate in candidates:
        if candidate and isdir(candidate) and isfile(join(candidate, "setenv.sh")):
            return candidate
    return None  # SDK will still work; just relies on system cmake/ninja


SDK_DIR = _find_sdk_dir()
PREBUILTS_DIR = _find_prebuilts_dir()
SOC = board.get("build.soc", "RTL8721F").upper()
PROJECT_DIR = env.subst("$PROJECT_DIR")
PROJECT_BUILD_DIR = env.subst("$BUILD_DIR")
ENV_NAME = env.subst("$PIOENV") or "default"

# v0.3: EXTERN_DIR mode means build_<SOC>/ lives under PROJECT_DIR, not SDK_DIR.
EXTERN_BUILD_DIR = join(PROJECT_DIR, f"build_{SOC}")


# -----------------------------------------------------------------------------
# v0.3: External project layout validation + auto-bootstrap
# -----------------------------------------------------------------------------
def _ensure_extern_project_layout():
    """Verify the PIO project has the SDK's external-project structure.

    Required minimum (per SDK's ``ameba.py new-project`` template):
      * ``CMakeLists.txt`` at PROJECT_DIR (entry: ``ameba_add_subdirectory(app_example)``)
      * ``app_example/CMakeLists.txt``  (registers user sources)
      * ``app_example/app_main.c``      (provides ``void app_example(void)``)

    Optional but nice:
      * ``Kconfig``, ``prj.conf``      (Kconfig overlay for the project)
      * ``src/*.[c|cpp|h]``            (PIO-standard user code; bridged below)

    If required files are missing, print a clear message pointing at
    ``ameba.py new-project`` and at our example template under examples/.
    """
    required = [
        ("CMakeLists.txt", "Top-level cmake entry. 1 line is enough:\n"
                            "    ameba_add_subdirectory(app_example)"),
        ("app_example/CMakeLists.txt",
         "Per-app sources list. See examples/ameba-blink/app_example/CMakeLists.txt"),
        ("app_example/app_main.c",
         "Must define `void app_example(void)`. SDK calls this from main."),
    ]
    missing = [(p, hint) for (p, hint) in required if not isfile(join(PROJECT_DIR, p))]
    if not missing:
        return

    print("[ameba] ERROR: this PIO project is not laid out as an Ameba external project.")
    print(f"[ameba] PROJECT_DIR={PROJECT_DIR}")
    print("[ameba] missing required files:")
    for p, hint in missing:
        print(f"[ameba]   - {p}")
        for line in hint.splitlines():
            print(f"[ameba]       {line}")
    print("[ameba] Quick fix:")
    print(f"[ameba]   1. cd {PROJECT_DIR} && \\")
    print(f"[ameba]      python {SDK_DIR}/ameba.py new-project . -a app   "
          "(creates the skeleton)")
    print(f"[ameba]   2. or copy examples/ameba-blink/* into {PROJECT_DIR}/")
    env.Exit(1)


def _bridge_src_into_app_example():
    """Make user-written ``src/*.[c|cpp]`` actually get compiled.

    PIO convention: users put code in ``src/``.  Ameba SDK convention:
    code in ``app_example/`` is registered via that dir's CMakeLists.txt.

    Bridge strategy: at build configure time, append every ``src/**/*.c``
    (and ``.cpp``) into ``app_example/CMakeLists.txt`` via a generated
    fragment file ``app_example/_pio_src_fragment.cmake`` that we control.

    The fragment is included by the user's app_example/CMakeLists.txt
    via ``include(_pio_src_fragment.cmake OPTIONAL)`` (added by our
    project template). If the user removes that include, src/ bridging
    is silently disabled -- their choice.

    We never touch the user's CMakeLists.txt directly, so this stays
    additive and reversible.
    """
    src_dir = join(PROJECT_DIR, "src")
    fragment = join(PROJECT_DIR, "app_example", "_pio_src_fragment.cmake")

    if not isdir(src_dir):
        # No src/, nothing to bridge. Remove stale fragment if present.
        if isfile(fragment):
            os.remove(fragment)
        return

    # Collect all user source files under src/ (recursive)
    sources = []
    for root, _dirs, files in os.walk(src_dir):
        for f in files:
            if f.lower().endswith((".c", ".cpp", ".cc", ".cxx", ".s", ".S")):
                full = join(root, f)
                # cmake on Linux/WSL handles forward slashes fine
                sources.append(full.replace(os.sep, "/"))

    if not sources:
        if isfile(fragment):
            os.remove(fragment)
        return

    # Find include dirs: any directory under src/ that contains .h files,
    # plus src/ itself.
    include_dirs = {src_dir.replace(os.sep, "/")}
    for root, _dirs, files in os.walk(src_dir):
        if any(f.lower().endswith((".h", ".hpp", ".hh", ".hxx")) for f in files):
            include_dirs.add(root.replace(os.sep, "/"))

    lines = [
        "# Auto-generated by platform-realtek-ameba. Do not edit.",
        "# Bridges PIO's src/ directory into the Ameba app_example library.",
        "# Regenerated on every `pio run`.",
        "",
        "ameba_list_append(private_sources",
    ]
    for s in sorted(sources):
        lines.append(f"    {s}")
    lines.append(")")
    lines.append("")

    if include_dirs:
        # NOTE: do not call target_include_directories() here -- the
        # CURRENT_LIB_NAME variable is not yet defined when this fragment
        # is include()-d (it is set later by ameba_add_internal_library).
        # Instead we emit a CMake list variable that the user's
        # CMakeLists.txt picks up after the library is created.
        lines.append("# Include dirs collected from src/ — applied below by")
        lines.append("# the user's app_example/CMakeLists.txt after the library exists.")
        lines.append("set(_pio_src_include_dirs")
        for d in sorted(include_dirs):
            lines.append(f"    {d}")
        lines.append(")")
        lines.append("")

    os.makedirs(os.path.dirname(fragment), exist_ok=True)
    new_content = "\n".join(lines)
    # Only rewrite if changed -- avoids needless cmake re-configure
    if isfile(fragment):
        try:
            with open(fragment, "r") as fh:
                if fh.read() == new_content:
                    return
        except OSError:
            pass
    with open(fragment, "w") as fh:
        fh.write(new_content)
    print(f"[ameba] bridged {len(sources)} source file(s) from src/ -> "
          f"app_example/_pio_src_fragment.cmake")


# -----------------------------------------------------------------------------
# Environment for `ameba.py *` subprocesses
# -----------------------------------------------------------------------------
def _make_sdk_env():
    """Build os.environ for subprocess calls into ameba.py.

    Sets:
      * RTK_TOOLCHAIN_DIR -> PIO platform cache (so toolchain auto-fetch
        survives across projects)
      * TARGET_SOC -> bypasses soc_info.json; per-env safe
      * VIRTUAL_ENV + PATH -> SDK venv first (json5/elftools), then
        prebuilts cmake/ninja, then system PATH
      * EXTRA_CFLAGS / EXTRA_CXXFLAGS -> PIO build_flags propagated to
        the SDK cmake invocation (v0.3 #5)
    """
    sdk_env = os.environ.copy()

    sdk_env["RTK_TOOLCHAIN_DIR"] = join(
        platform.get_dir(), ".cache", "rtk-toolchain"
    )
    os.makedirs(sdk_env["RTK_TOOLCHAIN_DIR"], exist_ok=True)

    # TARGET_SOC env var takes precedence over soc_info.json inside
    # ameba_soc_utils.SocManager.parse_soc_info(). Multi-env safe.
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

    # v0.3 #5: pass build_flags through to the SDK cmake.
    # PIO's BUILD_FLAGS / CPPDEFINES come from platformio.ini's build_flags.
    # We forward them as EXTRA_CFLAGS so the SDK toolchain sees them.
    extra_cflags = []
    raw_flags = env.subst("$BUILD_FLAGS").strip()
    if raw_flags:
        extra_cflags.append(raw_flags)
    if extra_cflags:
        existing = sdk_env.get("EXTRA_CFLAGS", "").strip()
        merged = (" ".join(extra_cflags) + (" " + existing if existing else "")).strip()
        sdk_env["EXTRA_CFLAGS"] = merged
        sdk_env["EXTRA_CXXFLAGS"] = merged

    return sdk_env


def _ameba_python():
    """Path to the python interpreter ameba.py expects (SDK venv's)."""
    venv_py = join(SDK_DIR, ".venv", "bin", "python3")
    if isfile(venv_py):
        return venv_py
    return "python3"


def _ameba_py_args(action, soc=SOC, clean=False, upload_opts=None,
                   menuconfig_opts=None, monitor_opts=None):
    """Translate PIO target -> ameba.py argv.

    Returns a list of subprocess argv lists to run in order.
    """
    py = _ameba_python()
    base = [py, join(SDK_DIR, "ameba.py")]

    if action == "build":
        # `ameba.py build <SOC>` accepts SOC as positional. With cwd=PROJECT_DIR
        # the SDK auto-injects -DEXTERN_DIR=PROJECT_DIR. No `soc` step needed
        # because TARGET_SOC env + positional arg both pin the choice.
        cmd = base + ["build", soc] + (["-c"] if clean else [])
        return [cmd]
    elif action == "flash":
        # flash needs to know the build artefacts location, which in
        # EXTERN_DIR mode is ${PROJECT_DIR}/build_<SOC>/. ameba.py flash
        # auto-discovers this when run from PROJECT_DIR.
        flash_args = base + ["flash"]
        if upload_opts:
            for k, v in upload_opts.items():
                if v is None or v is False:
                    continue
                flash_args.append(f"--{k}" if k.startswith("-")
                                  else f"--{k.replace('_', '-')}")
                if v is not True:
                    flash_args.append(str(v))
        return [flash_args]
    elif action == "clean":
        return [base + ["clean", soc]]
    elif action == "menuconfig":
        mc_args = base + ["menuconfig", soc]
        if menuconfig_opts:
            mc_args.extend(menuconfig_opts)
        return [mc_args]
    elif action == "monitor":
        mon_args = base + ["monitor"]
        if monitor_opts:
            for k, v in monitor_opts.items():
                if v is None or v is False:
                    continue
                mon_args.append(f"--{k.replace('_', '-')}"
                                if not k.startswith("-") else k)
                if v is not True:
                    mon_args.append(str(v))
        return [mon_args]
    else:
        raise ValueError(f"unknown action {action!r}")


# -----------------------------------------------------------------------------
# compile_commands.json export (VSCode IntelliSense)
# -----------------------------------------------------------------------------
def _export_compile_commands():
    """Copy cmake's compile_commands.json into PIO BUILD_DIR + project root.

    v0.3: cmake now writes to ``${PROJECT_DIR}/build_<SOC>/build/compile_commands.json``
    instead of inside SDK_DIR.
    """
    src = join(EXTERN_BUILD_DIR, "build", "compile_commands.json")
    if not isfile(src):
        print(f"[ameba] compile_commands.json not found at {src}; "
              "skipping IntelliSense export")
        return

    targets = [
        join(PROJECT_BUILD_DIR, "compile_commands.json"),  # PIO standard
        join(PROJECT_DIR, "compile_commands.json"),         # editor root
    ]
    for dst in targets:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    print(f"[ameba] exported compile_commands.json "
          f"({os.path.getsize(src)//1024} KB) -> "
          f"{PROJECT_DIR}/compile_commands.json (+ .pio/build/{ENV_NAME}/)")


# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------
def build_firmware(*_args, **_kwargs):
    import subprocess

    _ensure_extern_project_layout()
    _bridge_src_into_app_example()

    sdk_env = _make_sdk_env()
    cmd_chain = _ameba_py_args("build", soc=SOC)

    print(f"[ameba] building SoC={SOC} (env={ENV_NAME})")
    print(f"[ameba] PROJECT_DIR={PROJECT_DIR}  (= EXTERN_DIR)")
    print(f"[ameba] SDK_DIR={SDK_DIR}")
    print(f"[ameba] build outputs -> {EXTERN_BUILD_DIR}/")
    if sdk_env.get("EXTRA_CFLAGS"):
        print(f"[ameba] EXTRA_CFLAGS={sdk_env['EXTRA_CFLAGS']!r}")

    for cmd in cmd_chain:
        print(f"[ameba] $ (cwd={PROJECT_DIR}) {' '.join(cmd)}")
        # v0.3 KEY CHANGE: cwd=PROJECT_DIR, not SDK_DIR.
        # SDK detects "external project" mode and auto-passes -DEXTERN_DIR.
        rc = subprocess.call(cmd, cwd=PROJECT_DIR, env=sdk_env)
        if rc != 0:
            print(f"[ameba] command failed (rc={rc})")
            env.Exit(rc)

    # Copy firmware into PIO BUILD_DIR (v0.3: from PROJECT_DIR/build_<SOC>/)
    src_app = join(EXTERN_BUILD_DIR, "app.bin")
    dst_app = join(PROJECT_BUILD_DIR, "firmware.bin")
    if isfile(src_app):
        os.makedirs(PROJECT_BUILD_DIR, exist_ok=True)
        shutil.copyfile(src_app, dst_app)
        print(f"[ameba] copied {src_app} -> {dst_app}")
    else:
        print(f"[ameba] WARNING: app.bin not found at {src_app}")

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

    print(f"[ameba] uploading SoC={SOC}, opts={upload_opts}")
    for cmd in _ameba_py_args("flash", soc=SOC, upload_opts=upload_opts):
        print(f"[ameba] $ (cwd={PROJECT_DIR}) {' '.join(cmd)}")
        # v0.3: flash also runs from PROJECT_DIR (where build_<SOC>/ lives)
        rc = subprocess.call(cmd, cwd=PROJECT_DIR, env=sdk_env)
        if rc != 0:
            env.Exit(rc)


def serial_monitor(*_args, **_kwargs):
    import subprocess

    sdk_env = _make_sdk_env()
    monitor_opts = {}

    port = (env.subst("$MONITOR_PORT") or env.subst("$UPLOAD_PORT")
            or board.get("upload.port", ""))
    if port:
        monitor_opts["port"] = port

    # Ameba LogUART defaults to 1500000 baud.
    speed = env.subst("$MONITOR_SPEED") or "1500000"
    if speed:
        monitor_opts["baudrate"] = speed

    remote_server = (
        env.GetProjectOption("board_upload.remote_server", None)
        or env.GetProjectOption("custom_monitor_remote_server", None)
        or board.get("upload.remote_server", None)
    )
    if remote_server:
        monitor_opts["remote-server"] = remote_server

    remote_password = (
        env.GetProjectOption("board_upload.remote_password", None)
        or env.GetProjectOption("custom_monitor_remote_password", None)
        or board.get("upload.remote_password", None)
    )
    if remote_password:
        monitor_opts["remote-password"] = remote_password

    if env.GetProjectOption("custom_monitor_reset", "no").lower() in (
        "yes", "true", "1"
    ):
        monitor_opts["-reset"] = True

    if not sys.stdin.isatty() or env.GetProjectOption(
        "custom_monitor_no_console", "no"
    ).lower() in ("yes", "true", "1"):
        monitor_opts["no-console"] = True

    print(f"[ameba] monitor SoC={SOC}, opts={monitor_opts}")
    print("[ameba] (press Ctrl+C to exit; if board is silent, the firmware "
          "is probably idle -- set 'custom_monitor_reset = yes' in [env] to "
          "force a soft reset and capture boot log)")
    for cmd in _ameba_py_args("monitor", soc=SOC, monitor_opts=monitor_opts):
        print(f"[ameba] $ (cwd={PROJECT_DIR}) {' '.join(cmd)}")
        rc = subprocess.call(cmd, cwd=PROJECT_DIR, env=sdk_env)
        if rc != 0:
            env.Exit(rc)


def run_menuconfig(*_args, **_kwargs):
    import subprocess

    _ensure_extern_project_layout()  # menuconfig also needs proper layout

    sdk_env = _make_sdk_env()
    print(f"[ameba] menuconfig SoC={SOC}")
    print("[ameba] (this is interactive; will hand off your terminal to "
          "ameba.py menuconfig's curses UI)")

    for cmd in _ameba_py_args("menuconfig", soc=SOC):
        print(f"[ameba] $ (cwd={PROJECT_DIR}) {' '.join(cmd)}")
        rc = subprocess.call(cmd, cwd=PROJECT_DIR, env=sdk_env)
        if rc != 0:
            env.Exit(rc)


def clean_all(*_args, **_kwargs):
    """Clean everything: build_<SOC>/ in PROJECT_DIR, .pio/, and exported files."""
    sdk_env = _make_sdk_env()
    print(f"[ameba] cleaning SoC={SOC} ({EXTERN_BUILD_DIR} + PIO BUILD_DIR)")

    # 1. Remove the EXTERN_DIR build tree directly. Faster + more reliable
    #    than `ameba.py clean` which only does cmake-level cleanup.
    if isdir(EXTERN_BUILD_DIR):
        print(f"[ameba] rm -rf {EXTERN_BUILD_DIR}")
        shutil.rmtree(EXTERN_BUILD_DIR, ignore_errors=True)

    # 2. clean PIO side
    if isdir(PROJECT_BUILD_DIR):
        print(f"[ameba] rm -rf {PROJECT_BUILD_DIR}")
        shutil.rmtree(PROJECT_BUILD_DIR, ignore_errors=True)

    # 3. clean exported compile_commands.json from project root
    cc = join(PROJECT_DIR, "compile_commands.json")
    if isfile(cc):
        print(f"[ameba] rm {cc}")
        os.remove(cc)

    # 4. clean the auto-generated _pio_src_fragment.cmake
    fragment = join(PROJECT_DIR, "app_example", "_pio_src_fragment.cmake")
    if isfile(fragment):
        print(f"[ameba] rm {fragment}")
        os.remove(fragment)


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

env.AddCustomTarget(
    name="menuconfig",
    dependencies=None,
    actions=run_menuconfig,
    title="Menuconfig",
    description="Run interactive Kconfig menuconfig (delegates to "
                "`ameba.py menuconfig <SOC>`)",
)

env.AddCustomTarget(
    name="monitor_ameba",
    dependencies=None,
    actions=serial_monitor,
    title="Serial Monitor (ameba-rtos)",
    description="Open serial monitor via `ameba.py monitor` "
                "(supports board_upload.remote_server)",
)

env.AddCustomTarget(
    name="ameba-clean",
    dependencies=None,
    actions=clean_all,
    title="Clean All (ameba-rtos + .pio/)",
    description=f"Delete both {{PROJECT_DIR}}/build_<SOC>/ "
                "and PIO's .pio/ cache",
)

# `pio run` default
Default(target_firmware)

env.Replace(
    PROGNAME="firmware",
    PROGSUFFIX=".bin",
)
