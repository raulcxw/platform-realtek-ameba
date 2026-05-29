# Copyright 2026 raul_chen
# SPDX-License-Identifier: Apache-2.0

"""ameba-rtos framework script.

Loaded by PlatformIO when ``framework = ameba-rtos`` is set in
``platformio.ini``. The framework name matches the upstream Realtek SDK
repository (github.com/Ameba-AIoT/ameba-rtos).

For the v0.1 cut we simply hand off to ``builder/main.py``; all SDK
invocation lives there. This stub exists so PlatformIO's framework
discovery succeeds and so we have a place to add framework-specific options
(menuconfig passthrough, defconfig selection, etc.) later.
"""

from SCons.Script import DefaultEnvironment

env = DefaultEnvironment()

# Tag the build env so size/asm reporters know the framework
env.Replace(
    PIOFRAMEWORK_AMEBA_RTOS=True,
)

# Future hooks (deliberately no-op for v0.1):
#   - parse `board_build.ameba-rtos.defconfig` and pass via `ameba.py menuconfig -s`
#   - parse `board_build.ameba-rtos.app` and forward as `-a` to ameba.py build
#   - expose `pio run -t menuconfig` -> `ameba.py menuconfig`
