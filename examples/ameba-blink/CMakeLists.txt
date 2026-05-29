# PIO project root cmake entry for Ameba external project mode.
# This is a v0.3 platform-realtek-ameba requirement.
#
# The SDK (when invoked with -DEXTERN_DIR=<this dir>) reads this file as
# the project's top-level cmake. Keep it minimal -- the actual sources
# are registered in app_example/CMakeLists.txt.

ameba_add_subdirectory(app_example)
