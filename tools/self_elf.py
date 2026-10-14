#!/usr/bin/env python3
"""Unwrap a SELF container into the plain ELF the relinker requires.

The relinker refuses a signed input outright (``ElfReader`` rejects the two
SELF magics with ``The input is a SELF container, not an ELF``, and
``docs/user/USAGE.md`` states that a clean ELF is required), so a PS5 title --
whose ``eboot.bin`` and bundled modules ship signed -- has to be unwrapped
before it reaches the relinker. ``prepare_run`` promises *the relinkable
executable*, which is why the conversion lives in the prepare step rather than
in the relinker.

The reader itself is not re-implemented here: it is the backend's own
``ps5-ultrapack/backend/self_file.py`` (standard library only), loaded by path
with :mod:`importlib` because the ``ps5-ultrapack`` directory name contains a
hyphen and therefore cannot be imported as a package. A fake-signed SELF -- the
only kind this reader understands -- keeps every segment in the clear, so the
ELF is rebuilt without needing any key. An NPDRM-encrypted SELF cannot be
unwrapped and is reported as such.

Standard library only.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
#: The backend reader, beside this repository's ``tools/`` directory.
_SELF_FILE = _HERE.parent / "ps5-ultrapack" / "backend" / "self_file.py"
#: Module name used for the by-path load; must be registered before exec_module
#: because the reader defines ``@dataclass`` classes.
_MODULE_NAME = "anyps5_self_file"

#: First four bytes of a signed file, as accepted by the backend reader.
SELF_MAGICS = (b"\x4f\x15\x3d\x1d", b"\x54\x14\xf5\xee")


class SelfConversionError(ValueError):
    """A SELF container could not be unwrapped (encrypted or not a readable one)."""


_reader_cache = None


def _reader():
    """The backend ``self_file`` module, loaded once by path.

    Loading it into ``sys.modules`` *before* executing it is required: its
    ``@dataclass`` classes resolve annotations through the module registry and
    would otherwise fail with ``AttributeError: 'NoneType' object has no
    attribute '__dict__'``.
    """
    global _reader_cache
    if _reader_cache is None:
        if not _SELF_FILE.is_file():
            raise SelfConversionError(
                f"cannot unwrap a signed executable: {_SELF_FILE} is missing"
            )
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, str(_SELF_FILE))
        if spec is None or spec.loader is None:
            raise SelfConversionError(f"cannot load the SELF reader from {_SELF_FILE}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
        _reader_cache = module
    return _reader_cache


def is_self(data: bytes) -> bool:
    """True when *data* starts with a PS4/PS5 SELF magic."""
    return bytes(data[:4]) in SELF_MAGICS


def to_elf(data: bytes) -> bytes:
    """Return *data* as the plain ELF it wraps.

    A file that is not a SELF is returned unchanged, so callers can pass every
    staged file without first classifying it: an already-plain ELF stays
    byte-identical and anything else is left for the relinker to reject with
    its own diagnostic.

    Raises :class:`SelfConversionError` for a SELF whose segments are
    encrypted or compressed (an NPDRM retail file): rebuilding the ELF would
    need the console's keys, which this project does not have.
    """
    if not is_self(data):
        return data
    reader = _reader()
    image = reader.parse_self(data)
    if image is None:
        raise SelfConversionError(
            "the SELF header could not be read (truncated or unsupported layout)"
        )
    if not image.readable:
        raise SelfConversionError(
            "the executable is an encrypted SELF; decrypt it first "
            "(only a fake-signed file can be unwrapped without keys)"
        )
    elf = reader.elf_image(data, image)
    if elf is None or elf[:4] != b"\x7fELF":
        raise SelfConversionError("the SELF did not yield a valid ELF image")
    return elf


def convert_file(path: Path) -> bool:
    """Rewrite *path* in place when it holds a SELF; report whether it changed.

    The replacement is written to a temporary file in the same directory and
    moved over the target only once complete, so an interrupted conversion can
    never leave a half-written executable behind (the backend's fake signer
    uses the same scheme). The original mode bits are carried over.
    """
    path = Path(path)
    original = path.read_bytes()
    converted = to_elf(original)
    if converted == original:
        return False
    directory = path.parent
    fd, tmp = tempfile.mkstemp(prefix=".self-elf-", dir=str(directory))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(converted)
        try:
            os.chmod(tmp, os.stat(path).st_mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def convert_module_dir(directory: Path, verbose: bool = False) -> int:
    """Unwrap every file directly inside a bundled-module directory.

    Only the directory's own files are considered: that is the level the
    relinker scans (``docs/user/USAGE.md``). Returns how many files changed.
    """
    changed = 0
    for entry in sorted(Path(directory).iterdir()):
        if not entry.is_file():
            continue
        if convert_file(entry):
            changed += 1
            if verbose:
                print(f"[prepare_run] {entry.name}: SELF container -> plain ELF")
    return changed