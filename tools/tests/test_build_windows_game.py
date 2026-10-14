"""Unit tests for :mod:`tools.build_windows_game`.

The external processes (the relinker and the import auditor) are mocked, so
these tests exercise the orchestration, argument construction, path-with-spaces
handling, staging, cleanup and the state machine without a real PS5 title or the
compiled relinker. Integration with the real relinker is covered by the
relinker's own tests; a synthetic end-to-end run is demonstrated separately and
is explicitly NOT proof that a commercial title is playable.

Written as ``unittest`` like the rest of ``tools/tests`` so it runs both as a
standalone script (``python tools/tests/test_build_windows_game.py``) and under
pytest, and can be registered with CTest.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_windows_game as bwg  # noqa: E402


class FakeProcess:
    """Minimal stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, returncode=0, stdout="", stderr="", args=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.args = args or []


def make_fake_libs(directory: Path) -> Path:
    """Create a fake native library directory with the essential ``.prx`` files."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("libc.prx", "libkernel.prx", "libScePad.prx"):
        (directory / name).write_bytes(b"\x7fELF" + b"\x00" * 60)
    for dll in bwg.MINGW_DEPENDENCY_DLLS:
        (directory / dll).write_bytes(b"MZ" + b"\x00" * 10)
    return directory


class ScriptedRunner:
    """Dispatches on the command: relinker vs. import audit.

    The relinker call writes a fake ``MZ`` executable and a registry beside the
    output; the audit call writes ``audit.json`` and returns a configurable code.
    """

    def __init__(self, *, relinker_rc=0, audit_rc=0, audit_summary=None):
        self.relinker_rc = relinker_rc
        self.audit_rc = audit_rc
        self.audit_summary = audit_summary or {"unique_by_class": {"absent": 0}}
        self.relinker_cmds = []
        self.audit_cmds = []

    def __call__(self, cmd, timeout=None):
        args = [str(a) for a in cmd]
        if any("import_audit.py" in a for a in args):
            self.audit_cmds.append(args)
            json_index = args.index("--json")
            Path(args[json_index + 1]).write_text(json.dumps(self.audit_summary))
            return FakeProcess(self.audit_rc, "audit done", args=args)
        self.relinker_cmds.append(args)
        if self.relinker_rc != 0:
            return FakeProcess(self.relinker_rc, "", "relinker failed", args=args)
        output = Path(args[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"MZ" + b"\x00" * 128)
        (output.parent / (output.stem + ".registry.json")).write_text("[]")
        return FakeProcess(0, "converted", args=args)


class BuildWindowsGameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bwg_test_"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.libs = make_fake_libs(self.tmp / "libs")
        self.relinker = self.tmp / "relinker.exe"
        self.relinker.write_bytes(b"MZ")
        prepare = mock.patch.object(bwg._prepare, "prepare_run", self._fake_prepare)
        prepare.start()
        self.addCleanup(prepare.stop)
        detect = mock.patch.object(bwg.ps5_container, "detect_container", lambda path: None)
        detect.start()
        self.addCleanup(detect.stop)

    def _fake_prepare(self, staged_from, prepared, *args, **kwargs):
        prepared = Path(prepared)
        (prepared / "sce_sys").mkdir(parents=True, exist_ok=True)
        (prepared / "sce_sys" / "param.json").write_text("{}")
        (prepared / "eboot.bin").write_bytes(b"\x7fELF")
        return prepared

    def game_folder(self, name="game", with_modules=False) -> Path:
        game = self.tmp / name
        (game / "sce_sys").mkdir(parents=True, exist_ok=True)
        (game / "sce_sys" / "param.json").write_text("{}")
        (game / "eboot.bin").write_bytes(b"\x7fELF")
        if with_modules:
            (game / "sce_module").mkdir()
            (game / "sce_module" / "lib.prx").write_bytes(b"\x7fELF")
        return game

    def config_for(self, source, output) -> bwg.PipelineConfig:
        return bwg.PipelineConfig(source=source, output=output, relinker=self.relinker, libs=self.libs)

    def test_paths_with_spaces(self):
        source = self.game_folder("My Game (Demo)")
        output = self.tmp / "out dir"
        runner = ScriptedRunner(audit_rc=0)
        result = bwg.build(self.config_for(source, output), runner=runner)
        self.assertIsNone(result.error)
        self.assertEqual(result.reached, bwg.Phase.DEPS_VERIFIED.value)
        self.assertTrue(result.ready_to_launch)
        self.assertTrue((output / "game.exe").is_file())
        self.assertTrue((output / "libs" / "libc.prx").is_file())
        self.assertTrue((output / "app0" / "sce_sys" / "param.json").is_file())
        relinker_cmd = runner.relinker_cmds[0]
        self.assertTrue(any("out dir" in a for a in relinker_cmd))
        self.assertEqual(str(output / "game.exe"), relinker_cmd[-1])

    def test_game_folder_with_modules_does_not_skip(self):
        def prepare_with_modules(staged_from, prepared, *a, **k):
            prepared = Path(prepared)
            (prepared / "sce_sys").mkdir(parents=True, exist_ok=True)
            (prepared / "eboot.bin").write_bytes(b"\x7fELF")
            (prepared / "sce_module").mkdir()
            (prepared / "sce_module" / "lib.prx").write_bytes(b"\x7fELF")
            return prepared

        with mock.patch.object(bwg._prepare, "prepare_run", prepare_with_modules):
            source = self.game_folder("game", with_modules=True)
            runner = ScriptedRunner()
            bwg.build(self.config_for(source, self.tmp / "out"), runner=runner)
        self.assertNotIn("--skip-sce-module", runner.relinker_cmds[0])

    def test_elf_input_skips_modules(self):
        elf = self.tmp / "standalone.elf"
        elf.write_bytes(b"\x7fELF")
        runner = ScriptedRunner()
        bwg.build(self.config_for(elf, self.tmp / "out"), runner=runner)
        self.assertIn("--skip-sce-module", runner.relinker_cmds[0])

    def test_bundled_native_duplicate_is_dropped(self):
        """A bundled module AnyPS5 already ships in libs/ must not be converted.

        Converting the bundled copy of a system library (libc.prx) aborts the
        relink on a raw syscall the relinker refuses; the native build is what
        the rpath resolves, so the duplicate is dropped from staging.
        """
        def prepare_with_native_dupe(staged_from, prepared, *a, **k):
            prepared = Path(prepared)
            (prepared / "sce_sys").mkdir(parents=True, exist_ok=True)
            (prepared / "eboot.bin").write_bytes(b"\x7fELF")
            (prepared / "sce_module").mkdir()
            # libc.prx is provided natively (make_fake_libs); libOwn.prx is not.
            (prepared / "sce_module" / "libc.prx").write_bytes(b"\x7fELF")
            (prepared / "sce_module" / "libOwn.prx").write_bytes(b"\x7fELF")
            return prepared

        with mock.patch.object(bwg._prepare, "prepare_run", prepare_with_native_dupe):
            source = self.game_folder("game", with_modules=True)
            runner = ScriptedRunner()
            result = bwg.build(self.config_for(source, self.tmp / "out"), runner=runner)
        self.assertIsNone(result.error)
        # libOwn.prx survives, so modules are still converted (no skip).
        self.assertNotIn("--skip-sce-module", runner.relinker_cmds[0])
        self.assertTrue(any("skipped bundled libc.prx" in line for line in result.log.splitlines()))

    def test_only_native_duplicates_skips_module_conversion(self):
        """When every bundled module is a native duplicate, nothing is left to
        convert, so the relinker is told to skip sce-module processing."""
        def prepare_only_native(staged_from, prepared, *a, **k):
            prepared = Path(prepared)
            (prepared / "sce_sys").mkdir(parents=True, exist_ok=True)
            (prepared / "eboot.bin").write_bytes(b"\x7fELF")
            (prepared / "sce_module").mkdir()
            (prepared / "sce_module" / "libc.prx").write_bytes(b"\x7fELF")
            return prepared

        with mock.patch.object(bwg._prepare, "prepare_run", prepare_only_native):
            source = self.game_folder("game", with_modules=True)
            runner = ScriptedRunner()
            result = bwg.build(self.config_for(source, self.tmp / "out"), runner=runner)
        self.assertIsNone(result.error)
        self.assertIn("--skip-sce-module", runner.relinker_cmds[0])

    def test_drop_native_duplicates_never_touches_the_source(self):
        """Only the temporary staging tree is edited; the user's game folder
        keeps its bundled system library untouched."""
        staged = self.tmp / "prepared"
        (staged / "sce_module").mkdir(parents=True)
        (staged / "sce_module" / "libc.prx").write_bytes(b"\x7fELF")
        source_module = self.tmp / "source_libc.prx"
        source_module.write_bytes(b"\x7fELF")
        bwg._drop_native_duplicates(staged, self.libs, [])
        self.assertFalse((staged / "sce_module" / "libc.prx").exists())
        self.assertTrue(source_module.is_file())

    def test_container_input_is_extracted(self):
        extracted = {}

        def fake_extract(src, dest, *a, **k):
            extracted["src"], extracted["dest"] = Path(src), Path(dest)
            (Path(dest) / "eboot.bin").write_bytes(b"\x7fELF")
            return Path(dest)

        container = self.tmp / "title.ffpfsc"
        container.write_bytes(b"PKG")
        with mock.patch.object(bwg.ps5_container, "detect_container", lambda p: ".ffpfsc"), \
                mock.patch.object(bwg.ps5_container, "extract_container", fake_extract):
            result = bwg.build(self.config_for(container, self.tmp / "out"), runner=ScriptedRunner())
        self.assertIsNone(result.error)
        self.assertEqual(extracted.get("src"), container)

    def test_relinker_missing_blocks_input(self):
        source = self.game_folder("game")
        output = self.tmp / "out"
        with mock.patch.object(bwg, "DEFAULT_RELINKER", self.tmp / "nope.exe"), \
                mock.patch.dict(os.environ):
            os.environ.pop("ANYPS5_RELINKER", None)
            result = bwg.build(bwg.PipelineConfig(source=source, output=output, libs=self.libs),
                               runner=ScriptedRunner())
        self.assertIsNotNone(result.error)
        self.assertEqual(result.reached, bwg.Phase.INPUT_VALIDATED.value)
        self.assertFalse((output / "game.exe").exists())

    def test_relinker_nonzero_exit_stops_pipeline(self):
        source = self.game_folder("game")
        output = self.tmp / "out"
        result = bwg.build(self.config_for(source, output), runner=ScriptedRunner(relinker_rc=2))
        self.assertEqual(result.reached, bwg.Phase.PE_GENERATED.value)
        self.assertIn("relinker failed", result.error)
        self.assertFalse((output / "game.exe").exists())

    def test_audit_absent_imports_not_ready_but_pe_exists(self):
        source = self.game_folder("game")
        output = self.tmp / "out"
        result = bwg.build(self.config_for(source, output), runner=ScriptedRunner(audit_rc=1))
        self.assertEqual(result.reached, bwg.Phase.DEPS_INCOMPLETE.value)
        self.assertFalse(result.ready_to_launch)
        self.assertIsNone(result.error)
        self.assertTrue((output / "game.exe").is_file())

    def test_audit_verified_ready(self):
        source = self.game_folder("game")
        result = bwg.build(self.config_for(source, self.tmp / "out"), runner=ScriptedRunner(audit_rc=0))
        self.assertEqual(result.reached, bwg.Phase.DEPS_VERIFIED.value)
        self.assertTrue(result.ready_to_launch)

    def test_audit_read_error_is_reported(self):
        source = self.game_folder("game")
        result = bwg.build(self.config_for(source, self.tmp / "out"), runner=ScriptedRunner(audit_rc=2))
        self.assertEqual(result.reached, bwg.Phase.AUDIT_DONE.value)
        self.assertIn("import audit", result.error)

    def test_stale_output_is_replaced(self):
        source = self.game_folder("game")
        output = self.tmp / "out"
        output.mkdir()
        (output / "game.exe").write_bytes(b"STALE")
        (output / "game.registry.json").write_text("[]")
        (output / "libs").mkdir()
        (output / "libs" / "stale.prx").write_bytes(b"STALE")
        (output / "audit.json").write_text("{}")
        bwg.build(self.config_for(source, output), runner=ScriptedRunner())
        self.assertEqual((output / "game.exe").read_bytes()[:2], b"MZ")
        self.assertFalse((output / "libs" / "stale.prx").exists())

    def test_launch_requires_libs(self):
        output = self.tmp / "out"
        output.mkdir()
        (output / "game.exe").write_bytes(b"MZ")
        with self.assertRaises(bwg.PipelineError):
            bwg.launch(bwg.PipelineConfig(source=self.game_folder(), output=output, libs=self.libs),
                       popen=lambda *a, **k: None)

    def test_launch_starts_process_when_ready(self):
        output = self.tmp / "out"
        (output / "libs").mkdir(parents=True)
        (output / "game.exe").write_bytes(b"MZ")
        started = {}

        def fake_popen(cmd, cwd=None):
            started["cmd"], started["cwd"] = cmd, cwd
            return "PROC"

        handle = bwg.launch(bwg.PipelineConfig(source=self.game_folder(), output=output, libs=self.libs),
                            popen=fake_popen)
        self.assertEqual(handle, "PROC")
        self.assertEqual(started["cwd"], str(output))
        self.assertIn(str(output / "game.exe"), started["cmd"][0])

    def test_temp_workdir_cleanup(self):
        work_root = self.tmp / "work"
        work_root.mkdir()
        counter = {"n": 0}

        def fake_mkdtemp(prefix=None):
            counter["n"] += 1
            path = work_root / f"{prefix}{counter['n']}"
            path.mkdir()
            return str(path)

        source = self.game_folder("game")
        with mock.patch.object(bwg.tempfile, "mkdtemp", fake_mkdtemp):
            bwg.build(self.config_for(source, self.tmp / "out1"), runner=ScriptedRunner())
            self.assertFalse(any(work_root.iterdir()))
            cfg = self.config_for(source, self.tmp / "out2")
            cfg.keep_work = True
            bwg.build(cfg, runner=ScriptedRunner())
            self.assertTrue(any(work_root.iterdir()))


if __name__ == "__main__":
    unittest.main()
