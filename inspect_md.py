"""Dump the trailing SELF layout of MediaDecoders to decide the note fix."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "ps5-ultrapack" / "backend"))

import self_file  # noqa: E402

OUT = HERE / "md_layout.txt"
SRC = HERE / "_md_extract" / "MediaDecoders.Prospero.prx"

PT_NOTE = 0x4
PT_SCE_COMMENT = 0x6FFFFF00
PT_SCE_VERSION = 0x6FFFFF01
PT_LOAD = 0x1
PT_DYNAMIC = 0x2
PT_TLS = 0x7
PT_GNU_EH_FRAME = 0x6474E550
PT_GNU_RELRO = 0x6474E552
PT_SCE_DYNLIBDATA = 0x61000002
PT_SCE_DYNLIB = 0x61000000

LABELS = {
    PT_NOTE: "NOTE", PT_SCE_COMMENT: "COMMENT", PT_SCE_VERSION: "VERSION",
    PT_LOAD: "LOAD", PT_DYNAMIC: "DYNAMIC", PT_TLS: "TLS",
    PT_GNU_EH_FRAME: "GNU_EH_FRAME", PT_GNU_RELRO: "GNU_RELRO",
    PT_SCE_DYNLIBDATA: "DYNLIBDATA", PT_SCE_DYNLIB: "DYNLIB",
}


def main() -> int:
    raw = SRC.read_bytes()
    img = self_file.parse_self(raw)
    if img is None:
        OUT.write_text("parse_self returned None", encoding="utf-8")
        return 1
    lines: list[str] = []
    lines.append(f"raw SELF size={len(raw):#x}")
    lines.append(f"elf_offset={img.elf_offset:#x} "
                 f"elf_header_size={img.elf_header_size:#x} "
                 f"file_size={img.file_size:#x}")
    lines.append(f"phdrs ({len(img.phdrs)}):")
    for i, ph in enumerate(img.phdrs):
        p_type, _flags, off, _va, _pa, filesz, _msz, _al = ph
        lines.append(f"  [{i}] {LABELS.get(p_type, hex(p_type)):12} off={off:#x} "
                     f"filesz={filesz:#x} end={off + filesz:#x}")
    lines.append(f"stored segments keys: {sorted(img.segments.keys())}")

    # Convert with the patched elf_image and re-check every PHDR bound.
    converted = self_file.elf_image(raw, img)
    if converted is None:
        lines.append("elf_image returned None")
    else:
        import struct
        size = len(converted)
        (phoff,) = struct.unpack_from("<Q", converted, 32)
        e_phentsize, e_phnum = struct.unpack_from("<HH", converted, 54)
        lines.append(f"converted ELF size={size:#x} ({size}) "
                     f"phoff={phoff:#x} phnum={e_phnum}")
        bad = 0
        for i in range(e_phnum):
            base = phoff + i * e_phentsize
            p_type = struct.unpack_from("<I", converted, base)[0]
            p_offset = struct.unpack_from("<Q", converted, base + 8)[0]
            p_filesz = struct.unpack_from("<Q", converted, base + 32)[0]
            end = p_offset + p_filesz
            if end > size:
                bad += 1
                lines.append(f"  STILL EXCEEDS PHDR[{i}] "
                             f"type={LABELS.get(p_type, hex(p_type))} "
                             f"end={end:#x} > size={size:#x}")
        lines.append(f"exceed_count={bad}")
        lines.append("OK: no PHDR exceeds file" if bad == 0 else "FAIL: truncation remains")
        (HERE / "md_converted_fixed.elf").write_bytes(converted)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())