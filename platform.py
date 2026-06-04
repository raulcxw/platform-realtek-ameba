# Copyright 2026 raul_chen <chen.raul@example.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""platform-realtek-ameba: PlatformIO entry for the Realtek Ameba RTOS SDK.

Strategy:
1. Treat `ameba-rtos` as a black-box SDK driven by `ameba.py`.
2. Toolchain is downloaded by the upstream SDK on first `ameba.py build`.
3. `builder/main.py` shells out to `ameba.py build` for the selected SoC.

We handle the framework fetch internally (skipping heavy submodules like 
audio/ui/lvgl) to keep the initial download around ~30MB.
"""

import json
import os
import shutil
import subprocess
import sys

from platformio.public import PlatformBase


IS_WINDOWS = sys.platform.startswith("win")

# Upstream SDK source. Override with $AMEBA_SDK_GIT_URL for a fork or mirror.
DEFAULT_SDK_GIT_URL = "https://github.com/Ameba-AIoT/ameba-rtos.git"
DEFAULT_SDK_BRANCH = "master"

# Shallow-clone depth. Mirrors Realtek's official download instructions
# (`git clone --depth=5 ...`). A small buffer (>1) keeps shallow submodule
# fetches robust when a recorded submodule SHA is a few commits behind the
# branch tip.
DEFAULT_SDK_DEPTH = 5

# Realtek splits the upstream into two editions (per the official docs):
#   - "sdk"  (default): base SDK — Wi-Fi + BT, no submodules (~30 MB).
#   - "xdk"  (extended): adds the AI-voice / tflite_micro / UI(lvgl) / audio /
#            speechmind submodules for high-level features (~1.1 GB).
# Most users only need the base SDK, so it is the default. Opt into the
# extended edition with $AMEBA_SDK_EDITION=xdk. The choice is consumed once,
# at first clone (see _ensure_ameba_rtos_package).
DEFAULT_SDK_EDITION = "sdk"

# PIO expects ``framework-ameba-rtos`` as the package name to match
# ``frameworks.ameba-rtos.package`` in platform.json.
FRAMEWORK_PKG_NAME = "framework-ameba-rtos"


class RealtekamebaPlatform(PlatformBase):
    """PlatformIO platform for Realtek Ameba RTOS.

    Class name follows PIO convention: ``PlatformFactory.get_clsname()`` strips
    ``-``/``_`` from ``platform.json:name`` and only capitalizes the first
    letter. So ``realtek-ameba`` → ``RealtekamebaPlatform`` (NOT
    ``RealtekAmebaPlatform``). Don't "fix" this casing — PIO won't find the
    class.
    """

    def configure_default_packages(self, variables, targets):
        # Hook into PIO's standard `pio run -t clean` BEFORE the SDK venv
        # work below. PIO's CleanProject only wipes $BUILD_DIR (.pio/build/
        # <env>/), but our SDK runs in EXTERN_DIR mode and produces all
        # real artifacts in <PROJECT_DIR>/build_<SOC>/. Without this hook
        # `pio run -t clean` looks like it succeeded but the next
        # `pio run` is still incremental — exactly what the user
        # ran clean to avoid.
        #
        # We mirror the user-visible behavior of every other PIO platform:
        # one `pio run -t clean` wipes everything build-related.
        # ameba-clean stays around as a superset (also clears
        # compile_commands.json and soc_info.json).
        if "clean" in (targets or []):
            self._clean_extern_build_dir(variables)
            # Don't return — let PIO continue to its own CleanProject which
            # then wipes .pio/build/<env>/ and exits.

        # Ensure framework-ameba-rtos is present BEFORE PIO's package
        # manager kicks in. If this is the first build (or user wiped
        # ~/.platformio), we clone the SDK ourselves with
        # ``--no-recurse-submodules`` and write a package.json so the rest of
        # PIO accepts it as a valid framework package.
        #
        # We deliberately avoid PIO's ``packages.framework-ameba-rtos`` git
        # URL mechanism because PIO always uses ``git clone --recursive``,
        # which pulls 1.3 GB of submodules (audio, ui, aivoice, tflite_micro,
        # speechmind + nested lvgl 8.3 + lvgl 9.3) — none of which RTL8721D
        # blink/wifi builds actually need. Users who want those components can
        # ``cd ~/.platformio/packages/framework-ameba-rtos && git submodule
        # update --init component/audio`` (or similar) on demand.
        sdk_dir = self._ensure_ameba_rtos_package()

        # ALWAYS resync the SDK's Python venv against the current
        # tools/requirements.txt — separate concern from "is the SDK
        # cloned?". This runs on every `pio run` (it's idempotent: hash
        # match → ~10ms no-op). After `pio pkg update -p framework-ameba-rtos`
        # this is what catches a changed requirements.txt and re-runs
        # pip install --upgrade automatically.
        #
        # Also resolve the SDK that builder/main.py will actually use,
        # in case its discovery priority differs from platform.py's
        # (e.g. dev-tree fallback at ~/projects/.../repos/ameba-rtos
        # taking precedence). The venv must live next to the SDK that
        # builder will run ameba.py from, not where platform.py thinks
        # it lives.
        actual_sdk = self._resolve_active_sdk_dir(sdk_dir)
        if actual_sdk:
            self._setup_sdk_venv(actual_sdk)

        return super().configure_default_packages(variables, targets)

    def _resolve_active_sdk_dir(self, package_sdk_dir):
        """Mirror builder/main.py's _find_sdk_dir() lookup priority.

        builder/main.py walks: AMEBA_SDK_DIR → PIO package dir.
        We replicate that here so the venv install lands where the
        active SDK actually lives. If they diverge we'd resync the
        wrong venv and `pio run` would still fail with `Miss module: ...`.

        Returns the first SDK path that has ameba.py at root, or None
        as a last-ditch fallback (caller should print a warning).
        """
        candidates = [
            os.environ.get("AMEBA_SDK_DIR", "").strip(),
            package_sdk_dir,
        ]
        for c in candidates:
            if c and os.path.isfile(os.path.join(c, "ameba.py")):
                return c
        return None

    def _clean_extern_build_dir(self, variables):
        """Wipe SDK build artifacts on `pio run -t clean`.

        PIO core's CleanProject only removes $BUILD_DIR (.pio/build/<env>/).
        Our EXTERN_DIR build mode produces additional artifacts under
        PROJECT_DIR that PIO doesn't know about — clean them here so a
        single `pio run -t clean` actually returns the project to a
        pristine state:

          - <PROJECT_DIR>/build_<SOC>/         (real SDK build tree, ~350 MB)
          - <PROJECT_DIR>/compile_commands.json (mirror copy for IDEs)
          - <PROJECT_DIR>/soc_info.json         (SDK per-project SoC cache)
          - <PROJECT_DIR>/app_example/_pio_src_fragment.cmake
                                                (auto-generated src/ bridge)

        If we can't resolve the SoC (no board set), warn and fall through.
        """
        board_id = variables.get("board")
        if not board_id:
            sys.stderr.write(
                "[realtek-ameba] WARN: clean target without 'board' set; "
                "<PROJECT_DIR>/build_<SOC>/ left untouched.\n"
            )
            return

        try:
            board_config = self.board_config(board_id)
            soc = board_config.get("build.soc")
        except Exception as exc:
            sys.stderr.write(
                f"[realtek-ameba] WARN: clean couldn't resolve SoC for "
                f"board={board_id!r} ({exc}); <PROJECT_DIR>/build_<SOC>/ "
                f"left untouched.\n"
            )
            return

        if not soc:
            sys.stderr.write(
                f"[realtek-ameba] WARN: board {board_id!r} has no build.soc; "
                "<PROJECT_DIR>/build_<SOC>/ left untouched.\n"
            )
            return

        # PROJECT_DIR is the cwd when PIO runs `pio run` — same convention
        # builder/main.py uses (env.subst("$PROJECT_DIR")).
        project_dir = os.getcwd()
        extern_build_dir = os.path.join(project_dir, f"build_{soc}")
        if os.path.isdir(extern_build_dir):
            sys.stderr.write(
                f"[realtek-ameba] cleaning {extern_build_dir}\n"
            )
            shutil.rmtree(extern_build_dir, ignore_errors=True)

        # Stale auxiliary files that survive a naive PIO clean. They're
        # tiny (KB) but cause "stale data" debugging confusion if left:
        #   - compile_commands.json: IDE picks up old SDK paths after refactor
        #   - soc_info.json: SDK reads stale SoC name → "Invalid SOC" warnings
        #   - _pio_src_fragment.cmake: lists old src/ files after rename
        for stale in [
            os.path.join(project_dir, "compile_commands.json"),
            os.path.join(project_dir, "soc_info.json"),
            os.path.join(project_dir, "app_example", "_pio_src_fragment.cmake"),
        ]:
            if os.path.isfile(stale):
                sys.stderr.write(f"[realtek-ameba] removing {stale}\n")
                try:
                    os.remove(stale)
                except OSError:
                    pass

    def _ensure_ameba_rtos_package(self):
        """Clone ameba-rtos into the PIO package cache if not already there.

        Idempotent: if a valid ``package.json`` already exists in the package
        directory, this is a no-op.

        Honors ``$AMEBA_SDK_DIR`` — if set, we skip the clone entirely and
        only write ``package.json`` into that directory. This lets developers
        point at a local SDK fork without cloning twice.
        """
        # Developer override: AMEBA_SDK_DIR points at a local checkout.
        sdk_dir_override = os.environ.get("AMEBA_SDK_DIR", "").strip()
        if sdk_dir_override:
            if not os.path.isdir(sdk_dir_override):
                raise RuntimeError(
                    f"AMEBA_SDK_DIR={sdk_dir_override!r} is set but does not exist"
                )
            if not os.path.isfile(os.path.join(sdk_dir_override, "ameba.py")):
                raise RuntimeError(
                    f"AMEBA_SDK_DIR={sdk_dir_override!r} does not look like an "
                    "ameba-rtos checkout (no ameba.py at root)"
                )
            self._write_package_json(sdk_dir_override, source="local-override")
            return sdk_dir_override

        pkg_dir = self._packages_dir()

        # Already installed? Trust an existing package.json + ameba.py.
        if os.path.isfile(os.path.join(pkg_dir, "package.json")) and os.path.isfile(
            os.path.join(pkg_dir, "ameba.py")
        ):
            return pkg_dir

        # Stale/partial install — wipe and start clean.
        if os.path.isdir(pkg_dir):
            sys.stderr.write(
                f"[realtek-ameba] removing stale {pkg_dir} (no valid package.json)\n"
            )
            shutil.rmtree(pkg_dir)
        os.makedirs(os.path.dirname(pkg_dir), exist_ok=True)

        sdk_url = os.environ.get("AMEBA_SDK_GIT_URL", DEFAULT_SDK_GIT_URL)
        sdk_branch = os.environ.get("AMEBA_SDK_GIT_BRANCH", DEFAULT_SDK_BRANCH)

        edition = os.environ.get("AMEBA_SDK_EDITION", DEFAULT_SDK_EDITION).strip().lower()
        if edition not in ("sdk", "xdk"):
            raise RuntimeError(
                f"AMEBA_SDK_EDITION={edition!r} is invalid; expected 'sdk' "
                "(base: Wi-Fi + BT) or 'xdk' (extended: AI / tflite / UI / audio)."
            )
        want_xdk = edition == "xdk"

        clone_args = [
            "git",
            "clone",
            "--depth",
            str(DEFAULT_SDK_DEPTH),
            "--single-branch",
            "--branch",
            sdk_branch,
        ]
        if want_xdk:
            # Extended edition: recurse into all submodules but keep them
            # shallow (--shallow-submodules => depth 1 per submodule). This is
            # better than Realtek's documented `--recursive --depth=5`, which
            # pulls full submodule history.
            clone_args += ["--recurse-submodules", "--shallow-submodules"]
            sys.stderr.write(
                f"[realtek-ameba] First-time setup: cloning {sdk_url} "
                f"(branch={sdk_branch}, edition=XDK, all submodules) to {pkg_dir}\n"
                f"[realtek-ameba] XDK includes AI-voice / tflite_micro / UI(lvgl) "
                f"/ audio. This is a one-time ~1.1 GB download, "
                f"typically 10-15 minutes.\n"
            )
        else:
            clone_args += ["--no-recurse-submodules"]
            sys.stderr.write(
                f"[realtek-ameba] First-time setup: cloning {sdk_url} "
                f"(branch={sdk_branch}, edition=SDK, no submodules) to {pkg_dir}\n"
                f"[realtek-ameba] This is a one-time ~30 MB download, "
                f"typically 2-5 minutes. For AI / tflite / UI / audio features, "
                f"set $AMEBA_SDK_EDITION=xdk before the first build.\n"
            )
        clone_args += [sdk_url, pkg_dir]

        try:
            subprocess.check_call(clone_args)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"[realtek-ameba] git clone of ameba-rtos SDK failed (exit "
                f"{exc.returncode}). Either set $AMEBA_SDK_DIR to a local "
                f"checkout, or override $AMEBA_SDK_GIT_URL to a mirror."
            ) from exc

        self._write_package_json(pkg_dir, source="git")
        self._setup_sdk_venv(pkg_dir)
        sys.stderr.write(f"[realtek-ameba] SDK installed at {pkg_dir}\n")
        return pkg_dir

    def _setup_sdk_venv(self, sdk_dir):
        """Create / refresh the SDK Python venv to match tools/requirements.txt.

        The upstream ``ameba.py`` build pipeline relies on Python helpers
        (``axf2bin.py``, ``menuconfig.py``, etc.) that import third-party
        modules — most critically ``json5``, which CMake calls during
        ``ameba_soc_project_check`` before any source compilation. Without
        a populated ``$SDK/.venv``, cmake configure dies with::

            ERROR
            ➜ Miss module: json5
            ➜ Install by: pip install -r .../tools/requirements.txt

        The SDK ships ``env.sh`` to do this interactively for human
        developers, but PIO users never source it. We replicate the venv
        creation + pip install non-interactively here so first ``pio run``
        works out of the box.

        Idempotent strategy: SHA-256 fingerprint of ``tools/requirements.txt``
        is stored at ``$SDK/.venv/.pio_requirements_sha256`` after each
        successful install. On every ``pio run``, we re-hash the current
        requirements.txt — if the hash matches the stamp, skip; if it
        differs (SDK update changed deps, you edited the file, fresh
        install), run ``pip install -r requirements.txt --upgrade`` so
        new packages get installed and existing ones get upgraded.

        This means: after ``pio pkg update -p framework-ameba-rtos``,
        the user runs ``pio run`` and the venv resyncs automatically.
        No ``source env.sh`` needed, no manual ``pip install``, no docs
        the user has to remember.

        Escape hatch for unusual cases (manual ``pip uninstall`` inside
        the venv, etc.): delete the stamp file and the next ``pio run``
        will reinstall::

            rm $SDK/.venv/.pio_requirements_sha256
        """
        import hashlib

        venv_dir = os.path.join(sdk_dir, ".venv")
        venv_python = os.path.join(venv_dir, "bin", "python3")
        venv_pip = os.path.join(venv_dir, "bin", "pip")
        requirements = os.path.join(sdk_dir, "tools", "requirements.txt")
        stamp_path = os.path.join(venv_dir, ".pio_requirements_sha256")

        if not os.path.isfile(requirements):
            sys.stderr.write(
                f"[realtek-ameba] no tools/requirements.txt at {requirements}, "
                f"skipping venv setup (SDK layout may have changed)\n"
            )
            return

        # Compute current requirements.txt fingerprint.
        with open(requirements, "rb") as fh:
            req_hash = hashlib.sha256(fh.read()).hexdigest()

        # Idempotency: stamp matches AND venv interpreter exists → done.
        # We re-check venv_python existence because a user might have
        # `rm -rf .venv` while leaving the stamp behind (rare, but the
        # check is cheap and the failure mode is annoying).
        if os.path.isfile(venv_python) and os.path.isfile(stamp_path):
            try:
                with open(stamp_path, "r") as fh:
                    if fh.read().strip() == req_hash:
                        return  # venv healthy + requirements unchanged
            except OSError:
                pass  # stamp unreadable — fall through, reinstall

        # Need to either create venv or refresh it.
        venv_existed = os.path.isfile(venv_python)
        if not venv_existed:
            # Wipe a stale/partial venv before recreating.
            if os.path.isdir(venv_dir):
                shutil.rmtree(venv_dir)

            sys.stderr.write(
                f"[realtek-ameba] creating SDK venv at {venv_dir} and "
                f"installing requirements (one-time, ~30 seconds)\n"
            )
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "venv", venv_dir],
                    stdout=subprocess.DEVNULL,
                )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(
                    f"[realtek-ameba] failed to create SDK venv at {venv_dir}: {exc}"
                ) from exc
        else:
            sys.stderr.write(
                f"[realtek-ameba] tools/requirements.txt changed; "
                f"refreshing SDK venv at {venv_dir}\n"
            )

        # Build pip install args. Use --upgrade so existing deps get bumped
        # to the new version when SDK updates ship a tighter pin.
        # Use a domestic pip mirror by default for China-locale users (where
        # pypi.org timeouts are common). Override with $PIP_INDEX_URL upstream
        # if you want pypi.org or a private mirror.
        pip_args = [
            venv_pip,
            "install",
            "--quiet",
            "--upgrade",
            "-r",
            requirements,
        ]
        if not os.environ.get("PIP_INDEX_URL"):
            pip_args[2:2] = [
                "-i",
                "https://pypi.tuna.tsinghua.edu.cn/simple",
            ]
        try:
            subprocess.check_call(pip_args)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"[realtek-ameba] pip install -r {requirements} failed (exit "
                f"{exc.returncode}). Manual fix: cd {sdk_dir} && "
                f"python -m venv .venv && .venv/bin/pip install -r {requirements}"
            ) from exc

        # Write fingerprint AFTER successful install so a half-failed run
        # leaves no stamp and the next pio run will retry.
        try:
            with open(stamp_path, "w") as fh:
                fh.write(req_hash)
                fh.write("\n")
        except OSError as exc:
            sys.stderr.write(
                f"[realtek-ameba] WARN: could not write stamp file "
                f"{stamp_path} ({exc}); next `pio run` will reinstall.\n"
            )

    def _packages_dir(self):
        """Resolve ~/.platformio/packages/framework-ameba-rtos.

        Uses ``PioPlatform().get_dir()`` machinery if available, otherwise
        falls back to ``~/.platformio/packages/<pkg>``.
        """
        try:
            # ProjectConfig is the source of truth for packages_dir.
            from platformio.project.config import ProjectConfig

            packages_dir = ProjectConfig.get_instance().get(
                "platformio", "packages_dir"
            )
        except Exception:  # pylint: disable=broad-except
            packages_dir = os.path.expanduser(
                os.path.join("~", ".platformio", "packages")
            )
        return os.path.join(packages_dir, FRAMEWORK_PKG_NAME)

    def _write_package_json(self, sdk_dir, source):
        """Write a PIO-compatible package.json into the SDK directory.

        The shape mirrors framework-espidf-4.60001.0/package.json (verified
        by extracting the actual tarball from registry.platformio.org —
        only 7 fields: name, version, title, description, keywords, homepage,
        license, repository). We add a ``source`` marker so we can tell apart
        local-override vs git-cloned installs during debugging.
        """
        version = self._derive_sdk_version(sdk_dir)
        manifest = {
            "name": FRAMEWORK_PKG_NAME,
            "version": version,
            "title": "Realtek Ameba RTOS SDK",
            "description": (
                "Realtek official ameba-rtos SDK (RTL8710 / RTL8720 / RTL8721 "
                "/ RTL8730 series). Ships with built-in CMake/Ninja build "
                "system; PlatformIO drives it via the upstream ameba.py CLI. "
                "Base SDK (Wi-Fi + BT) is cloned by default; the extended XDK "
                "submodules (audio, ui/lvgl, aivoice, tflite_micro, speechmind) "
                "are omitted. Pull the whole XDK up front with "
                "$AMEBA_SDK_EDITION=xdk, or add a single component on demand: "
                "`git submodule update --init --depth 1 <component>` inside the SDK."
            ),
            "keywords": [
                "framework",
                "rtl8710",
                "rtl8720",
                "rtl8721",
                "rtl8730",
                "realtek",
                "ameba",
                "wifi",
                "bluetooth",
            ],
            "homepage": "https://github.com/Ameba-AIoT/ameba-rtos",
            "license": "Apache-2.0",
            "repository": {
                "type": "git",
                "url": "https://github.com/Ameba-AIoT/ameba-rtos",
            },
            # Non-standard but useful for `pio pkg show` debugging:
            "_source": source,  # "git" or "local-override"
        }
        with open(os.path.join(sdk_dir, "package.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")

    def _derive_sdk_version(self, sdk_dir):
        """Build a semver-ish version: ``<platform-version>+sha.<8-hex>``.

        Mirrors PIO's own ``build_metadata`` style (see PackageManagerBase
        in platformio/package/manager/base.py:200) which tacks ``+sha.<rev>``
        onto the version string when it knows the VCS revision. This way:

        * ``pio pkg list -g`` shows a stable version that changes when the
          upstream SDK actually changes.
        * Reinstalling pinned platforms still reproduces.
        """
        try:
            sha = (
                subprocess.check_output(
                    ["git", "-C", sdk_dir, "rev-parse", "--short=8", "HEAD"],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
        except Exception:  # pylint: disable=broad-except
            sha = "unknown"
        # Use a fixed marker tracking the platform's compatibility lineage,
        # then suffix the SDK commit so updates show up in `pio pkg list`.
        return f"0.3.2-dev+sha.{sha}"

    def get_boards(self, id_=None):
        result = super().get_boards(id_)
        if not result:
            return result
        if id_:
            self._add_default_debug_tools(result)
        else:
            for key in result:
                self._add_default_debug_tools(result[key])
        return result

    def _add_default_debug_tools(self, board):
        debug = board.manifest.get("debug", {})
        if "tools" not in debug:
            debug["tools"] = {}

        # OpenOCD with Ameba-provided JSON config (one per SoC)
        if "openocd" not in debug["tools"]:
            debug["tools"]["openocd"] = {
                "server": {
                    "package": "tool-openocd",
                    "executable": "bin/openocd",
                    "arguments": [
                        "-f",
                        "interface/cmsis-dap.cfg",
                        "-f",
                        f"target/{board.get('build.soc', '').lower()}.cfg",
                    ],
                },
                "default": True,
            }

        # J-Link as alternative (Ameba SDK ships ameba.py jlink)
        if "jlink" not in debug["tools"]:
            debug["tools"]["jlink"] = {
                "server": {
                    "package": "tool-jlink",
                    "arguments": [
                        "-singlerun",
                        "-if", "SWD",
                        "-select", "USB",
                        "-port", "2331",
                        "-device", board.get("build.soc", "RTL8721Dx"),
                    ],
                    "executable": (
                        "JLinkGDBServerCL.exe" if IS_WINDOWS else "JLinkGDBServer"
                    ),
                },
            }

        board.manifest["debug"] = debug
        return board
