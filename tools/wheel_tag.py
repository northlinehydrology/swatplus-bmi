"""The wheel platform tag for a built SWAT+ exchange executable, read from the executable itself, or an error if it
couldn't run on a machine of that tag as it is:

    python tools/wheel_tag.py bin/swatplus-62.0.1-exchange      # macosx_11_0_arm64, win_amd64, manylinux_2_17_x86_64...

- Windows: a 64-bit x86 program (win_amd64). SWAT+'s CMake links gfortran's runtime statically there, and the only
  DLLs it may name are Windows' own.
- Linux: a statically linked x86-64 or ARM program (no interpreter, no shared libraries), so any Linux runs it:
  manylinux_2_17 (manylinux2014).
- macOS: arm64 or x86_64, linked only to the system's libSystem (gfortran's runtime static), tagged with the oldest
  macOS it was built for (its LC_BUILD_VERSION minos).
"""

from __future__ import annotations

import re
import struct
import subprocess
import sys
from pathlib import Path

# DLLs every Windows has (System32), besides the api-ms-win-* sets of the Universal C runtime
WINDOWS_DLLS = {"kernel32.dll", "kernelbase.dll", "ntdll.dll", "msvcrt.dll", "ucrtbase.dll", "user32.dll", "gdi32.dll",
                "advapi32.dll", "shell32.dll", "shlwapi.dll", "ole32.dll", "oleaut32.dll", "comdlg32.dll", "ws2_32.dll",
                "bcrypt.dll", "crypt32.dll", "secur32.dll", "rpcrt4.dll", "version.dll", "psapi.dll", "dbghelp.dll",
                "winmm.dll", "iphlpapi.dll", "shcore.dll", "userenv.dll"}


def imports(data: bytes, pe: int) -> set[str]:
    """The DLLs a PE32+ program imports, from its import table (not every DLL name in it: gfortran's static runtime
    names libgcc_s_seh-1.dll as text, looked for at run time, not imported)."""
    nsect, = struct.unpack_from("<H", data, pe + 6)
    opt_size, = struct.unpack_from("<H", data, pe + 20)
    opt = pe + 24
    if struct.unpack_from("<H", data, opt)[0] != 0x20B:
        raise SystemExit("not a PE32+ (64-bit) program")
    rva, size = struct.unpack_from("<II", data, opt + 112 + 8)          # data directory 1: imports
    sections = [struct.unpack_from("<IIII", data, opt + opt_size + 40 * i + 8) for i in range(nsect)]

    def off(r):
        for vsize, vaddr, rawsize, rawptr in sections:
            if vaddr <= r < vaddr + max(vsize, rawsize):
                return rawptr + r - vaddr
        raise SystemExit(f"address {r:#x} in no section")

    names, d = set(), off(rva) if size else None
    while d is not None:
        name_rva = struct.unpack_from("<I", data, d + 12)[0]
        if name_rva == 0:
            break
        o = off(name_rva)
        names.add(data[o:data.index(b"\0", o)].decode().lower())
        d += 20
    return names


def windows(data: bytes) -> str:
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise SystemExit("not a PE executable")
    machine = struct.unpack_from("<H", data, pe + 4)[0]
    if machine != 0x8664:
        raise SystemExit(f"PE machine {machine:#x}, not x86-64")
    names = imports(data, pe)
    other = sorted(n for n in names if not (n in WINDOWS_DLLS or n.startswith("api-ms-win-")))
    if other:
        raise SystemExit(f"needs DLLs a plain Windows doesn't have: {other}")
    return "win_amd64"


def linux(data: bytes) -> str:
    if data[4] != 2 or data[5] != 1:
        raise SystemExit("not a 64-bit little-endian ELF")
    machine = struct.unpack_from("<H", data, 18)[0]
    arch = {62: "x86_64", 183: "aarch64"}.get(machine)
    if arch is None:
        raise SystemExit(f"ELF machine {machine}")
    phoff, = struct.unpack_from("<Q", data, 32)
    phentsize, phnum = struct.unpack_from("<HH", data, 54)
    for i in range(phnum):
        kind = struct.unpack_from("<I", data, phoff + i * phentsize)[0]
        if kind in (2, 3):                                   # PT_DYNAMIC, PT_INTERP
            raise SystemExit("dynamically linked: a wheel needs it static (SWAT+'s CMake links -static on most Linux)")
    return f"manylinux_2_17_{arch}.manylinux2014_{arch}"


def macos(exe: Path) -> str:
    libs = subprocess.run(["otool", "-L", str(exe)], check=True, capture_output=True, text=True).stdout.splitlines()[1:]
    other = [line.split()[0] for line in libs if line.strip() and not line.strip().startswith("/usr/lib/libSystem")]
    if other:
        raise SystemExit(f"linked to libraries a plain Mac doesn't have: {other}")
    load = subprocess.run(["otool", "-l", str(exe)], check=True, capture_output=True, text=True).stdout
    m = re.search(r"cmd LC_BUILD_VERSION.*?minos (\d+)\.(\d+)", load, re.S)
    if not m:
        raise SystemExit("no LC_BUILD_VERSION: can't tell the oldest macOS it runs on")
    arch = subprocess.run(["lipo", "-archs", str(exe)], check=True, capture_output=True, text=True).stdout.split()
    if len(arch) != 1 or arch[0] not in ("arm64", "x86_64"):
        raise SystemExit(f"architectures {arch}")
    major, minor = int(m[1]), int(m[2])
    return f"macosx_{major}_{minor if major < 11 else 0}_{arch[0]}"


def tag(exe: Path) -> str:
    data = exe.read_bytes()
    if data[:2] == b"MZ":
        return windows(data)
    if data[:4] == b"\x7fELF":
        return linux(data)
    if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        return macos(exe)
    raise SystemExit(f"{exe}: not an executable this knows")


if __name__ == "__main__":
    print(tag(Path(sys.argv[1])))
