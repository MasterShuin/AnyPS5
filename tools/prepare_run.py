#!/usr/bin/env python3
"""Prepare a PS5 game for running under AnyPS5.

Recognised inputs (searched in order):

  1. PS5 package containers (.pkg, .ffpfs, .ffpfsc, .exfat).
     These are not ELF files, so they are converted to a plain game folder
     first by an external converter (extract_container in ps5_container.py).
  2. PS5 game folder, i.e. a directory containing eboot.bin (and normally
     sce_sys/), used directly as the source game.
  3. Standalone ELF executable, copied as-is.

The prepared run directory contains:

  eboot.bin                    the relinkable executable
  sce_sys/                     param.json and other payload files (game folders
                               and unpacked containers only)
  sce_module/ / sce_modules/ / prx/   bundled ELF modules, when found

Exit codes:
    0   success
    1   invalid arguments
    2   input could not be read or prepared
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

# Make the sibling converter module importable regardless of the working
# directory this script is invoked from.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from ps5_container import (  # noqa: E402
    ContainerError,
    detect_container,
    extract_container,
)
import self_elf  # noqa: E402

#: Bundled-module directories, in the order the relinker honours them.
_MODULE_DIRS = ("sce_module", "sce_modules", "prx")

#: File suffixes of bundled modules that some titles ship loose in the game
#: folder root (beside ``eboot.bin``) instead of inside a ``sce_module`` dir.
_MODULE_SUFFIXES = (".prx", ".sprx")

DEFAULT_EXECUTABLE = "eboot.bin"
DEFAULT_SOURCE = "eboot.bin"


class _Parser(argparse.ArgumentParser):
    """Argument parser whose error() exits with status 1."""

    def error(self, message: str) -> None:
        print(f"[prepare_run] error: {message}", file=sys.stderr)
        self.print_usage(file=sys.stderr)
        sys.exit(1)


def is_game_folder(path: Path) -> bool:
    """A game folder holds eboot.bin at its root (and normally sce_sys/)."""
    return path.is_dir() and (path / "eboot.bin").is_file()


def discover_module_dirs(source_root: Path) -> list[Path]:
    """Return the module directories found beside *source_root*."""
    root = source_root if source_root.is_dir() else source_root.parent
    return [root / name for name in _MODULE_DIRS if (root / name).is_dir()]


def stage_loose_root_modules(game_folder: Path, output: Path, verbose: bool) -> list[Path]:
    """Stage bundled modules that ship loose in the game folder root.

    Not every title keeps its bundled modules inside a ``sce_module`` (or
    ``sce_modules``/``prx``) directory: some ship third-party modules such as
    ``libcohtml.Prospero.prx`` or ``libfmod.prx`` as plain files beside
    ``eboot.bin``. Those are skipped by :func:`discover_module_dirs`, which
    looks only for the module *directories*, so the relinker never sees them
    and the generated ``game.exe`` fails to ``LoadLibrary`` them at runtime
    (``GetLastError 126``).

    Copy every loose ``*.prx``/``*.sprx`` from *game_folder* into the prepared
    ``sce_module`` tree, unwrapping SELF to plain ELF exactly like the modules
    found inside a module directory. A module that already exists in the
    staged ``sce_module`` (i.e. the title ships it both loose and in the
    directory) keeps the directory copy, which is the canonical location the
    relinker already honours. Returns the staged module paths.
    """
    root = game_folder if game_folder.is_dir() else game_folder.parent
    destination = output / _MODULE_DIRS[0]
    staged: list[Path] = []
    for module in sorted(root.iterdir()):
        if not module.is_file() or module.suffix.lower() not in _MODULE_SUFFIXES:
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / module.name
        if target.exists():
            # The directory copy wins; do not overwrite it with the loose file.
            continue
        shutil.copy2(module, target)
        if self_elf.convert_file(target) and verbose:
            print(f"[prepare_run] {module.name}: SELF container -> plain ELF")
        staged.append(target)
    if staged and verbose:
        print(f"[prepare_run] loose modules -> {destination}")
    return staged

def copy_executables_and_payload(
    executable_source: Path,
    game_folder: Path | None,
    output: Path,
    executable_name: str,
    verbose: bool,
    module_dirs: tuple[Path, ...] = (),
) -> None:
    """Copy the executable, its sce_sys payload and its modules into *output*.

    The copies are unwrapped from SELF to plain ELF afterwards: the relinker
    accepts a clean ELF only, while a PS5 title ships its executable and its
    bundled modules signed. Only the copies are rewritten -- the user's source
    game folder is never modified.
    """
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable_source, output / executable_name)
    if verbose:
        print(f"[prepare_run] executable -> {output / executable_name}")
    if self_elf.convert_file(output / executable_name) and verbose:
        print(f"[prepare_run] {executable_name}: SELF container -> plain ELF")
    if game_folder is not None:
        # Some titles ship bundled modules loose in the game folder root
        # instead of inside a module directory; stage them too so the
        # relinker converts them and game.exe can load them at runtime.
        stage_loose_root_modules(game_folder, output, verbose)



    if game_folder is not None:
        sys_src = game_folder / "sce_sys"
        if sys_src.is_dir():
            shutil.copytree(sys_src, output / "sce_sys", dirs_exist_ok=True)
            if verbose:
                print(f"[prepare_run] sce_sys      -> {output / 'sce_sys'}")

    sources = module_dirs if module_dirs else discover_module_dirs(
        game_folder or executable_source
    )
    for src_mod in sources:
        shutil.copytree(src_mod, output / src_mod.name, dirs_exist_ok=True)
        if verbose:
            print(f"[prepare_run] module dir -> {output / src_mod.name}")
        self_elf.convert_module_dir(output / src_mod.name, verbose)
    if verbose:
        if sources:
            print(f"[prepare_run] copied {len(sources)} module directory/ies")
        else:
            print("[prepare_run] no bundled modules found")

def prepare_run(
    input_path: Path,
    output: Path,
    executable_name: str = DEFAULT_EXECUTABLE,
    source: str = DEFAULT_SOURCE,
    module_dirs: tuple[Path, ...] = (),
    skip_modules: bool = False,
    verbose: bool = False,
) -> Path:
    """Convert (if needed) and prepare a PS5 run directory, returning it."""
    suffix = detect_container(input_path)

    tmp_dir: Path | None = None
    try:
        if suffix is not None:
            tmp_dir = Path(tempfile.mkdtemp(prefix="prepare_run_"))
            try:
                extract_container(input_path, tmp_dir)
            except ContainerError as exc:
                raise ContainerError(
                    f"container {input_path.name!r} could not be converted: {exc}"
                ) from exc
            game_folder = tmp_dir
            if verbose:
                print(f"[prepare_run] converted container {input_path.name} -> {tmp_dir}")
        elif input_path.is_dir() and is_game_folder(input_path):
            game_folder = input_path
        elif input_path.is_file():
            # Standalone executable: no game folder, no sce_sys payload.
            game_folder = None
        else:
            raise ValueError(
                f"not a game folder, ELF or container: {input_path}"
            )

        executable_source = (
            game_folder / source if game_folder is not None else input_path
        )
        if not executable_source.is_file():
            raise FileNotFoundError(
                f"executable {executable_source} not found (source: {source})"
            )

        output.mkdir(parents=True, exist_ok=True)
        copy_executables_and_payload(
            executable_source,
            game_folder,
            output,
            executable_name,
            verbose,
            module_dirs,
        )
        return output
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

def build_parser() -> _Parser:
    parser = _Parser(
        description=(
            "Prepare a PS5 game (container, folder or ELF) for running "
            "under AnyPS5."
        )
    )
    parser.add_argument(
        "input",
        help="game folder, ELF, or .pkg/.ffpfs/.ffpfsc/.exfat container",
    )
    parser.add_argument(
        "output",
        nargs="?",
        help="prepared run directory (default: <input>_run)",
    )
    parser.add_argument(
        "--executable",
        "-e",
        default=DEFAULT_EXECUTABLE,
        help="name of the executable inside the run directory "
        f"(default: {DEFAULT_EXECUTABLE})",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=(
            "relative path of the executable inside a game folder/container "
            f"(default: {DEFAULT_SOURCE})"
        ),
    )
    parser.add_argument(
        "--modules",
        action="append",
        default=[],
        metavar="DIR",
        help="directory of bundled ELF modules (repeatable)",
    )
    parser.add_argument(
        "--skip-modules",
        action="store_true",
        help="do not look for or copy bundled modules",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print verbose progress messages",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"[prepare_run] error: input not found: {input_path}", file=sys.stderr)
        return 2

    if args.output is None:
        output = input_path.parent / (input_path.name + "_run")
    else:
        output = Path(args.output).expanduser().resolve()

    module_dirs = tuple(Path(m) for m in args.modules) if args.modules else ()

    try:
        prepare_run(
            input_path,
            output,
            args.executable,
            args.source,
            module_dirs,
            args.skip_modules,
            args.verbose,
        )
    except (ContainerError, FileNotFoundError, ValueError) as exc:
        print(f"[prepare_run] error: {exc}", file=sys.stderr)
        return 2

    print(f"[prepare_run] prepared run in {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

