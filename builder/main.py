# Copyright 2026 raul_chen
# SPDX-License-Identifier: Apache-2.0

"""platform-realtek-ameba main builder entry.

Wires PlatformIO's standard targets (`pio run`, `upload`, `clean`, `menuconfig`)
directly to the upstream `ameba.py` CLI. Serial monitor (`pio device monitor`)
uses PIO's built-in miniterm — no platform glue needed.

The build is invoked with `cwd=$PROJECT_DIR` (EXTERN_DIR mode).
This keeps `build_<SOC>/` inside the user's project, preserves absolute paths 
for GCC errors, and safely handles parallel multi-env builds.
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

    Strategy: SDK is distributed as a PIO package via git URL
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
# Fetch the 'build.soc' value from the currently active board JSON (e.g., RTL8721Dx).
# PlatformIO evaluates this script once per environment ([env:xxx]).
_soc = board.get("build.soc")
if not _soc:
    sys.stderr.write(f"Error: missing 'build.soc' in {board.id}.json\n")
    env.Exit(1)
SOC = _soc.upper()
PROJECT_DIR = env.subst("$PROJECT_DIR")
PROJECT_BUILD_DIR = env.subst("$BUILD_DIR")
ENV_NAME = env.subst("$PIOENV") or "default"

# EXTERN_DIR mode: build artifacts live under the project, not the SDK.
EXTERN_BUILD_DIR = join(PROJECT_DIR, f"build_{SOC}")


# -----------------------------------------------------------------------------
# External project layout validation
# -----------------------------------------------------------------------------
def _ensure_extern_project_layout():
    """Make PROJECT_DIR a valid Ameba external project, creating skeleton
    files when they're missing. Mirrors espressif32's create_default_project_files().

    Required by the SDK (when invoked with -DEXTERN_DIR=PROJECT_DIR):
      * ``CMakeLists.txt`` at PROJECT_DIR (entry: ``ameba_add_subdirectory(app_example)``)
      * ``app_example/CMakeLists.txt``  (registers user sources into SDK)
      * ``app_example/app_main.c``      (provides ``void app_example(void)``)

    These are auto-created on first build so that the standard PIO flow
    works out of the box:

        pio project init --board pke8721daf-c13-f10 \\
            --project-option "platform=https://...platform-realtek-ameba.git" \\
            --project-option "framework=ameba-rtos"
        pio run    # ← skeleton appears here, then build proceeds

    Users who want full control can edit these files (or replace them
    with the more elaborate examples/ameba-blink/ template). Subsequent
    builds detect they exist and leave them alone.
    """
    # Top-level CMakeLists.txt — minimal entry that pulls in app_example/.
    root_cmake = join(PROJECT_DIR, "CMakeLists.txt")
    if not isfile(root_cmake):
        with open(root_cmake, "w") as fp:
            fp.write(
                "# Auto-generated by platform-realtek-ameba on first build.\n"
                "# Top-level cmake entry for Ameba external-project mode.\n"
                "# Edit freely — subsequent builds will leave this alone.\n"
                "\n"
                "ameba_add_subdirectory(app_example)\n"
            )
        print(f"[ameba] created {root_cmake}")

    # app_example/CMakeLists.txt — registers app sources + bridges PIO src/.
    app_dir = join(PROJECT_DIR, "app_example")
    if not isdir(app_dir):
        os.makedirs(app_dir)
    app_cmake = join(app_dir, "CMakeLists.txt")
    if not isfile(app_cmake):
        with open(app_cmake, "w") as fp:
            fp.write(
                "# Auto-generated by platform-realtek-ameba on first build.\n"
                "# Registers user sources into the SDK as the `app_example` library.\n"
                "#\n"
                "# Two ways to add user code:\n"
                "#   (A) PIO style — drop .c/.h into ../src/. Auto-bridged via the\n"
                "#       OPTIONAL include() below (file list maintained for you).\n"
                "#   (B) SDK style — list explicitly with ameba_list_append. Use\n"
                "#       this for per-file flags or conditional sources.\n"
                "# Both can coexist.\n"
                "\n"
                "set(private_sources)\n"
                "set(_pio_src_include_dirs)\n"
                "\n"
                "ameba_list_append(private_sources app_main.c)\n"
                "\n"
                "include(\"${CMAKE_CURRENT_SOURCE_DIR}/_pio_src_fragment.cmake\""
                " OPTIONAL)\n"
                "\n"
                "ameba_add_internal_library(app_example\n"
                "    p_SOURCES\n"
                "        ${private_sources}\n"
                ")\n"
                "\n"
                "if(_pio_src_include_dirs)\n"
                "    target_include_directories(${c_CURRENT_TARGET_NAME}"
                " PRIVATE ${_pio_src_include_dirs})\n"
                "endif()\n"
            )
        print(f"[ameba] created {app_cmake}")

    # app_example/app_main.c — SDK entry point. Delegates to user_main()
    # in src/main.c when present, so users only ever touch src/.
    app_main = join(app_dir, "app_main.c")
    if not isfile(app_main):
        with open(app_main, "w") as fp:
            fp.write(
                "/*\n"
                " * Auto-generated by platform-realtek-ameba on first build.\n"
                " *\n"
                " * Required by the Ameba SDK: defines `void app_example(void)`,\n"
                " * which the SDK's main() calls during system bring-up. Treat\n"
                " * this as the equivalent of `app_main()` in ESP-IDF.\n"
                " *\n"
                " * By default we delegate to `user_main()` in src/main.c so\n"
                " * editor focus stays on the PIO-standard src/ directory.\n"
                " * Define your own `void user_main(void)` there.\n"
                " */\n"
                "\n"
                "#include <stdio.h>\n"
                "\n"
                "__attribute__((weak)) void user_main(void)\n"
                "{\n"
                "    /* Default no-op; override by defining user_main() in src/. */\n"
                "}\n"
                "\n"
                "void app_example(void)\n"
                "{\n"
                "    user_main();\n"
                "}\n"
            )
        print(f"[ameba] created {app_main}")

    # src/ — PIO-standard user code dir. If empty (or missing), drop a
    # starter main.c so first-run prints something visible.
    src_dir = join(PROJECT_DIR, "src")
    if not isdir(src_dir):
        os.makedirs(src_dir)
    if not os.listdir(src_dir):
        starter = join(src_dir, "main.c")
        with open(starter, "w") as fp:
            fp.write(
                "/*\n"
                " * src/main.c — PIO-standard user code entry.\n"
                " *\n"
                " * Define `void user_main(void)`; the auto-generated\n"
                " * app_example/app_main.c calls it during SDK startup.\n"
                " * Replace with your own logic.\n"
                " */\n"
                "\n"
                "#include <stdio.h>\n"
                "\n"
                "void user_main(void)\n"
                "{\n"
                "    printf(\"[ameba] hello from src/main.c\\n\");\n"
                "}\n"
            )
        print(f"[ameba] created {starter}")


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

    # Pass build_flags through to the SDK cmake.
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
                   menuconfig_opts=None):
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
    else:
        raise ValueError(f"unknown action {action!r}")

# -----------------------------------------------------------------------------
# compile_commands.json export (VSCode IntelliSense)
# -----------------------------------------------------------------------------
def _export_compile_commands():
    """Copy cmake's compile_commands.json into PIO BUILD_DIR + project root.


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
        # Execute in PROJECT_DIR to trigger external project mode.
        # SDK detects "external project" mode and auto-passes -DEXTERN_DIR.
        rc = subprocess.call(cmd, cwd=PROJECT_DIR, env=sdk_env)
        if rc != 0:
            print(f"[ameba] command failed (rc={rc})")
            env.Exit(rc)

    # Copy firmware into PIO BUILD_DIR
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
        # Flash runs from PROJECT_DIR
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
