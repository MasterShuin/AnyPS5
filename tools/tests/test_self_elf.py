"""Unit tests for :mod:`tools.self_elf` — unwrapping a signed PS5 file.

The SELF fixture is assembled by hand instead of through the vendored signer:
``self_file.parse_self`` only needs a header, one entry and an embedded ELF,
and building it here keeps the test independent of ``make_fself``'s own
parsing, so a failure points at this module rather than at the signer.
"""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_run  # noqa: E402
import self_elf  # noqa: E402

ELF_MAGIC = b"\x7fELF"
#: ``props`` bit marking an entry as program bytes rather than a digest, plus
#: the encrypted bit used for the retail-file case.
HAS_BLOCKS = 1 << 11
ENCRYPTED = 1 << 1
PAYLOAD = b"the executable bytes that must survive the round trip"
#: Where the payload sits in the rebuilt ELF (page-aligned, as in a real title).
PAYLOAD_OFFSET = 0x1000


def _elf_header() -> bytes:
    """A minimal ELF64 header with one PT_LOAD at :data:`PAYLOAD_OFFSET`."""
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHIQQQIHHHHHH", header, 16,
                     3,      # e_type: ET_DYN
                     0x3E,   # e_machine: x86-64
                     1,      # e_version
                     0,      # e_entry
                     64,     # e_phoff
                     0,      # e_shoff
                     0,      # e_flags
                     64,     # e_ehsize
                     56,     # e_phentsize
                     1,      # e_phnum
                     0, 0, 0)  # e_shentsize, e_shnum, e_shstrndx
    phdr = struct.pack("<2I6Q", 1, 5, PAYLOAD_OFFSET, 0, 0,
                       len(PAYLOAD), len(PAYLOAD), 0x1000)
    return bytes(header) + phdr


def signed_self(*, encrypted: bool = False, ps5: bool = True) -> bytes:
    """A SELF wrapping :data:`_elf_header` + :data:`PAYLOAD`."""
    elf = _elf_header()
    assert len(elf) == 120
    magic = b"\x54\x14\xf5\xee" if ps5 else b"\x4f\x15\x3d\x1d"
    payload_at = 0x100                     # inside the SELF, after its headers
    header_size = 0x20 + 32                # fixed header + one entry
    file_size = payload_at + len(PAYLOAD)
    head = bytearray(magic.ljust(8, b"\0"))
    head += struct.pack("<I2HQ2H4x", 0, header_size, 0, file_size, 1, 0)
    props = HAS_BLOCKS | (ENCRYPTED if encrypted else 0)
    head += struct.pack("<4Q", props, payload_at, len(PAYLOAD), len(PAYLOAD))
    assert len(head) == 0x40
    head += elf                            # embedded ELF starts here (0x40)
    head = head.ljust(payload_at, b"\0")
    head += PAYLOAD
    return bytes(head)


class ToElfTests(unittest.TestCase):
    def test_plain_elf_is_returned_unchanged(self):
        data = ELF_MAGIC + b"\x00" * 64
        self.assertEqual(self_elf.to_elf(data), data)

    def test_unrelated_bytes_are_returned_unchanged(self):
        data = b"this is not an executable"
        self.assertEqual(self_elf.to_elf(data), data)

    def test_fake_signed_self_becomes_an_elf(self):
        for ps5 in (True, False):
            with self.subTest(ps5=ps5):
                elf = self_elf.to_elf(signed_self(ps5=ps5))
                self.assertEqual(elf[:4], ELF_MAGIC)
                self.assertIn(PAYLOAD, elf)
                # The section headers a SELF does not carry stay absent.
                self.assertEqual(struct.unpack_from("<Q", elf, 0x28)[0], 0)
                self.assertEqual(struct.unpack_from("<3H", elf, 0x3A), (0, 0, 0))

    def test_payload_lands_at_its_program_header_offset(self):
        elf = self_elf.to_elf(signed_self())
        self.assertEqual(
            elf[PAYLOAD_OFFSET:PAYLOAD_OFFSET + len(PAYLOAD)], PAYLOAD
        )

    def test_encrypted_self_reports_a_clear_error(self):
        with self.assertRaises(self_elf.SelfConversionError) as ctx:
            self_elf.to_elf(signed_self(encrypted=True))
        self.assertIn("encrypted", str(ctx.exception))

    def test_truncated_self_reports_a_clear_error(self):
        with self.assertRaises(self_elf.SelfConversionError) as ctx:
            self_elf.to_elf(b"\x54\x14\xf5\xee" + bytes(0x10))
        self.assertIn("SELF header", str(ctx.exception))

    def test_is_self_only_matches_the_two_magics(self):
        self.assertTrue(self_elf.is_self(signed_self(ps5=True)))
        self.assertTrue(self_elf.is_self(signed_self(ps5=False)))
        self.assertFalse(self_elf.is_self(ELF_MAGIC))
        self.assertFalse(self_elf.is_self(b"\x00" * 8))

    def test_self_conversion_error_is_a_value_error(self):
        """prepare_run's CLI reports ValueError subclasses with exit status 2."""
        self.assertTrue(issubclass(self_elf.SelfConversionError, ValueError))


class ConvertFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="self_elf_test_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_signed_file_is_rewritten_in_place(self):
        path = self.tmp / "eboot.bin"
        path.write_bytes(signed_self())
        self.assertTrue(self_elf.convert_file(path))
        self.assertEqual(path.read_bytes()[:4], ELF_MAGIC)
        # A second pass has nothing left to unwrap.
        self.assertFalse(self_elf.convert_file(path))

    def test_plain_elf_file_is_left_byte_identical(self):
        data = ELF_MAGIC + b"\x00" * 64
        path = self.tmp / "clean.elf"
        path.write_bytes(data)
        self.assertFalse(self_elf.convert_file(path))
        self.assertEqual(path.read_bytes(), data)

    def test_no_temporary_file_is_left_behind(self):
        path = self.tmp / "eboot.bin"
        path.write_bytes(signed_self())
        self_elf.convert_file(path)
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["eboot.bin"])

    def test_encrypted_self_keeps_the_original(self):
        path = self.tmp / "eboot.bin"
        original = signed_self(encrypted=True)
        path.write_bytes(original)
        with self.assertRaises(self_elf.SelfConversionError):
            self_elf.convert_file(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["eboot.bin"])

    def test_module_dir_converts_files_only(self):
        directory = self.tmp / "sce_module"
        directory.mkdir()
        (directory / "liba.prx").write_bytes(signed_self())
        (directory / "data.txt").write_bytes(b"payload, not executable")
        nested = directory / "nested"
        nested.mkdir()
        (nested / "libb.prx").write_bytes(signed_self())

        changed = self_elf.convert_module_dir(directory)
        self.assertEqual(changed, 1)
        self.assertEqual((directory / "liba.prx").read_bytes()[:4], ELF_MAGIC)
        self.assertEqual((directory / "data.txt").read_bytes(), b"payload, not executable")
        self.assertEqual((nested / "libb.prx").read_bytes()[:4], b"\x54\x14\xf5\xee")


class PrepareIntegrationTests(unittest.TestCase):
    """The staged copies are plain ELF while the user's source stays signed."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="self_elf_prep_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.game = self.tmp / "game"
        (self.game / "sce_module").mkdir(parents=True)
        (self.game / "sce_sys").mkdir()
        (self.game / "eboot.bin").write_bytes(signed_self())
        (self.game / "sce_sys" / "param.json").write_text("{}", encoding="utf-8")
        (self.game / "sce_module" / "libmonkey.prx").write_bytes(signed_self())

    def test_staged_copies_are_elf_and_source_stays_signed(self):
        out = self.tmp / "run"
        prepare_run.prepare_run(self.game, out, verbose=False)
        self.assertEqual((out / "eboot.bin").read_bytes()[:4], ELF_MAGIC)
        self.assertEqual((out / "sce_module" / "libmonkey.prx").read_bytes()[:4], ELF_MAGIC)
        # The source folder is the user's: never rewritten.
        self.assertEqual((self.game / "eboot.bin").read_bytes()[:4], b"\x54\x14\xf5\xee")
        self.assertEqual((self.game / "sce_module" / "libmonkey.prx").read_bytes()[:4],
                         b"\x54\x14\xf5\xee")

    def test_encrypted_eboot_fails_the_prepare_step(self):
        (self.game / "eboot.bin").write_bytes(signed_self(encrypted=True))
        with self.assertRaises(self_elf.SelfConversionError):
            prepare_run.prepare_run(self.game, self.tmp / "run", verbose=False)


if __name__ == "__main__":
    unittest.main()
