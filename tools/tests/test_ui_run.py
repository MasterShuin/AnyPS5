"""Headless unit tests for :mod:`tools.ui_run`.

Only the pure helpers and the worker/callback plumbing are exercised — no
``Tk`` root is created, so these run without a display. The window itself is
verified statically (it compiles and imports); a real click-through needs a
graphical session.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ui_run  # noqa: E402


def make_file(path: Path, content=b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class DescribeInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ui_describe_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_game_folder(self):
        make_file(self.tmp / "game" / "eboot.bin")
        kind, _ = ui_run.describe_input(self.tmp / "game")
        self.assertEqual(kind, "folder")

    def test_folder_without_eboot(self):
        (self.tmp / "empty").mkdir()
        kind, message = ui_run.describe_input(self.tmp / "empty")
        self.assertEqual(kind, "folder_no_eboot")
        self.assertIn("eboot.bin", message)

    def test_container(self):
        for suffix in (".pkg", ".ffpfs", ".ffpfsc", ".exfat"):
            make_file(self.tmp / f"game{suffix}")
            kind, _ = ui_run.describe_input(self.tmp / f"game{suffix}")
            self.assertEqual(kind, "container", suffix)

    def test_standalone_elf(self):
        make_file(self.tmp / "app.elf")
        kind, _ = ui_run.describe_input(self.tmp / "app.elf")
        self.assertEqual(kind, "elf")

    def test_unsupported(self):
        make_file(self.tmp / "disk.iso")
        kind, message = ui_run.describe_input(self.tmp / "disk.iso")
        self.assertEqual(kind, "unsupported")
        self.assertIn("non supportato", message)

    def test_missing(self):
        kind, _ = ui_run.describe_input(self.tmp / "nope")
        self.assertEqual(kind, "missing")


class DefaultOutputDirTests(unittest.TestCase):
    def test_sibling_folder_not_inside_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "My Game (Demo)"
            game.mkdir()
            out = ui_run.default_output_dir(game)
            self.assertEqual(out.name, "AnyPS5_Output")
            self.assertEqual(out.parent, game.parent)
            self.assertNotIn(game.resolve(), out.resolve().parents)


class ResolveComponentsTests(unittest.TestCase):
    def test_resolves_both(self):
        with mock.patch.object(ui_run._bwg, "resolve_relinker", return_value=Path("rel.exe")), \
                mock.patch.object(ui_run._bwg, "resolve_libs", return_value=Path("libs")):
            relinker, libs, problems = ui_run.resolve_components()
        self.assertEqual(relinker, Path("rel.exe"))
        self.assertEqual(libs, Path("libs"))
        self.assertEqual(problems, [])

    def test_reports_missing(self):
        with mock.patch.object(ui_run._bwg, "resolve_relinker",
                               side_effect=ui_run._bwg.PipelineError(ui_run._bwg.Phase.INPUT_VALIDATED, "no relinker")), \
                mock.patch.object(ui_run._bwg, "resolve_libs", return_value=Path("libs")):
            relinker, libs, problems = ui_run.resolve_components()
        self.assertIsNone(relinker)
        self.assertTrue(problems)
        self.assertIn("relinker", problems[0])


class ClassifyOutcomeTests(unittest.TestCase):
    def _result(self, **kwargs):
        base = dict(error=None, ready_to_launch=False,
                    reached=ui_run._bwg.Phase.DEPS_INCOMPLETE.value,
                    output=Path("out"), log="")
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_exception_is_error(self):
        outcome = ui_run.classify_outcome(None, RuntimeError("boom"))
        self.assertEqual(outcome["kind"], "error")
        self.assertFalse(outcome["can_launch"])

    def test_result_error_is_error(self):
        outcome = ui_run.classify_outcome(self._result(error="relinker failed"), None)
        self.assertEqual(outcome["kind"], "error")
        self.assertFalse(outcome["can_launch"])

    def test_verified_is_ok_and_launchable(self):
        result = self._result(ready_to_launch=True, reached=ui_run._bwg.Phase.DEPS_VERIFIED.value)
        outcome = ui_run.classify_outcome(result, None)
        self.assertEqual(outcome["kind"], "ok")
        self.assertTrue(outcome["can_launch"])
        self.assertTrue(outcome["can_open"])

    def test_pe_only_is_warn_not_launchable(self):
        outcome = ui_run.classify_outcome(self._result(), None)
        self.assertEqual(outcome["kind"], "warn")
        self.assertFalse(outcome["can_launch"])
        self.assertTrue(outcome["can_open"])
        self.assertIn("non è stato ancora verificato", outcome["summary"])

    def test_phase_label_marks_ok_and_failure(self):
        self.assertTrue(ui_run.phase_label("prepared", True).startswith("✔"))
        self.assertIn("Preparazione", ui_run.phase_label("prepared", True))
        self.assertTrue(ui_run.phase_label("pe_generated", False).startswith("✖"))

    def test_build_report_lists_phases_audit_and_log(self):
        result = self._result(
            steps=[SimpleNamespace(phase="pe_generated", ok=True, detail="game.exe")],
            audit={"unique_by_class": {"implemented": 741, "stub": 26, "absent": 192}},
            log="$ relinker ...\nConversion complete",
        )
        outcome = ui_run.classify_outcome(result, None)
        report = ui_run.build_report(result, outcome)
        self.assertIn("Resoconto conversione", report)
        self.assertIn("pe_generated", report)
        self.assertIn("assenti: 192", report)
        self.assertIn("Conversion complete", report)

    def test_build_report_handles_a_failed_run(self):
        report = ui_run.build_report(None, ui_run.classify_outcome(None, RuntimeError("boom")))
        self.assertIn("Conversione non riuscita", report)
        self.assertIn("boom", report)


class FakeApp:
    """Minimal stand-in for the Tk app: records ``after`` and callback results.

    ``after`` accepts only positional arguments, exactly like ``Tk.after`` — so
    a regression to ``after(0, cb, exc=exc)`` raises ``TypeError`` here, which is
    the bug these tests guard against.
    """

    def __init__(self):
        self.after_calls = []
        self.done = []

    def after(self, ms, func, *args):
        self.after_calls.append((ms, func, args))

    def _convert_done(self, result=None, exc=None):
        self.done.append(("convert", result, exc))

    def _finish(self, out=None, exc=None):
        self.done.append(("prepare", out, exc))


class WorkerPlumbingTests(unittest.TestCase):
    """The workers must hand results to the UI thread with positional args."""

    def test_convert_worker_success_uses_positional_after(self):
        fake = FakeApp()
        fake._win_config = object()
        sentinel = object()
        with mock.patch.object(ui_run._bwg, "build", return_value=sentinel):
            ui_run.RunConfigApp._convert_worker(fake)
        self.assertEqual(len(fake.after_calls), 1)
        _ms, func, args = fake.after_calls[0]
        self.assertEqual(args, (sentinel,))
        func(*args)  # must not raise
        self.assertEqual(fake.done[-1], ("convert", sentinel, None))

    def test_convert_worker_exception_uses_positional_after(self):
        fake = FakeApp()
        fake._win_config = object()

        def boom(config, progress_callback=None):
            raise RuntimeError("kaboom")

        with mock.patch.object(ui_run._bwg, "build", boom):
            ui_run.RunConfigApp._convert_worker(fake)  # would raise if after(exc=...) regressed
        _ms, func, args = fake.after_calls[0]
        self.assertIsNone(args[0])
        self.assertIsInstance(args[1], RuntimeError)
        func(*args)
        self.assertEqual(fake.done[-1][0], "convert")
        self.assertIsInstance(fake.done[-1][2], RuntimeError)

    def test_prepare_worker_success_and_failure(self):
        fake = FakeApp()
        fake._source = Path(tempfile.mkdtemp(prefix="ui_prep_"))
        fake.adv_output_var = SimpleNamespace(get=lambda: str(fake._source / "out"))
        with mock.patch.object(ui_run, "_prepare_run", return_value=Path("prepared")):
            ui_run.RunConfigApp._worker(fake)
        func, args = fake.after_calls[-1][1], fake.after_calls[-1][2]
        func(*args)
        self.assertEqual(fake.done[-1], ("prepare", Path("prepared"), None))

        def boom(*a, **k):
            raise ValueError("bad input")

        with mock.patch.object(ui_run, "_prepare_run", boom):
            ui_run.RunConfigApp._worker(fake)
        func, args = fake.after_calls[-1][1], fake.after_calls[-1][2]
        func(*args)
        self.assertEqual(fake.done[-1][0], "prepare")
        self.assertIsInstance(fake.done[-1][2], ValueError)


if __name__ == "__main__":
    unittest.main()

