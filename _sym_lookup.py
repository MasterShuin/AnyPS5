import bisect
import re
import sys


bases = {
    "_nm_libc.txt": 0x33A350000,
    "_nm_libkernel.txt": 0x1D81B0000,
}


def lookup(path, targets, label):
    base_addr = bases[path]
    addrs = []
    names = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.rstrip("\r\n").split(" ", 2)
            if len(parts) < 3:
                continue
            addr, typ, name = parts
            if not re.fullmatch(r"[0-9a-fA-F]+", addr):
                continue
            if typ not in "tTwW":
                continue
            addrs.append(int(addr, 16) - base_addr)
            names.append(name)
    order = sorted(range(len(addrs)), key=lambda i: addrs[i])
    addrs = [addrs[i] for i in order]
    names = [names[i] for i in order]
    for target in targets:
        index = bisect.bisect_right(addrs, target) - 1
        if index < 0:
            print(f"{label} 0x{target:x} -> <no symbol below>")
            continue
        print(f"{label} 0x{target:x} -> {names[index]}+0x{target - addrs[index]:x} (0x{addrs[index]:x})")


libc_targets = [0x11DD8, 0xF7C31, 0x71306, 0x6C48B, 0xED5E7, 0x6C4B0, 0x737AA, 0x73B49, 0x57189, 0x12EF, 0x12A4, 0x13E6]
kernel_targets = [0x5CE36, 0x5D69C]

lookup("_nm_libc.txt", libc_targets, "libc")
lookup("_nm_libkernel.txt", kernel_targets, "libkernel")

