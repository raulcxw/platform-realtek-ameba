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

"""platform-amebartos: PlatformIO entry for the Realtek Ameba RTOS SDK.

Strategy (mirrors LibreTiny's "delegate to upstream" pattern, NOT espidf's
"reimplement CMake build"):

1.  Treat ``ameba-rtos`` as a black-box SDK driven by ``ameba.py``.
2.  Toolchain (asdk-12.3.1 / asdk-10.3.1, prebuilts-linux-1.0.3) is
    downloaded by the upstream SDK on first ``ameba.py build``; we just
    point ``RTK_TOOLCHAIN_DIR`` at PlatformIO's package cache so the
    artifacts are reused across projects.
3.  ``builder/main.py`` shells out to ``ameba.py build`` for the selected
    SoC. We avoid touching the upstream CMakeLists -- they bind tightly to
    SDK-internal env vars and version-pin a specific cmake.

The result: roughly 200 LoC of glue code instead of the ~2,300 LoC of
``platform-espressif32/builder/frameworks/espidf.py``.
"""

import os
import sys
from platformio.public import PlatformBase


IS_WINDOWS = sys.platform.startswith("win")


class AmebartosPlatform(PlatformBase):
    """PlatformIO platform for Realtek Ameba RTOS."""

    def configure_default_packages(self, variables, targets):
        # ARM toolchain (asdk-12.3.1 / asdk-10.3.1) is fetched on-demand by
        # the upstream `ameba.py build` into RTK_TOOLCHAIN_DIR. We do not
        # mirror those binaries through the PlatformIO registry -- both for
        # licensing reasons and because ameba.py already version-pins them
        # per SoC. So this hook is currently a no-op; we keep it as a
        # placeholder for future per-SoC package selection.
        return super().configure_default_packages(variables, targets)

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
                        "-device", board.get("build.soc", "RTL8721F"),
                    ],
                    "executable": (
                        "JLinkGDBServerCL.exe" if IS_WINDOWS else "JLinkGDBServer"
                    ),
                },
            }

        board.manifest["debug"] = debug
        return board
