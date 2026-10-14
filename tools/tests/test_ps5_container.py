"""Unit tests for :mod:`tools.ps5_container` — converter command building and
the Windows ``WinError 193`` / missing-file error handling."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ps5_container  # noqa: E402


class BuildConverterCommandTests(unittest.TestCase):
    def test_python_script_uses_interpreter(self):
        cmd = ps5_container.build_converter_command("backend/cli.py", "a b.pkg", "out dir")
        self.assertEqual(cmd[0], sys.executable)
        self.assertTrue(cmd[1].endswith("cli.py"))
        self.assertEqual(cmd[2:5], ["a b.pkg", "out dir", "--unpack"])

    def test_native_executable_runs_directly(self):
        cmd = ps5_container.build_converter_command("tool.exe", "game.pkg", "out")
        self.assertEqual(cmd, ["tool.exe", "game.pkg", "out", "--unpack"])

    def test_spaces_and_parentheses_preserved(self):
        tool = Path("C:/Program Files (x86)/tool.exe")
        src = Path("C:/Games/My Game (Demo).pkg")
        dest = Path("C:/out dir")
        cmd = ps5_container.build_converter_command(tool, src, dest)
        self.assertEqual(cmd[0], str(tool))
        self.assertIn(str(src), cmd)
        self.assertIn(str(dest), cmd)


class ExtractContainerErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ps5c_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.container = self.tmp / "game.pkg"
        self.container.write_bytes(b"PKG")
        self.dest = self.tmp / "out"

    def _extract(self, run_side_effect):
        with mock.patch.object(ps5_container.subprocess, "run", side_effect=run_side_effect):
            return ps5_container.extract_container(self.container, self.dest, converter="backend/cli.py")

    def test_success_returns_dest_and_uses_interpreter(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = self._extract(fake_run)
        self.assertEqual(result, self.dest.resolve())
        self.assertEqual(seen["cmd"][0], sys.executable)  # .py launched via the interpreter

    def test_winerror_193_is_reported_as_format_problem(self):
        def raise_193(cmd, **kwargs):
            raise OSError(193, "%1 is not a valid Win32 application")

        with self.assertRaises(ps5_container.ContainerError) as ctx:
            self._extract(raise_193)
        self.assertIn("interprete", str(ctx.exception))

    def test_missing_converter_is_reported(self):
        def raise_missing(cmd, **kwargs):
            raise FileNotFoundError("no such file")

        with self.assertRaises(ps5_container.ContainerError) as ctx:
            self._extract(raise_missing)
        self.assertIn("could not be started", str(ctx.exception))

    def test_nonzero_exit_is_reported(self):
        def fail(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "boom")

        with self.assertRaises(ps5_container.ContainerError) as ctx:
            self._extract(fail)
        self.assertIn("failed", str(ctx.exception))

    def test_overwrite_flag_is_passed_to_pre_created_dest(self):
        """The destination is always pre-created (mkdtemp/mkdir), so the converter
        must be told to overwrite or it aborts with "Output folder already exists"."""
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        self._extract(fake_run)
        self.assertIn("--unpack", seen["cmd"])
        self.assertIn("--overwrite", seen["cmd"])
        self.assertEqual(seen["cmd"][-1], "--overwrite")  # stays after --unpack

    def test_stdout_error_reason_is_surfaced(self):
        """The backend prints [ERROR] lines on stdout, not stderr; a failure must
        surface that reason instead of a bare exit code."""
        stdout = (
            "[INFO] Extracting...\n"
            "[ERROR] Output folder already exists: C:\\out\n"
            "[ERROR] Use --overwrite to replace existing extracted files.\n"
        )

        def fail(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout, "")

        with self.assertRaises(ps5_container.ContainerError) as ctx:
            self._extract(fail)
        text = str(ctx.exception)
        self.assertIn("Output folder already exists", text)
        self.assertIn("--overwrite", text)

    def test_child_output_is_decoded_as_utf8(self):
        """Non-ASCII paths/messages must not be decoded with the locale code page."""
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["kwargs"] = kwargs
            return subprocess.CompletedProcess(cmd, 0, "", "")

        self._extract(fake_run)
        self.assertEqual(seen["kwargs"].get("encoding"), "utf-8")
        self.assertEqual(seen["kwargs"].get("errors"), "replace")
        self.assertEqual(seen["kwargs"]["env"].get("PYTHONUTF8"), "1")

    def test_unsupported_suffix(self):
        other = self.tmp / "game.iso"
        other.write_bytes(b"x")
        with self.assertRaises(ps5_container.ContainerError):
            ps5_container.extract_container(other, self.dest, converter="backend/cli.py")


if __name__ == "__main__":
    unittest.main()
