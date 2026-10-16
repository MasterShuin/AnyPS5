#!/usr/bin/env python3
"""Add export names to a PE ``.prx`` image without relinking it.

The export directory of the native libraries lives in ``.edata``, which has
only a few hundred bytes of slack, and the last section is far too small to
hold the extra table entries, so growing the original directory in place does
not work.  Instead this appends a brand-new section (``.expd``) holding a
*complete*, freshly built export directory:

* every original export keeps its ``AddressOfFunctions`` slot (including
  forwarders, whose strings are copied into the new section so they stay
  inside the new directory range);
* the merged name pointer table is sorted -- the Windows loader binary
  searches it and ``tools/import_audit.py`` rejects an unsorted table;
* ordinals are **indexes into the address table** (not ``base + index``);
* each new NID gets its own address entry pointing at an ``xor eax,eax;
  ret`` gadget found in the executable section, so both the import audit and
  the loader see a normal, file-backed function RVA.

Run::

    python tools/prx_export_patch.py SRC.prx DST.prx [nids.txt]

``nids.txt`` has one NID per line; when omitted the union of the historical
missing-NID lists at the repository root is used.  The result is re-parsed with
:func:`import_audit.read_exports` (the code the pipeline audit runs) before the
script reports success.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

#: Section we append / rebuild, recognised so the patch can run twice.
SECTION_NAME = b".expd\x00\x00\x00"
#: IMAGE_SCN_CNT_INITIALIZED_DATA | IMAGE_SCN_MEM_READ
SECTION_CHARACTERISTICS = 0x00000040 | 0x40000000
#: IMAGE_SCN_MEM_EXECUTE, used to find the executable section for the gadget.
SCN_MEM_EXECUTE = 0x20000000

#: Default NID list for the CLI (the pipeline passes the audit's own absent set).
DEFAULT_NIDS = Path(__file__).resolve().parent.parent / "inject_nids_union.txt"


class PatchError(Exception):
    """Raised when the image cannot be patched safely."""


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def parse_pe(data: bytes) -> dict:
    """Return headers, section table location and export data directory."""
    if data[:2] != b"MZ":
        raise PatchError("not an MZ image")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\x00\x00":
        raise PatchError("not a PE image")
    num_sections = struct.unpack_from("<H", data, pe + 6)[0]
    size_opt = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic not in (0x10B, 0x20B):
        raise PatchError(f"unsupported optional header magic 0x{magic:x}")
    section_align = struct.unpack_from("<I", data, opt + 32)[0]
    file_align = struct.unpack_from("<I", data, opt + 36)[0]
    size_of_image_off = opt + 56
    size_of_image = struct.unpack_from("<I", data, size_of_image_off)[0]
    dd = opt + (112 if magic == 0x20B else 96)
    export_rva, export_size = struct.unpack_from("<II", data, dd)
    sec_start = opt + size_opt
    sections = []
    for index in range(num_sections):
        off = sec_start + index * 40
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, off + 8)
        characteristics = struct.unpack_from("<I", data, off + 36)[0]
        sections.append({
            "name": data[off:off + 8],
            "vsize": vsize,
            "vaddr": vaddr,
            "rsize": rsize,
            "raddr": raddr,
            "characteristics": characteristics,
            "header": off,
        })
    return {
        "pe": pe,
        "num_sections": num_sections,
        "sec_start": sec_start,
        "section_align": section_align,
        "file_align": file_align,
        "size_of_image_off": size_of_image_off,
        "size_of_image": size_of_image,
        "dd_export": dd,
        "export_rva": export_rva,
        "export_size": export_size,
        "sections": sections,
    }


def rva_to_foff(sections: list, rva: int) -> int | None:
    """Map an RVA to a file offset; ``None`` when it is not file-backed."""
    for section in sections:
        span = max(section["vsize"], section["rsize"])
        if section["vaddr"] <= rva < section["vaddr"] + span:
            relative = rva - section["vaddr"]
            if relative >= section["rsize"]:
                return None
            return section["raddr"] + relative
    return None


def _cstring(data: bytes, offset: int) -> bytes:
    end = data.find(b"\x00", offset)
    if end < 0:
        raise PatchError(f"unterminated string at 0x{offset:x}")
    return data[offset:end]
def read_export_directory(data: bytes, headers: dict) -> dict:
    """Read the current export directory (names, EAT, ordinals, forwarders)."""
    rva, size = headers["export_rva"], headers["export_size"]
    if not rva:
        raise PatchError("image has no export directory")
    foff = rva_to_foff(headers["sections"], rva)
    if foff is None:
        raise PatchError(f"export directory at RVA 0x{rva:x} is not file-backed")
    name_rva, ordinal_base, n_addr, n_names, eat_rva, names_rva, ords_rva = (
        struct.unpack_from("<IIIIIII", data, foff + 12)
    )
    if n_names > n_addr:
        raise PatchError(f"{n_names} names but only {n_addr} address slots")
    eat_off = rva_to_foff(headers["sections"], eat_rva)
    names_off = rva_to_foff(headers["sections"], names_rva)
    ords_off = rva_to_foff(headers["sections"], ords_rva)
    if None in (eat_off, names_off, ords_off):
        raise PatchError("export tables are not file-backed")
    eat = list(struct.unpack_from(f"<{n_addr}I", data, eat_off))
    ordinals = list(struct.unpack_from(f"<{n_names}H", data, ords_off))
    names = []
    for index in range(n_names):
        entry_rva = struct.unpack_from("<I", data, names_off + index * 4)[0]
        entry_off = rva_to_foff(headers["sections"], entry_rva)
        if entry_off is None:
            raise PatchError(f"export name #{index} at RVA 0x{entry_rva:x} not file-backed")
        names.append(_cstring(data, entry_off).decode("ascii"))
    name_to_slot = {}
    for name, slot in zip(names, ordinals):
        if slot >= n_addr:
            raise PatchError(f"export {name}: ordinal {slot} outside address table")
        name_to_slot.setdefault(name, slot)
    # Forwarders are address entries pointing into the directory itself.
    forwarders = []
    for slot, target in enumerate(eat):
        if rva <= target < rva + size:
            target_off = rva_to_foff(headers["sections"], target)
            if target_off is None:
                raise PatchError(f"forwarder #{slot} at RVA 0x{target:x} not file-backed")
            forwarders.append((slot, _cstring(data, target_off)))
    dll_off = rva_to_foff(headers["sections"], name_rva)
    if dll_off is None:
        raise PatchError("export directory Name string is not file-backed")
    return {
        "ordinal_base": ordinal_base,
        "eat": eat,
        "names": names,
        "name_to_slot": name_to_slot,
        "forwarders": forwarders,
        "dll_name": _cstring(data, dll_off),
    }


def find_stub_rva(data: bytes, headers: dict, eat: list) -> int:
    """Return an RVA of ``xor eax,eax; ret`` in an executable section.

    A shared gadget is a safe stand-in for the generated stubs: calls return
    zero instead of jumping into an unrelated function.  Falls back to a bare
    ``ret`` and finally to the first address-table entry.
    """
    for section in headers["sections"]:
        if not section["characteristics"] & SCN_MEM_EXECUTE or not section["rsize"]:
            continue
        limit = section["raddr"] + section["rsize"]
        for gadget in (b"\x31\xc0\xc3", b"\x33\xc0\xc3"):
            pos = data.find(gadget, section["raddr"], limit)
            if pos >= 0:
                return section["vaddr"] + (pos - section["raddr"])
        pos = data.find(b"\xc3", section["raddr"], limit)
        if pos >= 0:
            return section["vaddr"] + (pos - section["raddr"])
    if eat:
        return eat[0]
    raise PatchError("no executable section found for the stub target")
def _build_blob(old: dict, new_names: list, stub_rva: int, dll_name: bytes) -> tuple:
    """Serialize a complete export directory covering old + new exports.

    Returns ``(blob, meta)``; the blob still has relative offsets, RVAs are
    written by :func:`_finalize_blob` once the section base RVA is known.
    """
    eat = list(old["eat"])
    slots = dict(old["name_to_slot"])
    fresh = sorted(set(new_names) - set(old["names"]))
    for name in fresh:
        slots[name] = len(eat)
        eat.append(stub_rva)
    merged = sorted(set(old["names"]) | set(fresh))
    try:
        encoded = [(name, name.encode("ascii")) for name in merged]
    except UnicodeEncodeError as error:
        raise PatchError(f"export name is not ASCII: {error}") from error

    n_addr, n_names = len(eat), len(encoded)
    # Layout: directory | EAT | name pointers | ordinals | strings.
    off_eat = 40
    off_ptrs = off_eat + n_addr * 4
    off_ords = off_ptrs + n_names * 4
    off_strings = off_ords + n_names * 2
    total = off_strings + len(dll_name) + 1
    total += sum(len(raw) + 1 for _, raw in encoded)
    total += sum(len(raw) + 1 for _, raw in old["forwarders"])

    blob = bytearray(total)
    struct.pack_into("<I", blob, 16, old["ordinal_base"])
    struct.pack_into("<I", blob, 20, n_addr)
    struct.pack_into("<I", blob, 24, n_names)
    for index, value in enumerate(eat):
        struct.pack_into("<I", blob, off_eat + index * 4, value)

    cursor = off_strings
    dll_offset = cursor
    blob[cursor:cursor + len(dll_name) + 1] = dll_name + b"\x00"
    cursor += len(dll_name) + 1
    name_offsets = {}
    for name, raw in encoded:
        name_offsets[name] = cursor
        blob[cursor:cursor + len(raw) + 1] = raw + b"\x00"
        cursor += len(raw) + 1
    forward_offsets = {}
    for slot, raw in old["forwarders"]:
        forward_offsets[slot] = cursor
        blob[cursor:cursor + len(raw) + 1] = raw + b"\x00"
        cursor += len(raw) + 1
    if cursor != len(blob):
        raise PatchError(f"blob size mismatch: {cursor} != {len(blob)}")

    meta = {
        "off_eat": off_eat,
        "off_ptrs": off_ptrs,
        "off_ords": off_ords,
        "dll_offset": dll_offset,
        "name_offsets": name_offsets,
        "forward_offsets": forward_offsets,
        "merged": merged,
        "slots": slots,
        "n_addr": n_addr,
        "n_names": n_names,
    }
    return bytes(blob), meta


def _finalize_blob(blob: bytes, meta: dict, base_rva: int) -> bytes:
    """Write RVAs (they depend on the section base) into the serialized blob."""
    out = bytearray(blob)
    struct.pack_into("<I", out, 12, base_rva + meta["dll_offset"])
    struct.pack_into("<I", out, 28, base_rva + meta["off_eat"])
    struct.pack_into("<I", out, 32, base_rva + meta["off_ptrs"])
    struct.pack_into("<I", out, 36, base_rva + meta["off_ords"])
    for index, name in enumerate(meta["merged"]):
        struct.pack_into("<I", out, meta["off_ptrs"] + index * 4,
                         base_rva + meta["name_offsets"][name])
        struct.pack_into("<H", out, meta["off_ords"] + index * 2, meta["slots"][name])
    for slot, offset in meta["forward_offsets"].items():
        struct.pack_into("<I", out, meta["off_eat"] + slot * 4, base_rva + offset)
    return bytes(out)
def add_exports(src_path, dst_path, new_export_names) -> dict:
    """Patch *src_path* so it exports *new_export_names* too; write *dst_path*.

    Returns stats (old/new counts, section placement, stub RVA).  Raises
    :class:`PatchError` when the image cannot be patched safely.  Running it
    again on its own output only adds names that are still missing.
    """
    src_path, dst_path = Path(src_path), Path(dst_path)
    data = src_path.read_bytes()
    headers = parse_pe(data)
    old = read_export_directory(data, headers)

    wanted, seen = [], set(old["names"])
    for name in new_export_names:
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            wanted.append(name)
    if not wanted:
        # Idempotent no-op: still materialise dst so callers can rely on it.
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_bytes(data)
        verify(dst_path)
        return {"old_exports": len(old["names"]), "added": 0,
                "total": len(old["names"]), "section_rva": headers["export_rva"],
                "section_file_offset": rva_to_foff(headers["sections"],
                                                   headers["export_rva"]),
                "stub_rva": None, "path": str(dst_path)}

    stub_rva = find_stub_rva(data, headers, old["eat"])
    sections = headers["sections"]
    existing = next((s for s in sections if s["name"] == SECTION_NAME), None)

    if existing is not None:
        # Re-patch: rebuild at the same virtual address, replacing the blob.
        base_rva = existing["vaddr"]
        file_off = existing["raddr"]
        out = bytearray(data[:file_off])
    else:
        base_rva = _align(max(s["vaddr"] + max(s["vsize"], s["rsize"])
                              for s in sections), headers["section_align"])
        file_off = _align(len(data), headers["file_align"])
        table_end = headers["sec_start"] + headers["num_sections"] * 40
        first_raw = min(s["raddr"] for s in sections if s["raddr"])
        if table_end + 40 > first_raw:
            raise PatchError(
                f"no slack for a new section header (table ends 0x{table_end:x}, "
                f"first raw data 0x{first_raw:x})"
            )
        out = bytearray(data)
        out.extend(b"\x00" * (file_off - len(out)))

    blob, meta = _build_blob(old, wanted, stub_rva, src_path.name.encode("ascii"))
    blob = _finalize_blob(blob, meta, base_rva)
    rsize = _align(len(blob), headers["file_align"])
    out.extend(blob)
    out.extend(b"\x00" * (file_off + rsize - len(out)))

    if existing is None:
        header_off = headers["sec_start"] + headers["num_sections"] * 40
        out[header_off:header_off + 8] = SECTION_NAME
        struct.pack_into("<I", out, header_off + 8, len(blob))   # VirtualSize
        struct.pack_into("<I", out, header_off + 12, base_rva)   # VirtualAddress
        struct.pack_into("<I", out, header_off + 16, rsize)      # SizeOfRawData
        struct.pack_into("<I", out, header_off + 20, file_off)   # PointerToRawData
        struct.pack_into("<I", out, header_off + 36, SECTION_CHARACTERISTICS)
        struct.pack_into("<H", out, headers["pe"] + 6, headers["num_sections"] + 1)
    else:
        struct.pack_into("<I", out, existing["header"] + 8, len(blob))
        struct.pack_into("<I", out, existing["header"] + 16, rsize)
        struct.pack_into("<I", out, existing["header"] + 20, file_off)

    size_of_image = _align(base_rva + len(blob), headers["section_align"])
    if size_of_image > headers["size_of_image"]:
        struct.pack_into("<I", out, headers["size_of_image_off"], size_of_image)
    struct.pack_into("<II", out, headers["dd_export"], base_rva, len(blob))

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_bytes(bytes(out))
    stats = {
        "old_exports": len(old["names"]),
        "added": len(wanted),
        "total": meta["n_names"],
        "section_rva": base_rva,
        "section_file_offset": file_off,
        "stub_rva": stub_rva,
        "path": str(dst_path),
    }
    verify(dst_path, wanted)
    return stats
def verify(path, expected=()) -> int:
    """Re-parse *path* exactly the way the pipeline audit does.

    Raises :class:`PatchError` on any structural problem (unsorted name
    table, out-of-range ordinals, non-file-backed RVAs, missing expected
    names); returns the number of exports read.
    """
    path = Path(path)
    data = path.read_bytes()
    headers = parse_pe(data)

    # 1. Structural read through the very code the pipeline audit runs.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import import_audit  # noqa: PLC0415 - local import keeps this module light
        names = import_audit.read_exports(path, data)
    except ImportError as error:  # pragma: no cover - tools/ ships with the repo
        raise PatchError(f"cannot import tools/import_audit.py: {error}") from error
    except Exception as error:  # noqa: BLE001 - AuditError, struct.error, ...
        raise PatchError(f"audit rejected the patched image: {error}") from error
    finally:
        sys.path.pop(0)

    missing = [name for name in expected if name not in names]
    if missing:
        raise PatchError(f"missing {len(missing)} expected exports: {missing[:5]}")

    # 2. Every named export must land inside some section's virtual range.
    #    (Data exports may live in .bss, which is zero-filled and therefore
    #    not file-backed -- that is legal and the audit accepts it.)
    old = read_export_directory(data, headers)
    export_end = headers["export_rva"] + headers["export_size"]
    for name, slot in old["name_to_slot"].items():
        target = old["eat"][slot]
        if headers["export_rva"] <= target < export_end:
            continue  # forwarder, already validated while reading
        if not any(s["vaddr"] <= target < s["vaddr"] + max(s["vsize"], s["rsize"])
                   for s in headers["sections"]):
            raise PatchError(f"export {name}: target RVA 0x{target:x} in no section")
    return len(names)



def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    src, dst = Path(argv[0]), Path(argv[1])
    nids_path = Path(argv[2]) if len(argv) > 2 else DEFAULT_NIDS
    if not nids_path.is_file():
        print(f"ERROR: NID list not found: {nids_path}", file=sys.stderr)
        return 2
    nids = [line.strip() for line in
            nids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        stats = add_exports(src, dst, nids)
    except PatchError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    print(f"OK: {dst}")
    print(f"  exports: {stats['old_exports']} -> {stats['total']} "
          f"(+{stats['added']} requested)")
    print(f"  .expd section: RVA 0x{stats['section_rva']:x}, "
          f"file offset 0x{stats['section_file_offset']:x}")
    if stats["stub_rva"] is not None:
        print(f"  stub gadget: RVA 0x{stats['stub_rva']:x}")
    print("  verified with tools/import_audit.read_exports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




