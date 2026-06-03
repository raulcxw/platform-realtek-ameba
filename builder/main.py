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

import json
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
      3. Hardcoded ~/.platformio/packages/framework-ameba-rtos as
         a last-resort fallback (covers the case where PioPlatform is
         not available, e.g. tools running outside PIO context)

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

    # Hardcoded standard PIO package location (last resort).
    candidates.append(
        os.path.expanduser("~/.platformio/packages/framework-ameba-rtos")
    )

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
# Pass through verbatim — ameba.py is case-sensitive (e.g. RTL8721Dx, not
# RTL8721DX). Whatever the board manifest declares is the canonical name.
SOC = _soc
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
                " *\n"
                " * IMPORTANT: user_main() runs ON THE SDK'S MAIN TASK and\n"
                " * MUST RETURN promptly. The SDK still has work to do after\n"
                " * us — atcmd service, lwIP, Wi-Fi driver init — none of\n"
                " * which can run if user_main() blocks.\n"
                " *\n"
                " * For any persistent work (loops, blinking, network I/O):\n"
                " *   1. xTaskCreate() a worker function\n"
                " *   2. let user_main() return\n"
                " *   3. SDK takes over and your task runs alongside it\n"
                " *\n"
                " * Doing `while (1) { ... }` directly inside user_main()\n"
                " * will hang the boot — atcmd won't come up, monitor stays\n"
                " * silent, and you'll waste an hour debugging.\n"
                " */\n"
                "\n"
                "#include \"ameba_soc.h\"\n"
                "#include \"FreeRTOS.h\"\n"
                "#include \"task.h\"\n"
                "\n"
                "static void blink_task(void *param)\n"
                "{\n"
                "    (void)param;\n"
                "    int tick = 0;\n"
                "    while (1) {\n"
                "        DiagPrintf(\"[ameba] tick=%d\\n\", tick++);\n"
                "        vTaskDelay(pdMS_TO_TICKS(2000));\n"
                "    }\n"
                "}\n"
                "\n"
                "void user_main(void)\n"
                "{\n"
                "    DiagPrintf(\"[ameba] hello from src/main.c\\n\");\n"
                "\n"
                "    /* Spin up persistent work on its own task and RETURN\n"
                "     * so the SDK can finish bring-up. */\n"
                "    xTaskCreate(blink_task, \"blink\", 256, NULL,\n"
                "                tskIDLE_PRIORITY + 1, NULL);\n"
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
      * RTK_TOOLCHAIN_DIR -> ~/rtk-toolchain by default. This matches the
        SDK's own default, so `pio run` and standalone `ameba.py build`
        share a single ~5GB toolchain cache instead of duplicating it.
        Override with $RTK_TOOLCHAIN_DIR if you need to relocate (e.g.
        disk-full migration to another drive).
      * TARGET_SOC -> bypasses soc_info.json; per-env safe
      * VIRTUAL_ENV + PATH -> SDK venv first (json5/elftools), then
        prebuilts cmake/ninja, then system PATH
      * EXTRA_CFLAGS / EXTRA_CXXFLAGS -> PIO build_flags propagated to
        the SDK cmake invocation (v0.3 #5)
    """
    sdk_env = os.environ.copy()

    # Default to the SDK's own convention (~/rtk-toolchain) so users who
    # run `ameba.py build` directly AND use PIO get one shared toolchain
    # cache instead of duplicate ~1GB downloads.
    if "RTK_TOOLCHAIN_DIR" not in sdk_env:
        sdk_env["RTK_TOOLCHAIN_DIR"] = os.path.expanduser("~/rtk-toolchain")
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
                # Special case: 'image' is a list of [name, start, end]
                # triples that must each become a separate `-i N S E` group.
                # ameba.py's `-i` argparse uses nargs=3 + action='append'.
                if k == "image" and isinstance(v, list):
                    for triple in v:
                        flash_args.append("-i")
                        flash_args.extend(str(x) for x in triple)
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


def _run_ameba_flash(cmd, sdk_env, label):
    """Run an `ameba.py flash` subprocess with proper failure detection.

    The SDK's flash.py main() returns exit code 0 even when its internal
    flash thread reports `Finished FAIL: ErrType.XXX` via stdout (the
    failure never propagates to sys.exit). To catch this we capture
    stdout and grep for the failure markers. False negatives here = silent
    failures the user only finds out when their next monitor session shows
    nothing was actually flashed.

    Returns nothing on success; calls env.Exit() on failure.
    """
    import subprocess

    print(f"[ameba] $ (cwd={PROJECT_DIR}) {' '.join(cmd)}")
    result = subprocess.run(
        cmd, cwd=PROJECT_DIR, env=sdk_env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )
    sys.stdout.write(result.stdout)
    sys.stdout.flush()

    if result.returncode != 0:
        env.Exit(result.returncode)
        return

    if "Finished FAIL" in result.stdout or "Finished PASS" not in result.stdout:
        print(
            f"\n[ameba] ERROR: {label} did NOT complete.\n"
            "  Common causes:\n"
            "    1. Board not in download mode. Try this sequence:\n"
            "         a) start the pio command\n"
            "         b) when you see 'Check supported flash size...',\n"
            "            press the board's RESET button\n"
            "       Auto-reset via DTR/RTS only works on USB-UART chips\n"
            "       that wire those pins to the board's reset (CP2102, CH340).\n"
            "       PL2303 typically does NOT, so manual reset is needed.\n"
            "    2. Serial port held by another process — make sure no\n"
            "       `pio device monitor` is running on this port.\n"
            "    3. Image larger than its target flash region — check the\n"
            "       'too large for ...' line above and either shrink the\n"
            "       image or grow the partition via menuconfig."
        )
        env.Exit(1)
        return


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
# pio check support: feed CPPPATH/CPPDEFINES from compile_commands.json into
# PIO's build env so cppcheck/clang-tidy can resolve SDK headers and macros.
# -----------------------------------------------------------------------------
#
# Why this is needed: PIO core's `pio check` reads CPPPATH/CPPDEFINES/CCFLAGS
# from the build environment via `load_build_metadata()` (see
# platformio/check/tools/base.py:_load_cpp_data). Black-box vendor-CLI
# platforms like ours run `subprocess.call("ameba.py build")` and never feed
# any -I/-D flags into PIO's SCons env. Result: `pio check` sees
# `includes={"build": [], "compatlib": [], "toolchain": []}` and reports
# every SDK function as `unknownTypeName` / `cannotFindIncludeFile`.
#
# Fix: parse the compile_commands.json that the SDK already emits during
# `pio run`, extract every -I path and -D macro, dedupe, and AppendUnique
# them to env. cppcheck/clang-tidy then resolve SDK headers correctly.
#
# espressif32 does this implicitly via its espidf.py framework script
# (`env.AppendUnique(CPPPATH=app_includes["plain_includes"])`) — same pattern,
# different data source (CMake File API vs compile_commands.json).
def _inject_check_metadata():
    """Parse compile_commands.json and inject CPPPATH/CPPDEFINES into env.

    Called from build_firmware() after _export_compile_commands(). On `pio
    check` the user must `pio run` at least once first; otherwise this is
    a no-op with a helpful warning.

    We sample a single representative entry rather than walking all 600+
    entries: every TU compiled by the SDK uses the same global -I and -D
    set (it's a CMake `target_include_directories` PUBLIC fan-out), so
    sampling one is correct and ~50x faster than aggregating all.
    """
    # Look in both standard locations: .pio/build/<env>/ (PIO standard) and
    # PROJECT_DIR/ (where we ALSO copy it for VSCode auto-discovery — see
    # _export_compile_commands). On a fresh `pio check` after the user has
    # done `pio run -t clean`, the .pio path is gone but PROJECT_DIR's copy
    # is sticky — fall back to it.
    cc_path = join(PROJECT_BUILD_DIR, "compile_commands.json")
    if not isfile(cc_path):
        cc_path = join(PROJECT_DIR, "compile_commands.json")
    if not isfile(cc_path):
        # First-ever pio check — build hasn't run.
        return

    try:
        with open(cc_path, "r", encoding="utf-8") as fh:
            entries = json.load(fh)
    except (OSError, ValueError) as ex:
        print(f"[ameba] pio check: failed to read {cc_path} ({ex}); "
              "cppcheck will lack SDK includes")
        return

    if not entries:
        return

    # Find the longest-command entry — cmake-emitted compile_commands has
    # bootstrap entries (e.g. preprocessor checks) with abbreviated flags.
    # The TU entries are the longest; sampling them gets the full -I/-D set.
    sample = max(entries, key=lambda e: len(e.get("command",
                                                   " ".join(e.get("arguments", [])))))
    cmdline = sample.get("command") or " ".join(sample.get("arguments", []))

    # Tokenize. shlex handles quoted paths correctly (rare on linux but
    # safer than split-by-space).
    import shlex
    try:
        tokens = shlex.split(cmdline)
    except ValueError:
        tokens = cmdline.split()

    includes, defines = [], []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        # -I<path>  OR  -I <path>
        if tok == "-I" and i + 1 < len(tokens):
            includes.append(tokens[i + 1])
            i += 2
            continue
        if tok.startswith("-I"):
            includes.append(tok[2:])
            i += 1
            continue
        # -isystem <path>  (treat as include too)
        if tok == "-isystem" and i + 1 < len(tokens):
            includes.append(tokens[i + 1])
            i += 2
            continue
        # -D<macro>  OR  -D <macro>
        if tok == "-D" and i + 1 < len(tokens):
            defines.append(tokens[i + 1])
            i += 2
            continue
        if tok.startswith("-D"):
            defines.append(tok[2:])
            i += 1
            continue
        i += 1

    # Dedupe while preserving order (insertion-ordered dict).
    includes = list(dict.fromkeys(includes))
    defines = list(dict.fromkeys(defines))

    # PIO's CPPDEFINES wants either ["MACRO"] or [("MACRO", "value")].
    # Translate "FOO=bar" → ("FOO", "bar"), "FOO" → "FOO".
    cppdefines = []
    for d in defines:
        if "=" in d:
            k, v = d.split("=", 1)
            cppdefines.append((k, v))
        else:
            cppdefines.append(d)

    env.AppendUnique(CPPPATH=includes, CPPDEFINES=cppdefines)
    print(f"[ameba] pio check: injected {len(includes)} include path(s), "
          f"{len(cppdefines)} define(s) from compile_commands.json")


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

    # Copy firmware artifacts into PIO BUILD_DIR for the standard PIO flow.
    #
    # Naming policy (aligned with platform-espressif32 / ststm32 conventions):
    #   firmware.elf      -> AP (Application Processor) ELF; the only ELF most
    #                        users care about. Used by `pio debug`, GDB,
    #                        `pio run -t size`. Mirrors ESP/STM32's single-ELF
    #                        convention — debugger attaches to AP, period.
    #   firmware.bin      -> SDK's app.bin (all cores packed; what `pio device`
    #                        displays as the firmware blob)
    #   firmware_ota.bin  -> SDK's ota_all.bin (full OTA payload incl. boot)
    #   firmware_boot.bin -> SDK's boot.bin (bootloader)
    #
    # NP (Network Processor) cores: NOT exposed as separate ELFs. Their size
    # IS counted in the Flash:/RAM: report below — Ameba's NP often hosts the
    # Wi-Fi driver (~340 KB), too significant to silently exclude from totals.
    # But the ELFs themselves stay inside build_<SOC>/build/project_<sdk>/...
    # for advanced debugging; standard PIO flow doesn't need them in
    # .pio/build/<env>/.
    os.makedirs(PROJECT_BUILD_DIR, exist_ok=True)

    # Read core layout from board manifest (build.cores). First entry is
    # the AP, rest are NPs. See per-board JSON for the canonical declaration.
    cores = board.get("build.cores", [])
    if not cores:
        sys.stderr.write(
            f"Error: board '{board.id}' missing 'build.cores' array; "
            "platform requires at least one core declaration.\n"
        )
        env.Exit(1)
    ap_sdk_project = cores[0].get("sdk_project")
    if not ap_sdk_project:
        sys.stderr.write(
            f"Error: board '{board.id}' build.cores[0].sdk_project missing.\n"
        )
        env.Exit(1)

    ap_elf_src = join(
        EXTERN_BUILD_DIR, "build", f"project_{ap_sdk_project}",
        "image", "target_img2.axf",
    )

    artifacts = [
        (ap_elf_src,                            "firmware.elf"),
        (join(EXTERN_BUILD_DIR, "app.bin"),     "firmware.bin"),
        (join(EXTERN_BUILD_DIR, "ota_all.bin"), "firmware_ota.bin"),
        (join(EXTERN_BUILD_DIR, "boot.bin"),    "firmware_boot.bin"),
    ]
    for src, dst_name in artifacts:
        dst = join(PROJECT_BUILD_DIR, dst_name)
        if isfile(src):
            shutil.copyfile(src, dst)
            print(f"[ameba] copied {src} -> {dst}")
        else:
            # ELF and app.bin are required; OTA/boot are optional artifacts.
            level = "WARNING" if dst_name in ("firmware.elf", "firmware.bin") else "info"
            print(f"[ameba] {level}: {src} not found (skipped {dst_name})")

    _export_compile_commands()

    # Standard PIO Flash:/RAM: progress bar — runs after artifacts are in place.
    if _arm_size_tool:
        _print_size_report(_arm_size_tool, board, cores)


def _resolve_arm_size_tool():
    """Find arm-none-eabi-size in the SDK-managed toolchain cache.

    The SDK auto-fetches the toolchain into ${RTK_TOOLCHAIN_DIR}/asdk-<version>/
    on first build. We glob for any `asdk-*/linux/newlib/bin/arm-none-eabi-size`.
    Returns None if not yet fetched (size report will silently no-op until then).

    Search order matches _make_sdk_env(): $RTK_TOOLCHAIN_DIR override first,
    then the SDK default ~/rtk-toolchain.
    """
    import glob
    cache_root = (
        os.environ.get("RTK_TOOLCHAIN_DIR")
        or os.path.expanduser("~/rtk-toolchain")
    )
    candidates = sorted(glob.glob(
        join(cache_root, "asdk-*", "linux", "newlib", "bin", "arm-none-eabi-size")
    ))
    return candidates[-1] if candidates else None


# Resolved at script-evaluation time so build_firmware() can use it.
_arm_size_tool = _resolve_arm_size_tool()


# ISA-keyed regex tables for `arm-none-eabi-size -A` section parsing.
#
# Section names are stable across the Ameba SoC families per ld script audit
# (amebadplus / amebagreen2 / amebalite / amebasmart hp+lp / RTL8720F all share
# the .xip_image2.text / .sram_image2.text.data / .ram_image2.bss family).
# RISC-V (kr4 in amebalite) adds .ram_image2.sbss for small BSS.
# Cortex-A (ap in amebasmart, RTL8730E future) uses standard GCC layout
# (.code/.data/.bss/.heap/.stack) on top of .xip_image2.text — different beast.
_SIZE_REGEX_BY_ISA = {
    "arm-cortex-m": {
        "prog": (
            r"^(?:\.xip_image2\.text|\.ARM\.exidx|\.ARM\.extab"
            r"|\.psram_image2\.text\.data)\s+(\d+).*"
        ),
        "data": (
            r"^(?:\.ram_image2\.entry|\.sram_image2\.text\.data"
            r"|\.sram_timer_idle_task_stack\.bss|\.ram_image2\.bss"
            r"|\.ram_image2\.nocache\.data|\.sram_rtos_static_[0-9]+\.bss"
            r"|\.psram_image2\.bss)\s+(\d+).*"
        ),
    },
    "riscv": {
        # RISC-V kr4: identical to ARM-M plus .ram_image2.sbss (small BSS).
        "prog": (
            r"^(?:\.xip_image2\.text|\.ARM\.exidx|\.ARM\.extab"
            r"|\.psram_image2\.text\.data)\s+(\d+).*"
        ),
        "data": (
            r"^(?:\.ram_image2\.entry|\.sram_image2\.text\.data"
            r"|\.sram_timer_idle_task_stack\.bss|\.ram_image2\.bss"
            r"|\.ram_image2\.sbss|\.ram_image2\.nocache\.data"
            r"|\.sram_rtos_static_[0-9]+\.bss|\.psram_image2\.bss)\s+(\d+).*"
        ),
    },
    "arm-cortex-a": {
        # Cortex-A (e.g. CA32 in amebasmart `ap` core, RTL8730E future):
        # standard GCC sections layered on top of .xip_image2.text.
        # .heap/.stack are reserved (allocated, not yet used) -- exclude
        # from RAM total to match user expectation of "what code consumes".
        # .mmu_tbl / .xlat_table are page tables (small but flash-resident).
        "prog": (
            r"^(?:\.xip_image2\.text|\.code|\.text|\.rodata"
            r"|\.ARM\.exidx|\.ARM\.extab|\.ctors|\.dtors"
            r"|\.preinit_array|\.init_array|\.fini_array"
            r"|\.mmu_tbl|\.xlat_table|\.bluetooth_trace\.text)\s+(\d+).*"
        ),
        "data": (
            r"^(?:\.data|\.bss|\.psram_heap\.start)\s+(\d+).*"
        ),
    },
}


def _print_size_report(size_tool: str, board_obj, cores: list):
    """Reproduce PIO's CheckUploadSize logic locally, multi-core aware.

    `cores` is the board manifest's build.cores list; first entry is AP,
    rest are NPs. We feed every core's image2 ELF to `arm-none-eabi-size -A`
    so the Flash:/RAM: totals reflect the full device occupation (NP runs
    Wi-Fi driver, sometimes ~340 KB — too significant to silently drop).

    Per-core regex selection is keyed on `cores[*].isa`:
      arm-cortex-m / riscv / arm-cortex-a (see _SIZE_REGEX_BY_ISA).
    """
    import re as _re
    import subprocess as _sp

    prog_max = int(board_obj.get("upload.maximum_size", 0))
    data_max = int(board_obj.get("upload.maximum_ram_size", 0))
    if prog_max == 0:
        return

    # Resolve every core's actual SDK build ELF (not the .pio/build copy --
    # NPs aren't copied there). Skip cores whose ELF doesn't exist yet.
    core_elfs = []  # list of (sdk_project, isa, elf_path)
    for core in cores:
        sdk_project = core.get("sdk_project")
        isa = core.get("isa", "arm-cortex-m")
        if not sdk_project:
            continue
        elf = join(EXTERN_BUILD_DIR, "build", f"project_{sdk_project}",
                   "image", "target_img2.axf")
        if isfile(elf):
            core_elfs.append((sdk_project, isa, elf))
    if not core_elfs:
        return

    # Run `size -A -d` once per ISA (sections regex differs). Most boards
    # are single-ISA so this is one subprocess; the kr4+km4 amebalite case
    # needs two (RISC-V + ARM); a hypothetical mixed CA32+M-core SoC needs
    # three. All cheap.
    prog_size = 0
    data_size = 0
    for isa, regex_set in _SIZE_REGEX_BY_ISA.items():
        elfs_for_isa = [e for (_, i, e) in core_elfs if i == isa]
        if not elfs_for_isa:
            continue
        try:
            res = _sp.run(
                [size_tool, "-A", "-d", *elfs_for_isa],
                capture_output=True, text=True, check=True,
            )
        except (_sp.CalledProcessError, FileNotFoundError) as ex:
            print(f"[ameba] size report skipped for isa={isa}: {ex}")
            continue

        prog_re = _re.compile(regex_set["prog"])
        data_re = _re.compile(regex_set["data"])
        for line in res.stdout.split("\n"):
            line = line.strip()
            mp = prog_re.search(line)
            if mp:
                prog_size += sum(int(v) for v in mp.groups())
                continue
            md = data_re.search(line)
            if md:
                data_size += sum(int(v) for v in md.groups())

    # Warn (not fail) if the manifest declares an ISA we don't have a regex
    # for — we'd silently undercount otherwise.
    unknown_isas = {i for (_, i, _) in core_elfs} - set(_SIZE_REGEX_BY_ISA)
    if unknown_isas:
        print(f"[ameba] WARNING: no size regex for isa={sorted(unknown_isas)}; "
              "report may undercount. Add to _SIZE_REGEX_BY_ISA in builder/main.py.")

    def _bar(value, total):
        pct = float(value) / float(total) if total else 0.0
        blocks = min(int(round(10 * pct)), 10)
        return "[{:{}}] {: 6.1%} (used {:d} bytes from {:d} bytes)".format(
            "=" * blocks, 10, pct, value, total,
        )

    print('Advanced Memory Usage is available via '
          '"PlatformIO Home > Project Inspect"')
    if data_max:
        print(f"RAM:   {_bar(data_size, data_max)}")
    print(f"Flash: {_bar(prog_size, prog_max)}")

    if data_max and data_size > data_max:
        sys.stderr.write(
            f"Warning! data size ({data_size}) > max ({data_max})\n"
        )
    if prog_size > prog_max:
        sys.stderr.write(
            f"Error: program size ({prog_size}) > max ({prog_max})\n"
        )
        env.Exit(1)


def _parse_extra_images(raw: str):
    """Parse `board_upload.extra_images` from platformio.ini into a list of dicts.

    User format (one per line, whitespace-separated):
        name_or_path  start_addr  end_addr

    `name_or_path` is either a filename (resolved relative to PROJECT_DIR's
    build_<SOC>/ tree) or an absolute path. Addresses are 0x-prefixed hex.

    Returns: list of {"path": abspath, "start_addr": int, "end_addr": int,
                      "label": basename} -- empty if `raw` is empty/None.
    """
    out = []
    if not raw:
        return out
    for line_no, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            sys.stderr.write(
                f"[ameba] board_upload.extra_images line {line_no}: expected "
                f"'<name_or_path> <start_addr> <end_addr>', got {line!r}\n"
            )
            env.Exit(1)
        name_or_path, start_str, end_str = parts
        try:
            start_addr = int(start_str, 16)
            end_addr = int(end_str, 16)
        except ValueError as ex:
            sys.stderr.write(
                f"[ameba] board_upload.extra_images line {line_no}: address "
                f"parse failed ({ex}); use 0x-prefixed hex.\n"
            )
            env.Exit(1)
            continue  # unreachable, but Pyright wants the binding clear
        # Resolve path: absolute as-is, relative against PROJECT_DIR
        if os.path.isabs(name_or_path):
            path = name_or_path
        else:
            path = os.path.realpath(join(PROJECT_DIR, name_or_path))
        out.append({
            "path": path,
            "start_addr": start_addr,
            "end_addr": end_addr,
            "label": os.path.basename(name_or_path),
        })
    return out


def _resolve_default_layout():
    """Call SDK's parse_project() to get the default 4-region layout.

    Returns list of {"path": abspath, "start_addr": int, "end_addr": int,
                     "type": "IMG_BOOT"/"IMG_APP_OTA1"/"VFS1"/...} for the
    current SoC's default partition table.

    The SDK's parse_project() hardcodes build_dir = <sdk_root>/build_<soc>/.
    We use EXTERN_DIR mode so artifacts are at <PROJECT_DIR>/build_<soc>/.
    Workaround: create a temporary symlink <sdk_root>/build_<soc> ->
    <PROJECT_DIR>/build_<soc> for the duration of the call, then remove it.
    Idempotent: any pre-existing symlink/dir is left alone.
    """
    parser_dir = join(SDK_DIR, "tools", "ameba", "ameba_dev_mcp", "config")
    if parser_dir not in sys.path:
        sys.path.insert(0, parser_dir)
    try:
        from flashcfg_parser import parse_project, FlashCfgParseError
    except ImportError as ex:
        print(f"[ameba] WARNING: SDK flashcfg_parser unavailable ({ex}); "
              "extra_images skipped, falling back to default ameba.py flash.")
        return None

    # Set up the symlink only if (a) SDK build_dir doesn't already exist
    # AND (b) our EXTERN_BUILD_DIR does. Skip otherwise to avoid stomping on
    # a real SDK-tree build.
    sdk_build_dir = join(SDK_DIR, f"build_{SOC}")
    created_symlink = False
    if not os.path.lexists(sdk_build_dir) and isdir(EXTERN_BUILD_DIR):
        try:
            os.symlink(EXTERN_BUILD_DIR, sdk_build_dir)
            created_symlink = True
        except OSError as ex:
            print(f"[ameba] WARNING: cannot create temporary symlink "
                  f"{sdk_build_dir} -> {EXTERN_BUILD_DIR} ({ex}); "
                  "extra_images skipped.")
            return None

    try:
        parsed = parse_project(SDK_DIR, SOC)
    except (FlashCfgParseError, Exception) as ex:
        print(f"[ameba] WARNING: layout parse failed ({ex}); "
              "extra_images skipped, falling back to default ameba.py flash.")
        return None
    finally:
        if created_symlink:
            try:
                os.unlink(sdk_build_dir)
            except OSError:
                pass

    # SDK ResolvedImage.path points at <sdk_root>/build_<soc>/<file>, but our
    # EXTERN_DIR mode puts artifacts at <PROJECT_DIR>/build_<soc>/<file>.
    # Rewrite each path by basename so the symlinked path doesn't leak out.
    images = []
    for img in parsed.images:
        rewritten_path = join(EXTERN_BUILD_DIR, os.path.basename(img.path))
        images.append({
            "path": rewritten_path,
            "start_addr": int(img.start_addr, 16),
            "end_addr": int(img.end_addr, 16),
            "type": img.type,
            "label": os.path.basename(img.path),
        })
    return images


def _check_image_fits(images: list):
    """Validate each image's file size <= declared (end_addr - start_addr + 1).

    Aborts the build with env.Exit(1) on any over-region image. Optional
    images (those that don't exist on disk) are skipped silently.
    """
    bad = []
    for img in images:
        if not isfile(img["path"]):
            continue  # optional images (vfs, user partitions) may be absent
        actual = os.path.getsize(img["path"])
        region_size = img["end_addr"] - img["start_addr"] + 1
        if actual > region_size:
            bad.append((img, actual, region_size))

    if bad:
        sys.stderr.write("[ameba] ERROR: image(s) exceed their flash region:\n")
        for img, actual, region_size in bad:
            overage = actual - region_size
            sys.stderr.write(
                f"  {img['label']:<20} "
                f"size={actual} bytes ({actual/1024:.1f} KB), "
                f"region=0x{img['start_addr']:08X}-0x{img['end_addr']:08X} "
                f"({region_size} bytes / {region_size/1024:.1f} KB), "
                f"OVER by {overage} bytes ({overage/1024:.1f} KB)\n"
            )
        sys.stderr.write(
            "[ameba] Reduce image size or expand the region in menuconfig "
            "(`pio run -t menuconfig` -> Flash Layout).\n"
        )
        env.Exit(1)


def upload_firmware(*_args, **_kwargs):

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

    # Extra images: user-defined custom regions to flash on top of the default
    # boot/ota1/(ota2)/vfs1 layout. Format documented in _parse_extra_images.
    extra_raw = env.GetProjectOption("board_upload.extra_images", "") or ""
    extra_images = _parse_extra_images(extra_raw)

    # Two flash modes:
    #   (a) No extra_images -> default behavior. ameba.py flash live-parses
    #       layout from .config and uses canonical bin filenames. Rock-solid,
    #       handles every SoC family the SDK supports.
    #   (b) extra_images present -> we must build the FULL partition table
    #       (default 4 regions + user extras) and pass it via repeated
    #       `-i name addr_start addr_end` flags. ameba.py flash treats `-i`
    #       as REPLACE not APPEND, so omitting any default region would skip
    #       it. We call SDK's parse_project() to get the canonical defaults.
    if extra_images:
        default_images = _resolve_default_layout()
        if default_images is None:
            # parse_project failed -- fall through to mode (a) but warn that
            # extras won't be flashed. Better than silently breaking.
            print("[ameba] WARNING: extra_images requested but layout parse "
                  "failed; flashing default layout only (extras NOT flashed).")
        else:
            # Size-check user extras only -- defaults are vendor-managed and
            # already pass via the standard build flow.
            _check_image_fits(extra_images)

            all_images = default_images + extra_images
            print(f"[ameba] custom partition table ({len(all_images)} entries):")
            for img in all_images:
                tag = img.get("type", "USER")
                size = (os.path.getsize(img["path"])
                        if isfile(img["path"]) else "(missing)")
                print(f"[ameba]   {tag:<14} {img['label']:<22} "
                      f"@ 0x{img['start_addr']:08X}-0x{img['end_addr']:08X}  "
                      f"size={size}")

            # Translate to ameba.py flash's `-i name start end` (repeatable).
            # We use 'image' so _ameba_py_args's option iteration handles it
            # generically; pass as a list -> emitted multiple times.
            #
            # Important: ameba.py's AmebaFlash.py download_handler treats
            # `end` as EXCLUSIVE (region size = end - start, see
            # tools/ameba/Flash/base/download_handler.py:837). But
            # flashcfg_parser gives us end_addr INCLUSIVE (0x08722FFF is
            # the last byte of VFS1, not one-past-end). So we add 1 here
            # to convert inclusive->exclusive, otherwise a perfectly-sized
            # image fails with "image too large for region".
            upload_opts["image"] = [
                [img["path"], hex(img["start_addr"]), hex(img["end_addr"] + 1)]
                for img in all_images
            ]

    print(f"[ameba] uploading SoC={SOC}, opts={ {k: v for k, v in upload_opts.items() if k != 'image'} }")
    for cmd in _ameba_py_args("flash", soc=SOC, upload_opts=upload_opts):
        _run_ameba_flash(cmd, sdk_env, label="upload")


def erase_flash(*_args, **_kwargs):
    """Full chip erase + reflash via `ameba.py flash --chip-erase`.

    Wired up as `pio run -t erase`. End-to-end behavior:
      1. Wipe the entire SPI flash (boot + app + vfs + user partitions)
      2. Reflash the current project's boot.bin + app.bin
      3. Board reboots into the freshly-flashed firmware

    Use cases:
      - Recover from a corrupted partition table or stuck OTA state
      - Force a clean install when board_upload.chip_erase=yes feels too
        magical to put in platformio.ini

    Honors the same port/baud/memory_type/remote-server settings as
    upload_firmware().
    """

    sdk_env = _make_sdk_env()
    upload_opts = {"chip-erase": True}

    # Re-use upload's port/baud/memory-type knobs so erase respects whatever
    # the user already configured in platformio.ini / board JSON.
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

    print(f"[ameba] CHIP ERASE + RE-FLASH SoC={SOC} "
          "(full flash wipe followed by reflash of current project images)")
    for cmd in _ameba_py_args("flash", soc=SOC, upload_opts=upload_opts):
        _run_ameba_flash(cmd, sdk_env, label="erase + reflash")


def _resolve_vfs_region():
    """Locate the VFS1 region in the SDK's default partition layout.

    Returns dict {start_addr, end_addr, size, label} or None if no VFS1
    region exists in the current partition table (= user has disabled
    VFS in menuconfig, or this SoC family doesn't ship one).

    Caller responsible for env.Exit() on None — we just report.
    """
    images = _resolve_default_layout()
    if images is None:
        return None
    for img in images:
        if img.get("type") == "VFS1":
            size = img["end_addr"] - img["start_addr"] + 1
            return {
                "start_addr": img["start_addr"],
                "end_addr": img["end_addr"],
                "size": size,
                "label": img["label"],
            }
    return None


def build_fs_image(*_args, **_kwargs):
    """Build a LittleFS image from PROJECT_DIR/data/.

    Wired up as `pio run -t buildfs`. Output goes to
    $BUILD_DIR/firmware_fs.bin (matches espressif32 naming).

    Block size is hardcoded at 4096 — Ameba flash sectors are 4 KB and
    the SDK's mount-littlefs code path assumes that. block_count is
    derived from the VFS1 partition size declared in the SDK's flash
    layout (live-parsed via flashcfg_parser, so menuconfig changes are
    honored automatically).

    Filesystem choice: LittleFS only. SDK's vfs.py supports fatfs too,
    but Ameba's runtime VFS adapter favors LittleFS and that's what
    most users actually use. fatfs support is one CLI flag away if a
    user asks (pass `-t fatfs` instead), but we don't expose that until
    there's demand.
    """
    import subprocess

    # 1. Validate filesystem type (only littlefs supported for now)
    fs_type = (env.GetProjectOption("board_build.filesystem", "littlefs")
               or "littlefs").lower()
    if fs_type != "littlefs":
        print(f"[ameba] ERROR: board_build.filesystem={fs_type!r} not "
              "supported. Only 'littlefs' works at the moment "
              "(SDK's vfs.py also supports fatfs but we haven't exposed it; "
              "open an issue if you need it).")
        env.Exit(1)
        return

    # 2. Validate data dir exists and has files
    data_dir = join(PROJECT_DIR, "data")
    if not isdir(data_dir):
        print(f"[ameba] ERROR: data/ directory not found at {data_dir}. "
              "Create it and put files there for buildfs to pack.")
        env.Exit(1)
        return

    file_count = sum(len(files) for _, _, files in os.walk(data_dir))
    if file_count == 0:
        print("[ameba] WARNING: data/ is empty; will produce an empty "
              "LittleFS image (just metadata).")

    # 3. Resolve VFS1 partition geometry
    region = _resolve_vfs_region()
    if region is None:
        print("[ameba] ERROR: no VFS1 region in current partition layout. "
              "Either:\n"
              "  - This SoC family doesn't ship VFS by default (rare)\n"
              "  - VFS is disabled in menuconfig — re-enable via "
              "`pio run -t menuconfig` -> Flash Layout\n"
              "  - You haven't built once yet (run `pio run` first to "
              "generate the layout config)")
        env.Exit(1)
        return

    BLOCK_SIZE = 4096  # Ameba flash sector size, NOT user-configurable
    block_count = region["size"] // BLOCK_SIZE

    if block_count < 4:
        print(f"[ameba] ERROR: VFS1 region too small "
              f"({region['size']} bytes / {block_count} blocks); "
              "LittleFS needs at least 4 blocks. "
              "Resize the partition via menuconfig.")
        env.Exit(1)
        return

    # 4. Run vfs.py
    out_path = join(PROJECT_BUILD_DIR, "firmware_fs.bin")
    os.makedirs(PROJECT_BUILD_DIR, exist_ok=True)
    vfs_py = join(SDK_DIR, "tools", "image_scripts", "vfs.py")
    if not isfile(vfs_py):
        print(f"[ameba] ERROR: vfs.py not found at {vfs_py}. "
              "SDK layout may have changed.")
        env.Exit(1)
        return

    sdk_python = join(SDK_DIR, ".venv", "bin", "python3")
    if not isfile(sdk_python):
        print(f"[ameba] ERROR: SDK venv not found at {sdk_python}. "
              "Run `pio run` once first to provision it.")
        env.Exit(1)
        return

    cmd = [
        sdk_python, vfs_py,
        "-t", "LITTLEFS",  # vfs.py argparse choices = ['LITTLEFS', 'FATFS'] (uppercase)
        "-s", str(BLOCK_SIZE),
        "-c", str(block_count),
        "-dir", data_dir,
        "-out", out_path,
    ]
    print(f"[ameba] buildfs SoC={SOC} fs=littlefs "
          f"region=0x{region['start_addr']:08X}-0x{region['end_addr']:08X} "
          f"({region['size']} bytes / {block_count} blocks)")
    print(f"[ameba] $ {' '.join(cmd)}")
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"[ameba] vfs.py failed (rc={rc})")
        env.Exit(rc)
        return

    out_size = os.path.getsize(out_path)
    print(f"[ameba] built {out_path} ({out_size} bytes, "
          f"{file_count} source files)")


def upload_fs_image(*_args, **_kwargs):
    """Flash the LittleFS image to the VFS1 partition.

    Wired up as `pio run -t uploadfs`. Depends on buildfs producing
    $BUILD_DIR/firmware_fs.bin. Calls ameba.py flash with a single
    `-i firmware_fs.bin <start> <end>` triple — note this REPLACES
    the default partition table (unlike upload_firmware which uses
    extra_images for additive flashing).
    """

    fs_bin = join(PROJECT_BUILD_DIR, "firmware_fs.bin")
    if not isfile(fs_bin):
        print(f"[ameba] ERROR: {fs_bin} not found. "
              "Run `pio run -t buildfs` first.")
        env.Exit(1)
        return

    region = _resolve_vfs_region()
    if region is None:
        print("[ameba] ERROR: cannot resolve VFS1 region; "
              "run `pio run` once to populate the SDK build dir.")
        env.Exit(1)
        return

    fs_size = os.path.getsize(fs_bin)
    if fs_size > region["size"]:
        print(f"[ameba] ERROR: firmware_fs.bin is {fs_size} bytes but "
              f"VFS1 partition is only {region['size']} bytes. "
              "Either shrink data/ or grow the VFS1 partition in "
              "menuconfig -> Flash Layout.")
        env.Exit(1)
        return

    sdk_env = _make_sdk_env()
    # ameba.py flash -i expects EXCLUSIVE end; flashcfg_parser gives us
    # INCLUSIVE end. Convert with +1 (see download_handler.py:837 in SDK).
    upload_opts = {
        "image": [[
            fs_bin,
            hex(region["start_addr"]),
            hex(region["end_addr"] + 1),
        ]],
    }

    # Mirror upload_firmware()'s port/baud/memory_type plumbing.
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

    print(f"[ameba] uploadfs SoC={SOC} "
          f"-> 0x{region['start_addr']:08X}-0x{region['end_addr']:08X} "
          f"({fs_size}/{region['size']} bytes used)")
    for cmd in _ameba_py_args("flash", soc=SOC, upload_opts=upload_opts):
        _run_ameba_flash(cmd, sdk_env, label="uploadfs")


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
    name="erase",
    dependencies=None,
    actions=erase_flash,
    title="Erase Flash + Reflash",
    description="Wipe the entire SPI flash (boot + app + vfs + user) "
                "then reflash the current project's boot.bin + app.bin. "
                "The board reboots into the freshly-flashed firmware.",
)

env.AddCustomTarget(
    name="buildfs",
    dependencies=None,
    actions=build_fs_image,
    title="Build Filesystem Image",
    description="Pack PROJECT_DIR/data/ into a LittleFS image at "
                "$BUILD_DIR/firmware_fs.bin sized to the SDK's VFS1 "
                "partition. Requires `pio run` to have run once "
                "(needs the partition layout).",
)

env.AddCustomTarget(
    name="uploadfs",
    dependencies=None,
    actions=[build_fs_image, upload_fs_image],
    title="Upload Filesystem Image",
    description="Build (`buildfs`) and flash a LittleFS image to the "
                "VFS1 partition. Does NOT touch the app/boot images.",
)

# `pio run` default
Default(target_firmware)

# -----------------------------------------------------------------------------
# Wire up the standard PIO `pio run -t size` target.
#
# The post-build `Flash:`/`RAM:` progress bar is emitted from inside
# build_firmware() via `_print_size_report()` -- we cannot reuse PIO's own
# CheckUploadSize because it requires `target` to be an env.Program() output.
# But we still expose `pio run -t size` as a standalone target so users can
# rerun the size summary without rebuilding.
#
# Section taxonomy (verified empirically on RTL8721Dx, asdk-10.3.1 toolchain):
#   FLASH (XIP from external flash):
#     .xip_image2.text                  -- main code (MAJORITY, esp KM0 = Wi-Fi driver)
#     .ARM.exidx / .ARM.extab           -- C++ unwind tables (small but counted)
#     .psram_image2.text.data           -- when PSRAM is enabled (currently 0 B)
#   RAM (SRAM/PSRAM resident):
#     .ram_image2.entry                 -- vectors / entry stub
#     .sram_image2.text.data            -- SRAM-resident code+data
#     .ram_image2.bss                   -- BSS in SRAM
#     .ram_image2.nocache.data          -- non-cached SRAM data (rare)
#     .sram_timer_idle_task_stack.bss   -- FreeRTOS idle stack
#     .sram_rtos_static_*.bss           -- FreeRTOS static control blocks
#     .psram_image2.bss                 -- BSS in PSRAM (when enabled)
#
# .debug_*, .comment, .stab*, .ARM.attributes, .coex_trace.text -- excluded
# (debug info / metadata, not flashed to the device).
if _arm_size_tool:
    # Resolve all cores' SDK ELFs for the standalone `pio run -t size` target.
    # Same data source as build_firmware()'s _print_size_report — board's
    # build.cores array. SDK ELFs (not the AP-only firmware.elf in PIO
    # BUILD_DIR) are passed so `size -B -d` reports per-core line items.
    _all_core_elfs = [
        join(EXTERN_BUILD_DIR, "build", f"project_{c['sdk_project']}",
             "image", "target_img2.axf")
        for c in board.get("build.cores", [])
        if c.get("sdk_project")
    ]
    _size_cmd_args = " ".join(f'"{e}"' for e in _all_core_elfs)
    env.Replace(
        SIZETOOL=_arm_size_tool,
        SIZEPRINTCMD=f'"$SIZETOOL" -B -d {_size_cmd_args}',
    )
    env.AddCustomTarget(
        name="size",
        dependencies=None,
        actions=env.VerboseAction("$SIZEPRINTCMD", "Calculating size"),
        title="Program Size",
        description="Print firmware size summary (all cores' image2 ELFs)",
    )

    # Register `checkprogsize` as an alias of target_firmware. PIO's core
    # builder/main.py auto-injects `Default("checkprogsize")` whenever
    # SIZETOOL is set (~/.platformio/penv/.../platformio/builder/main.py
    # ~line 185-193). Without this alias, that Default() call fails with
    #   *** Do not know how to make File target `checkprogsize'. Stop.
    # which makes SCons exit non-zero AFTER our build_firmware succeeded
    # — so the user sees the size report print correctly, then [FAILED].
    #
    # The actual size report is already emitted inside build_firmware()
    # via _print_size_report(). This alias just teaches SCons what
    # `checkprogsize` resolves to (= the firmware build itself), keeping
    # the standard PIO target graph satisfied.
    AlwaysBuild(env.Alias("checkprogsize", target_firmware))

env.Replace(
    PROGNAME="firmware",
    PROGSUFFIX=".elf",
)

# -----------------------------------------------------------------------------
# Module-level: inject CPPPATH/CPPDEFINES from a previous build's
# compile_commands.json so `pio check` (run WITHOUT a build prefix) can find
# SDK headers and macros.
#
# `pio check` only loads this builder script (it doesn't invoke build_firmware);
# the call inside build_firmware is for the WITH-build flow where this script
# also runs end-to-end. Both paths converge on the same in-memory env.
# Caveat: first-ever `pio check` (no compile_commands.json yet) is a no-op +
# warning. User runs `pio run` once, then check works on every subsequent run.
# -----------------------------------------------------------------------------
_inject_check_metadata()
