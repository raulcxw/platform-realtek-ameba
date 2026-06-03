#!/usr/bin/env bash
# tests/lint.sh — Layer 1 static checks (~10s)
#
# Catches the cheap class of regressions before integration tests even start:
#   - Python syntax errors in platform.py / builder/main.py / framework scripts
#   - Malformed JSON in platform.json / boards/*.json
#   - Board manifest missing required fields (build.soc, build.mcu, etc.)
#   - Optional: ruff for unused imports / undefined names
#
# Runs on every CI push. Should never take more than a few seconds.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== lint: Python syntax ==="
for f in platform.py builder/main.py builder/frameworks/*.py; do
    python3 -m py_compile "$f"
    echo "  ✓ $f"
done

echo
echo "=== lint: platform.json ==="
python3 -c "import json; json.load(open('platform.json'))"
echo "  ✓ platform.json valid"

echo
echo "=== lint: boards/*.json ==="
shopt -s nullglob
for f in boards/*.json; do
    python3 -c "import json; json.load(open('$f'))"
    echo "  ✓ $f"
done

echo
echo "=== lint: required board fields ==="
python3 - <<'PYEOF'
import json, sys, glob

# (path-dotted-key, error-msg-suffix)
required = [
    ('build.soc',           'must declare which Realtek SoC to target'),
    ('build.mcu',           'must declare MCU family for IDE auto-config'),
    ('build.cores',         'must declare core list for multi-core size report'),
    ('frameworks',          'must declare supported framework(s)'),
    ('name',                'human-readable board name'),
    ('upload.protocol',     'flash protocol (e.g. ameba)'),
    ('upload.maximum_size', 'flash region size for upload size check'),
    ('monitor.speed',       'serial monitor baudrate (LogUART convention)'),
]

fail = False
for f in sorted(glob.glob('boards/*.json')):
    d = json.load(open(f))
    for path, msg in required:
        cur = d
        for k in path.split('.'):
            cur = cur.get(k) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur is None:
            print(f"  ❌ {f}: missing '{path}' — {msg}")
            fail = True
        else:
            pass  # quiet pass; would be too noisy to print every field
    print(f"  ✓ {f}")

sys.exit(1 if fail else 0)
PYEOF

echo
echo "=== lint: optional ruff (skip if not installed) ==="
# Try system PATH first, then SDK venv (where dev installs land), then skip.
RUFF=""
if command -v ruff >/dev/null 2>&1; then
    RUFF=$(command -v ruff)
elif [ -x ~/.platformio/packages/framework-ameba-rtos/.venv/bin/ruff ]; then
    RUFF=~/.platformio/packages/framework-ameba-rtos/.venv/bin/ruff
fi

if [ -n "$RUFF" ]; then
    # E, F: pycodestyle errors + pyflakes (unused imports, undefined names)
    # ignore E501 line-too-long (we have long error messages by design)
    "$RUFF" check --select E,F --ignore E501 platform.py builder/
    echo "  ✓ ruff passed ($RUFF)"
else
    echo "  - ruff not installed; skip"
fi

echo
echo "✅ lint passed"
