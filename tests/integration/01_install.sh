#!/usr/bin/env bash
# tests/integration/01_install.sh
#
# T01: Verify `pio platform install` from local checkout succeeds and
#      triggers SDK clone + venv setup on first `pio run`.
#
# This is the "happy path" smoke for the install pipeline. Cold-cache
# CI run takes ~5 minutes (base SDK ~30MB clone + ~280MB toolchain);
# warm cache (after actions/cache hit) ~30 seconds. Honors
# $AMEBA_SDK_EDITION (default "sdk"); CI keeps it on the base SDK.
#
# Required env vars:
#   TEST_BOARD       — board id, e.g. pke8721daf-c13-f10
#   PLATFORM_PATH    — path to current platform checkout (default: $PWD)

set -euo pipefail

cd "$(dirname "$0")/../.."
PLATFORM_PATH="${PLATFORM_PATH:-$(pwd)}"
TEST_BOARD="${TEST_BOARD:-pke8721daf-c13-f10}"

PROJ=$(mktemp -d -t pio-install-XXXXXX)
LOG="$PROJ/install.log"

cleanup() {
    cd /
    rm -rf "$PROJ"
}
trap cleanup EXIT

echo "=== T01 setup ==="
echo "  PLATFORM_PATH = $PLATFORM_PATH"
echo "  TEST_BOARD    = $TEST_BOARD"
echo "  PROJ          = $PROJ"

# Reinstall platform from local checkout to validate the install path.
# Using `file://` lets us test the install logic without depending on a
# network round-trip to GitHub.
#
# The `-g` flag is critical: pio pkg uninstall/install without -g operates
# on the current project's packages and requires platformio.ini in cwd.
# Repo root has no platformio.ini → NotPlatformIOProjectError. With -g we
# install to ~/.platformio/platforms/ globally, which is where the test
# project below will resolve `platform = realtek-ameba` from.
echo
echo "=== T01 step 1: pio platform install (from local checkout) ==="
pio pkg uninstall -g -p realtek-ameba 2>&1 | tail -3 || true
pio pkg install -g -p "file://$PLATFORM_PATH" 2>&1 | tail -10

if [ ! -d ~/.platformio/platforms/realtek-ameba ]; then
    echo "❌ T01: ~/.platformio/platforms/realtek-ameba not created"
    exit 1
fi
echo "  ✓ platform dir present"

# Check key files copied
for f in platform.json platform.py builder/main.py; do
    if [ ! -f ~/.platformio/platforms/realtek-ameba/"$f" ]; then
        echo "❌ T01: $f missing in installed platform"
        exit 1
    fi
done
echo "  ✓ key files present"

echo
echo "=== T01 step 2: minimal project setup ==="
mkdir -p "$PROJ/src"
cat > "$PROJ/platformio.ini" <<EOF
[env:$TEST_BOARD]
platform = realtek-ameba
framework = ameba-rtos
board = $TEST_BOARD
EOF

# Empty src/ — relies on auto-skeleton to populate it on first build
echo "  ✓ project structure ready"

echo
echo "=== T01 step 3: first pio run (triggers SDK clone + venv + auto-skeleton) ==="
echo "  This is the slow step (cold cache: ~5 min base SDK, warm: ~30s)"
echo "  Log: $LOG"

cd "$PROJ"
# Stream to log AND tail; CI will see the running build, errors are in log.
if ! pio run -e "$TEST_BOARD" > "$LOG" 2>&1; then
    echo "❌ T01: pio run failed. Last 40 lines of log:"
    tail -40 "$LOG"
    exit 1
fi

echo "  ✓ pio run completed"

echo
echo "=== T01 step 4: validate SDK + venv + artifacts ==="

# SDK must be cloned
if [ ! -f ~/.platformio/packages/framework-ameba-rtos/ameba.py ]; then
    echo "❌ T01: SDK not cloned (no ameba.py in framework-ameba-rtos/)"
    exit 1
fi
echo "  ✓ SDK cloned"

# venv must be set up with stamp
STAMP=~/.platformio/packages/framework-ameba-rtos/.venv/.pio_requirements_sha256
if [ ! -f "$STAMP" ]; then
    echo "❌ T01: venv stamp missing — auto-resync didn't run"
    exit 1
fi
EXPECTED_HASH=$(sha256sum ~/.platformio/packages/framework-ameba-rtos/tools/requirements.txt | cut -d' ' -f1)
ACTUAL_HASH=$(cat "$STAMP")
if [ "$EXPECTED_HASH" != "$ACTUAL_HASH" ]; then
    echo "❌ T01: stamp hash mismatch"
    echo "  expected: $EXPECTED_HASH"
    echo "  actual:   $ACTUAL_HASH"
    exit 1
fi
echo "  ✓ venv stamp matches requirements.txt"

# Auto-skeleton: src/main.c, app_example/, top CMakeLists.txt
for path in src/main.c app_example/app_main.c app_example/CMakeLists.txt CMakeLists.txt; do
    if [ ! -f "$PROJ/$path" ]; then
        echo "❌ T01: auto-skeleton missing $path"
        exit 1
    fi
done
echo "  ✓ auto-skeleton populated"

# Build artifacts
for f in firmware.elf firmware.bin firmware_boot.bin; do
    if [ ! -f "$PROJ/.pio/build/$TEST_BOARD/$f" ]; then
        echo "❌ T01: $f not produced"
        exit 1
    fi
done
echo "  ✓ firmware artifacts present"

# compile_commands.json mirrored to project root (for VSCode)
if [ ! -f "$PROJ/compile_commands.json" ]; then
    echo "❌ T01: compile_commands.json not exported to project root"
    exit 1
fi
echo "  ✓ compile_commands.json mirrored"

# Sanity-check the user_main hint comment is in the auto-generated src/main.c
if ! grep -q "user_main()" "$PROJ/src/main.c"; then
    echo "❌ T01: src/main.c starter doesn't mention user_main"
    exit 1
fi
if ! grep -q "MUST RETURN" "$PROJ/src/main.c"; then
    echo "❌ T01: src/main.c starter missing 'MUST RETURN' user_main hint"
    exit 1
fi
echo "  ✓ src/main.c starter has user_main hint"

echo
echo "✅ T01 install: PASS ($TEST_BOARD)"
