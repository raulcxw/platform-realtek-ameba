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
        self._ensure_ameba_rtos_package()
        return super().configure_default_packages(variables, targets)

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

        sys.stderr.write(
            f"[realtek-ameba] First-time setup: cloning {sdk_url} "
            f"(branch={sdk_branch}, no submodules) to {pkg_dir}\n"
            f"[realtek-ameba] This is a one-time ~30 MB download, "
            f"typically 2-5 minutes.\n"
        )

        try:
            subprocess.check_call(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--single-branch",
                    "--branch",
                    sdk_branch,
                    "--no-recurse-submodules",
                    sdk_url,
                    pkg_dir,
                ]
            )
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
        """Create the SDK Python venv and pip-install tools/requirements.txt.

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

        Idempotent: if the venv already has json5 importable, skip.
        """
        venv_dir = os.path.join(sdk_dir, ".venv")
        venv_python = os.path.join(venv_dir, "bin", "python3")
        requirements = os.path.join(sdk_dir, "tools", "requirements.txt")

        if not os.path.isfile(requirements):
            sys.stderr.write(
                f"[realtek-ameba] no tools/requirements.txt at {requirements}, "
                f"skipping venv setup (SDK layout may have changed)\n"
            )
            return

        # Idempotency check: probe-import json5 in the existing venv.
        if os.path.isfile(venv_python):
            try:
                subprocess.check_call(
                    [venv_python, "-c", "import json5"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return  # venv already healthy
            except (subprocess.CalledProcessError, OSError):
                pass  # fall through, rebuild

        sys.stderr.write(
            f"[realtek-ameba] creating SDK venv at {venv_dir} and installing "
            f"requirements (one-time, ~30 seconds)\n"
        )

        # Wipe a stale/partial venv before recreating.
        if os.path.isdir(venv_dir):
            shutil.rmtree(venv_dir)

        try:
            subprocess.check_call(
                [sys.executable, "-m", "venv", venv_dir],
                stdout=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"[realtek-ameba] failed to create SDK venv at {venv_dir}: {exc}"
            ) from exc

        # Use a domestic pip mirror by default for China-locale users (where
        # pypi.org timeouts are common). Override with $PIP_INDEX_URL upstream
        # if you want pypi.org or a private mirror.
        pip_args = [
            os.path.join(venv_dir, "bin", "pip"),
            "install",
            "--quiet",
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
                "Submodules (audio, ui, aivoice, tflite_micro, speechmind) "
                "are NOT cloned by default — fetch them on demand with "
                "`git submodule update --init <component>` inside the SDK."
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
                        f"interface/cmsis-dap.cfg",
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
