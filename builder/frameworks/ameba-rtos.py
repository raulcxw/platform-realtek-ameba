# Copyright 2026 raul_chen
# SPDX-License-Identifier: Apache-2.0

"""ameba-rtos framework script.

Loaded by PlatformIO when ``framework = ameba-rtos`` is set in
``platformio.ini``. The framework name matches the upstream Realtek SDK
repository (github.com/Ameba-AIoT/ameba-rtos).

This acts as a stub so PlatformIO's framework discovery succeeds. 
Actual SDK invocation is deferred to `builder/main.py`.
"""

from SCons.Script import DefaultEnvironment

env = DefaultEnvironment()

# Tag the build env so size/asm reporters know the framework
env.Replace(
    PIOFRAMEWORK_AMEBA_RTOS=True,
)

# Future hooks:
#   - parse `board_build.ameba-rtos.defconfig` and pass via `ameba.py menuconfig -s`
#   - parse `board_build.ameba-rtos.app` and forward as `-a` to ameba.py build
#   - expose `pio run -t menuconfig` -> `ameba.py menuconfig`
