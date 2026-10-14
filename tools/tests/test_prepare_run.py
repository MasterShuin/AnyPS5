import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_run  # noqa: E402


class PrepareRunTests(unittest.TestCase):
    """End-to-end behaviour of :func:`prepare_run.prepare_run`.

    The container-conversion step is intentionally exercised by monkeypatching
    :func:`prepare_run.extract_container`, because there is no converter
    executable available in the build tree. The happy paths (game folder,
    standalone ELF) are tested directly.
    """

    SUFFIXES = (".pkg", ".ffpfs", ".ffpfsc", ".exfat")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="prepare_run_test_"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def game_folder(self, modules=("sce_module",)) -> Path:
        root = self.tmp / "game"
        root.mkdir()
        (root / "eboot.bin").write_bytes(b"\x7fELF\x02\x01\x01\x00")
        (root / "sce_sys").mkdir()
        (root / "sce_sys" / "param.json").write_text(
            '{"title": "Test Game", "version": "1.0"}', encoding="utf-8"
        )
        for name in modules:
            module_dir = root / name
            module_dir.mkdir()
            (module_dir / "libmonkey.prx").write_bytes(b"prx")
        (root / "sce_sys" / "app_param.json").write_bytes(b"{}")
        return root

    def _write_container(self, name: str) -> Path:
        path = self.tmp / name
        path.write_bytes(b"this is not a real pack, just a fixture")
        return path

    def _write_elf(self, name: str = "image.elf") -> Path:
        path = self.tmp / name
        path.write_bytes(b"\x7fELF" + b"\x00" * 64)
        return path

    def test_detect_container(self):
        for suffix in self.SUFFIXES:
            path = self.tmp / f"game{suffix}"
            path.write_bytes(b"container data")
            self.assertEqual(prepare_run.detect_container(path), suffix)
        self.assertIsNone(prepare_run.detect_container(self.tmp / "game.txt"))
        self.assertIsNone(prepare_run.detect_container(self.tmp / "game"))
        self.assertIsNone(prepare_run.detect_container(self.tmp / "missing.pkg"))

    def test_is_game_folder(self):
        self.assertFalse(prepare_run.is_game_folder(self.tmp))
        self.assertFalse(prepare_run.is_game_folder(self.tmp / "image.elf"))
        self.assertFalse(prepare_run.is_game_folder(self.tmp / "game"))

        game = self.game_folder()
        self.assertTrue(prepare_run.is_game_folder(game))
        self.assertFalse(prepare_run.is_game_folder(game / "subdir"))

    def test_discover_module_dirs(self):
        game = self.game_folder()
        modules = prepare_run.discover_module_dirs(game)
        self.assertEqual({m.name for m in modules}, {"sce_module"})

        siblings = prepare_run.discover_module_dirs(self.tmp / "game")
        self.assertEqual({m.name for m in siblings}, {"sce_module"})

        single = self.tmp / "game" / "rom.elf"
        single.write_bytes(b"\x7fELF")
        siblings = prepare_run.discover_module_dirs(single)
        self.assertEqual({m.name for m in siblings}, {"sce_module"})

        lone = self.tmp / "standalone.bin"
        lone.write_bytes(b"\x7fELF")
        self.assertEqual(prepare_run.discover_module_dirs(lone), [])

    def test_copy_executables_and_payload(self):
        game = self.game_folder()
        output = self.tmp / "run"
        module_dirs = (self.tmp / "extra_modules",)
        module_dirs[0].mkdir()
        (module_dirs[0] / "libextra.prx").write_bytes(b"prx")

        prepare_run.copy_executables_and_payload(
            game / "eboot.bin",
            game,
            output,
            "eboot.bin",
            verbose=False,
            module_dirs=module_dirs,
        )

        self.assertTrue((output / "eboot.bin").is_file())
        self.assertTrue((output / "sce_sys" / "param.json").is_file())
        self.assertTrue((output / "extra_modules" / "libextra.prx").is_file())
        self.assertFalse((output / "sce_module").exists())


    def test_prepare_run_game_folder(self):
        game = self.game_folder()
        out = self.tmp / "run"
        prepared = prepare_run.prepare_run(game, out, verbose=False)
        self.assertEqual(prepared, out)
        self.assertTrue((out / "eboot.bin").is_file())
        self.assertTrue((out / "sce_sys" / "param.json").is_file())
        self.assertTrue((out / "sce_module" / "libmonkey.prx").is_file())

    def test_prepare_run_standalone_elf(self):
        image = self._write_elf()
        out = self.tmp / "run"
        prepared = prepare_run.prepare_run(
            image, out, executable_name="spoon.bin", verbose=False
        )
        self.assertEqual(prepared, out)
        self.assertTrue((out / "spoon.bin").is_file())
        self.assertFalse((out / "sce_sys").exists())

    def test_prepare_run_container_is_converted_then_prepared(self):
        game = self.game_folder()
        fixture = self._write_container("game.ffpfs")

        with mock.patch.object(
            prepare_run, "extract_container", return_value=game
        ) as extractor, mock.patch.object(prepare_run, "tempfile") as mock_tempfile:
            fake_game = self.tmp / "converted_game"
            fake_game.mkdir()
            (fake_game / "eboot.bin").write_bytes(b"\x7fELF")
            (fake_game / "sce_sys").mkdir()
            (fake_game / "sce_sys" / "param.json").write_text("{}")
            mock_tempfile.mkdtemp.return_value = str(fake_game)

            out = self.tmp / "run"
            prepared = prepare_run.prepare_run(fixture, out, verbose=False)
            self.assertEqual(prepared, out)
            self.assertTrue((out / "eboot.bin").is_file())
            self.assertTrue((out / "sce_sys").is_dir())
            extractor.assert_called_once()
            args, _ = extractor.call_args
            self.assertEqual(args[0], fixture)
            self.assertEqual(args[1], fake_game)

    def test_main_game_folder(self):
        game = self.game_folder()
        out = self.tmp / "run"
        code = prepare_run.main([str(game), str(out)])
        self.assertEqual(code, 0)
        self.assertTrue((out / "eboot.bin").is_file())

    def test_main_standalone_elf(self):
        image = self._write_elf()
        out = self.tmp / "run"
        code = prepare_run.main([str(image), str(out), "--executable", "spoon.bin"])
        self.assertEqual(code, 0)
        self.assertTrue((out / "spoon.bin").is_file())

    def test_main_missing_input(self):
        code = prepare_run.main(["does-not-exist", str(self.tmp / "out")])
        self.assertEqual(code, 2)

    def test_prepare_run_unsupported_directory_raises_value_error(self):
        with self.assertRaises(ValueError):
            prepare_run.prepare_run(self.tmp / "empty_dir", self.tmp / "out")

    def test_main_missing_executable(self):
        game = self.game_folder()
        out = self.tmp / "run"
        code = prepare_run.main([str(game), str(out), "--source", "nope.bin"])
        self.assertEqual(code, 2)

    def test_loose_root_modules_are_staged(self):
        """Third-party modules shipped loose in the game folder root (not in a
        sce_module/ dir) are staged into sce_module so the relinker converts
        them; otherwise game.exe cannot LoadLibrary them (GetLastError 126)."""
        game = self.game_folder(modules=())
        (game / "libcohtml.Prospero.prx").write_bytes(b"\x00" * 16)
        (game / "libfmod.prx").write_bytes(b"\x00" * 16)
        out = self.tmp / "run"

        prepared = prepare_run.prepare_run(game, out, verbose=False)

        self.assertEqual(prepared, out)
        self.assertTrue((out / "sce_module" / "libcohtml.Prospero.prx").is_file())
        self.assertTrue((out / "sce_module" / "libfmod.prx").is_file())
        # The loose file is not left in the run root.
        self.assertFalse((out / "libcohtml.Prospero.prx").exists())

    def test_loose_root_module_does_not_override_dir_copy(self):
        """When a module ships both inside sce_module/ and loose in the root,
        the sce_module copy (the relinker's canonical location) wins."""
        game = self.game_folder(modules=("sce_module",))
        (game / "sce_module" / "libcohtml.Prospero.prx").write_bytes(b"dir-copy")
        (game / "libcohtml.Prospero.prx").write_bytes(b"loose-copy")
        out = self.tmp / "run"

        prepare_run.prepare_run(game, out, verbose=False)

        staged = (out / "sce_module" / "libcohtml.Prospero.prx").read_bytes()
        self.assertEqual(staged, b"dir-copy")

    def test_defaults(self):
        self.assertEqual(prepare_run.DEFAULT_EXECUTABLE, "eboot.bin")
        self.assertEqual(prepare_run.DEFAULT_SOURCE, "eboot.bin")


if __name__ == "__main__":
    unittest.main()

