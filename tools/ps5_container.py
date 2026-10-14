"""External converters for PS5 package-container formats.

prepare_run.py accepts a PS5 game folder, a standalone ELF executable, or a
native PS5 package container (.pkg, .ffpfs, .ffpfsc, .exfat). Containers are
not ELF files, so they cannot be prepared for the relinker directly: they must
first be unpacked into a plain game folder. The unpacking is intentionally
delegated to an external converter (this module) instead of being baked into
prepare_run.py, so that:

  * prepare_run.py stays small and dependency-free (standard library only);
  * any external PS5 package tool can be dropped in without touching
    prepare_run.py, and
  * missing or broken converters fail with a clear, actionable error.

How a converter is chosen
-------------------------
The first converter below that is available is used:

  1. In-process access to the bundled ps5-ultrapack backend (if the package is
     importable). It is not importable from the repository tree as shipped
     (the directory is named with a hyphen, so Python import is impossible),
     hence the next fallback.
  2. The bundled ps5-ultrapack backend CLI, executed as a subprocess of the
     interpreter that runs this script.

An explicit external converter can be supplied through
:func:`extract_container` via the *converter* parameter when a project-local
tool replaces the bundled backend.

Supported container suffixes
----------------------------
.pkg, .ffpfs, .ffpfsc, .exfat
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SUPPORTED_CONTAINER_SUFFIXES = (".pkg", ".ffpfs", ".ffpfsc", ".exfat")

#: Reproductions of the backend CLI for the "unpack" operation. Tuple of
#: (label, cli_path_resolver).
_BUILTIN_CONVERTERS = (
    (
        "ps5-ultrapack backend CLI",
        lambda: _resolve_ps5_ultrapack_cli(),
    ),
)


class ContainerError(RuntimeError):
    """Raised when a supported container cannot be extracted."""


def detect_container(path: Path) -> str | None:
    """Return the matched container suffix for *path*, or ``None``.

    A path counts as a container only when it is an existing file whose
    lowercased suffix is one of :data:`SUPPORTED_CONTAINER_SUFFIXES`.
    """
    path = Path(path).resolve()
    if not path.is_file():
        return None
    suffix = path.suffix.lower()
    return suffix if suffix in SUPPORTED_CONTAINER_SUFFIXES else None


def _resolve_ps5_ultrapack_cli() -> Path | None:
    """Return the path of the bundled backend CLI, or ``None`` when absent."""
    candidate = (
        Path(__file__).resolve().parent.parent
        / "ps5-ultrapack"
        / "backend"
        / "cli.py"
    )
    return candidate if candidate.is_file() else None


def build_converter_command(converter, src, dest, extra=()):
    """Build the argv that runs the unpack converter.

    A ``.py`` backend is a Python script, not a Windows executable: launching it
    directly raises ``OSError [WinError 193]`` (``%1 is not a valid Win32
    application``). Such a converter is therefore started through the current
    interpreter; a native executable is run as-is. Arguments are always a list,
    so paths containing spaces or parentheses are passed verbatim.
    """
    src, dest = str(src), str(dest)
    program = str(converter)
    if program.lower().endswith(".py"):
        return [sys.executable, program, src, dest, "--unpack", *extra]
    return [program, src, dest, "--unpack", *extra]


def _unpack_failure_detail(proc) -> str:
    """Best human-readable reason for a failed converter run.

    The backend prints its ``[ERROR] ...`` diagnostics on *stdout* while
    *stderr* only carries MkPFS progress bars. Prefer the explicit error
    lines; otherwise report the tail of whichever stream produced output, so a
    failure never degrades to a bare exit code with the real reason lost.
    """
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    error_lines = [line for line in stdout.splitlines() if "[ERROR]" in line]
    if error_lines:
        return "\n".join(error_lines)
    for stream in (stdout, stderr):
        if stream:
            return "\n".join(stream.splitlines()[-15:])
    return f"exit code {proc.returncode}"


def extract_container(
    src: Path,
    dest: Path,
    converter: str = "auto",
    *,
    extra: tuple[str, ...] = (),
) -> Path:
    """Unpack *src* into *dest*, returning *dest* once it holds a game folder.

    *converter* chooses the external tool:

    * ``"auto"`` (default): use the first available built-in converter.
    * A path to a converter: a ``.py`` script is started through the current
      Python interpreter (never executed directly, which would fail on Windows
      with ``WinError 193``); a native executable is run as-is. In both cases
      the argv is ``[<program>, src, dest, "--unpack", "--overwrite"]``: every
      caller pre-creates *dest* (``build_windows_game`` mkdir's it and
      ``prepare_run`` must, via ``mkdtemp``), and the backend rejects an
      existing output folder unless ``--overwrite`` is given.

    Raises :class:`ContainerError` when the input is not a supported
    container or when every available converter fails.
    """
    src = Path(src).resolve()
    dest = Path(dest).resolve()

    if not src.is_file():
        raise ContainerError(f"container not found: {src}")
    suffix = detect_container(src)
    if suffix is None:
        raise ContainerError(
            f"unsupported container {src.name!r}; expected one of "
            f"{', '.join(SUPPORTED_CONTAINER_SUFFIXES)}"
        )

    dest.mkdir(parents=True, exist_ok=True)
    if converter == "auto":
        converter = next(
            (str(resolver()) for _label, resolver in _BUILTIN_CONVERTERS
             if resolver() is not None),
            "auto",
        )
    if converter == "auto":
        raise ContainerError(
            "no external converter available for container "
            f"{src.name!r} (expected one of {', '.join(SUPPORTED_CONTAINER_SUFFIXES)})"
        )
    # *dest* is always pre-created by the caller (and prepare_run must, via
    # mkdtemp), but the backend refuses to unpack into an existing folder
    # unless --overwrite is given; that flag is its documented remedy for the
    # "Output folder already exists" guard.
    cmd = build_converter_command(converter, src, dest, ("--overwrite", *extra))
    # Force UTF-8 in the child and decode its output as UTF-8: the converter is
    # a Python script whose stdout would otherwise use the locale code page
    # (cp1252 on an Italian Windows), turning non-ASCII paths or messages into
    # decode errors or mojibake.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace", env=env
        )
    except FileNotFoundError as exc:
        raise ContainerError(
            f"the container converter {cmd[0]!r} could not be started ({exc}); "
            "the bundled backend or Python may be missing"
        ) from exc
    except OSError as exc:
        # WinError 193 (%1 is not a valid Win32 application): a non-executable
        # such as a .py script was launched directly instead of via the interpreter.
        raise ContainerError(
            f"the container converter {cmd[0]!r} is not a runnable program here "
            f"({exc}); a .py backend must be started through the Python interpreter"
        ) from exc
    if proc.returncode != 0:
        message = _unpack_failure_detail(proc)
        raise ContainerError(
            f"extracting {src.name!r} with converter {cmd[0]!r} failed "
            f"(exit {proc.returncode}): {message}"
        )
    return dest


