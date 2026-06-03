"""tests/unit/test_erase_fail_detection.py

Verifies that `_run_ameba_flash()` correctly catches the SDK's
"thread reports FAIL but main() exits 0" silent-failure pattern.

Background: ameba.py flash spawns its real work in a thread. When that
thread reports `Finished FAIL: ErrType.XXX` to stdout, the main process
returns exit code 0 anyway (the failure is never propagated to sys.exit).

Without our `_run_ameba_flash` helper grepping stdout, PIO would happily
report `[SUCCESS]` and the user's next `pio device monitor` would show
the firmware unchanged — wasting ~30 minutes of confused debugging.

This test prevents that regression by mocking subprocess.run to return
the failure pattern and asserting our helper calls env.Exit(1).
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add repo root + builder/ to import path so we can pull in main.py functions
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, 'builder'))


@pytest.fixture
def isolated_main(monkeypatch, tmp_path):
    """Import builder/main.py with all PIO-coupled globals stubbed.

    builder/main.py expects to be loaded as a SCons script with `env`
    and `board` injected — we don't have that in unit tests, so we
    monkey-patch import-time globals to no-op stubs and manually expose
    just the function under test.
    """
    # Stub SCons.Script (imported at module top)
    fake_scons = MagicMock()
    monkeypatch.setitem(sys.modules, 'SCons', MagicMock())
    monkeypatch.setitem(sys.modules, 'SCons.Script', fake_scons)

    # Stub PIO modules
    monkeypatch.setitem(sys.modules, 'platformio', MagicMock())
    monkeypatch.setitem(sys.modules, 'platformio.public', MagicMock())
    monkeypatch.setitem(sys.modules, 'platformio.project', MagicMock())
    monkeypatch.setitem(sys.modules, 'platformio.project.config', MagicMock())

    # builder/main.py needs an env, board, and platform from
    # DefaultEnvironment(). We hand-roll a minimal one.
    fake_env = MagicMock()
    fake_env.subst = MagicMock(side_effect=lambda s: s.replace('$PROJECT_DIR', str(tmp_path)))
    fake_env.GetProjectOption = MagicMock(return_value=None)
    fake_env.PioPlatform = MagicMock()
    fake_env.BoardConfig = MagicMock()

    # board.get('build.soc') → minimal 8721 stub
    fake_board = MagicMock()
    fake_board.get = MagicMock(side_effect=lambda key, default=None: {
        'build.soc': 'RTL8721Dx',
        'build.cores': [{'sdk_project': 'km4', 'isa': 'arm-cortex-m'}],
    }.get(key, default))

    fake_env.BoardConfig.return_value = fake_board

    # The DefaultEnvironment() at module-top returns this
    fake_scons.DefaultEnvironment = MagicMock(return_value=fake_env)
    # Also inject board/platform symbols some places use
    fake_scons.board = fake_board

    # Provide a writable PROJECT_DIR
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "platformio.ini").write_text(
        "[env:test]\nplatform=realtek-ameba\nboard=pke8721daf-c13-f10\n"
    )

    # Now actually import — but only the function we need.
    # builder/main.py runs lots at module-top (env.Replace, AddCustomTarget...),
    # which would explode without a real SCons env. So we extract the
    # function definition via exec() of just the source we care about.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ameba_main", os.path.join(REPO_ROOT, "builder", "main.py")
    )
    # Module-level execution will fail (SCons mocks aren't deep enough),
    # but we just want the function objects.
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        # Module exec fails because of SCons mock incompleteness — fine.
        # We extract function bodies via direct source eval below.
        mod = None

    if mod and hasattr(mod, '_run_ameba_flash'):
        return mod._run_ameba_flash, fake_env

    # Fallback: parse source and exec _run_ameba_flash in isolation.
    with open(os.path.join(REPO_ROOT, "builder", "main.py")) as fh:
        src = fh.read()

    # Crude function extraction by AST parse to avoid relying on text patterns
    import ast, textwrap
    tree = ast.parse(src)
    target_fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == '_run_ameba_flash':
            target_fn = node
            break
    assert target_fn is not None, "_run_ameba_flash function not found in builder/main.py"

    fn_src = textwrap.dedent(ast.get_source_segment(src, target_fn))
    namespace = {
        'os': os,
        'sys': sys,
        'env': fake_env,
        'PROJECT_DIR': str(tmp_path),
    }
    exec(fn_src, namespace)
    return namespace['_run_ameba_flash'], fake_env


def test_finished_fail_triggers_exit(isolated_main):
    """Mock subprocess.run to return 'Finished FAIL' with exit 0.

    Expected: _run_ameba_flash detects the failure and calls env.Exit(1).
    Without this our PIO output would say [SUCCESS] for a flash that
    silently failed (the 2026-06-03 erase regression).
    """
    run_ameba_flash, fake_env = isolated_main

    # Make env.Exit() observable
    exit_calls = []
    fake_env.Exit = lambda rc: exit_calls.append(rc)

    fake_stdout = (
        "[2026-06-03 14:15:32] AmebaFlash Version: 1.1.6.1\n"
        "Check supported flash size...\n"
        "[E] Fail to read eFuse: ErrType.SYS_PROTO\n"
        "[E] Finished FAIL: ErrType.SYS_PROTO\n"
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_stdout)
        run_ameba_flash(
            cmd=["python3", "ameba.py", "flash", "--chip-erase"],
            sdk_env={},
            label="erase",
        )

    assert exit_calls == [1], (
        f"expected env.Exit(1) on Finished FAIL; got Exit({exit_calls})"
    )


def test_finished_pass_does_not_exit(isolated_main):
    """Sanity: clean PASS path must NOT trigger env.Exit."""
    run_ameba_flash, fake_env = isolated_main

    exit_calls = []
    fake_env.Exit = lambda rc: exit_calls.append(rc)

    fake_stdout = (
        "[2026-06-03 14:17:34] AmebaFlash Version: 1.1.6.1\n"
        "Chip erase end\n"
        "boot.bin download done: 28KB\n"
        "app.bin download done: 782KB\n"
        "All images download done\n"
        "Finished PASS\n"
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_stdout)
        run_ameba_flash(
            cmd=["python3", "ameba.py", "flash"],
            sdk_env={},
            label="upload",
        )

    assert exit_calls == [], (
        f"expected no env.Exit on Finished PASS; got Exit({exit_calls})"
    )


def test_nonzero_returncode_triggers_exit(isolated_main):
    """Honest failure path: subprocess returncode != 0 → env.Exit(returncode)."""
    run_ameba_flash, fake_env = isolated_main

    exit_calls = []
    fake_env.Exit = lambda rc: exit_calls.append(rc)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=2, stdout="argparse error\n")
        run_ameba_flash(
            cmd=["python3", "ameba.py", "flash", "--unknown-flag"],
            sdk_env={},
            label="upload",
        )

    assert exit_calls == [2], f"expected env.Exit(2); got Exit({exit_calls})"


def test_silent_no_pass_marker_triggers_exit(isolated_main):
    """Edge case: subprocess produces output but no 'Finished PASS' line.

    We treat this as failure — could be ameba.py crashed mid-stream.
    """
    run_ameba_flash, fake_env = isolated_main

    exit_calls = []
    fake_env.Exit = lambda rc: exit_calls.append(rc)

    fake_stdout = "Some random output\nbut no PASS or FAIL marker\n"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_stdout)
        run_ameba_flash(
            cmd=["python3", "ameba.py", "flash"],
            sdk_env={},
            label="upload",
        )

    assert exit_calls == [1], (
        f"expected env.Exit(1) when no PASS marker; got Exit({exit_calls})"
    )
