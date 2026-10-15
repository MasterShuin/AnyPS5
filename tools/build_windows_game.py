#!/usr/bin/env python3
"""AnyPS5 Windows pipeline orchestrator.

Drives a PS5 title from a container / game folder / ELF to a Windows run
directory produced by the compiled relinker, then audits the result. The
pipeline is deliberately split into explicit, independently testable phases so
the GUI and the CLI share one implementation and so a failure in one phase
never lets a stale or half-written output be mistaken for a finished one.

Phases (see :class:`Phase`):

    input_validated -> prepared -> pe_generated -> libs_staged
        -> app0_staged -> audit_done -> deps_verified | deps_incomplete

``deps_verified`` means only what ``tools/import_audit.py`` proves: no import is
``absent`` and every needed library has a file. It does **not** mean the title
renders, accepts input or is playable. Launching is a separate, explicit step
and never implies the game reached a frame.

Standard library only. Console usage::

    python tools/build_windows_game.py <game> -o <outdir>

Importable API::

    from tools.build_windows_game import build, launch, PipelineConfig
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

# Make the sibling tool modules importable regardless of the working directory.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import prepare_run as _prepare  # noqa: E402
import ps5_container  # noqa: E402
import self_elf  # noqa: E402

ROOT = _HERE.parent

#: Default relinker binary, matching the Ninja layout produced by the build.
DEFAULT_RELINKER = ROOT / "build" / "core" / "relinker" / (
    "relinker.exe" if os.name == "nt" else "relinker"
)
#: Default directory the ``libs`` build target fills with native ``.prx`` files.
DEFAULT_LIBS = ROOT / "build" / "core" / "libs" / "libs"
#: The import auditor shipped with the project.
IMPORT_AUDIT = _HERE / "import_audit.py"

#: Runtime DLLs a self-built ``libs/`` needs on Windows (docs/user/USAGE.md).
MINGW_DEPENDENCY_DLLS = (
    "libgcc_s_seh-1.dll",
    "libstdc++-6.dll",
    "libwinpthread-1.dll",
)
#: System libraries without which nothing can start; used as a staging check.
ESSENTIAL_PRX = ("libc.prx", "libkernel.prx")
#: Bundled-module directory names the relinker and prepare_run recognise.
MODULE_DIRS = ("sce_module", "sce_modules", "prx")
#: Name of the generated Windows executable inside the output directory.
EXE_NAME = "game.exe"
#: Default system-library search path baked into the PE (``$ORIGIN`` = exe dir).
DEFAULT_RPATH = "$ORIGIN/libs"


class Phase(str, Enum):
    """Explicit pipeline states. Never collapse two of these into "ready"."""

    INPUT_VALIDATED = "input_validated"
    PREPARED = "prepared"
    PE_GENERATED = "pe_generated"
    LIBS_STAGED = "libs_staged"
    APP0_STAGED = "app0_staged"
    AUDIT_DONE = "audit_done"
    DEPS_VERIFIED = "deps_verified"
    DEPS_INCOMPLETE = "deps_incomplete"
    LAUNCH_REQUESTED = "launch_requested"
    PROCESS_STARTED = "process_started"
    ERROR = "error"


class PipelineError(RuntimeError):
    """Raised when a phase fails; carries the phase it failed in."""

    def __init__(self, phase: Phase, message: str):
        super().__init__(message)
        self.phase = phase


@dataclass
class StepRecord:
    """One completed phase, for the machine-readable report."""

    phase: str
    ok: bool
    detail: str = ""
    returncode: int | None = None


@dataclass
class PipelineConfig:
    """Everything the pipeline needs. Paths may contain spaces (handled)."""

    source: Path
    output: Path
    relinker: Path | None = None
    libs: Path | None = None
    windows_gui: bool = False
    rpath: str = DEFAULT_RPATH
    copy_app_data: bool = False
    keep_work: bool = False


@dataclass
class PipelineResult:
    """Outcome of :func:`build`. ``ready_to_launch`` is audit-backed only."""

    output: Path
    executable: Path
    reached: str
    ready_to_launch: bool
    steps: list[StepRecord] = field(default_factory=list)
    audit: dict | None = None
    log: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "output": str(self.output),
            "executable": str(self.executable),
            "reached": self.reached,
            "ready_to_launch": self.ready_to_launch,
            "error": self.error,
            "steps": [vars(step) for step in self.steps],
            "audit": self.audit,
        }


# ``_run`` is a module attribute on purpose: tests monkeypatch it to avoid
# spawning the real relinker / auditor while still exercising the pipeline.
Runner = Callable[[list], "subprocess.CompletedProcess"]


def _run(cmd: list, timeout: float | None = None) -> "subprocess.CompletedProcess":
    """Run *cmd* as an argument list (never ``shell=True``), capturing output."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def resolve_relinker(explicit: Path | None = None) -> Path:
    """Return the relinker binary path, or raise ``PipelineError``.

    Order: explicit argument, ``ANYPS5_RELINKER`` env var, the default Ninja
    location. The result must be an existing file.
    """
    candidates = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env = os.environ.get("ANYPS5_RELINKER")
    if env:
        candidates.append(Path(env))
    candidates.append(DEFAULT_RELINKER)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(c) for c in candidates)
    raise PipelineError(
        Phase.INPUT_VALIDATED,
        "relinker not found (searched: " + searched + "). Build it with "
        "'cmake --build build --target relinker' or pass --relinker.",
    )


def resolve_libs(explicit: Path | None = None) -> Path:
    """Return the native ``.prx`` library directory, or raise ``PipelineError``."""
    candidates = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env = os.environ.get("ANYPS5_LIBS")
    if env:
        candidates.append(Path(env))
    candidates.append(DEFAULT_LIBS)
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("*.prx")):
            return candidate
    searched = ", ".join(str(c) for c in candidates)
    raise PipelineError(
        Phase.INPUT_VALIDATED,
        "system libraries not found (searched: " + searched + "). Build them "
        "with 'cmake --build build --target libs' or pass --libs.",
    )


def _append_log(log: list, header: str, proc: "subprocess.CompletedProcess") -> None:
    log.append("$ " + " ".join(_quote(a) for a in getattr(proc, "args", [])))
    out = (proc.stdout or "").rstrip()
    err = (proc.stderr or "").rstrip()
    if out:
        log.append(out)
    if err:
        log.append("[stderr] " + err)
    log.append("[exit %s]" % proc.returncode)
    log.append("")


def _quote(arg: str) -> str:
    return '"' + arg + '"' if (" " in arg or "\t" in arg) else arg


def _module_dirs_present(directory: Path) -> list:
    return [directory / name for name in MODULE_DIRS if (directory / name).is_dir()]


def _clean_output(output: Path) -> None:
    """Remove artefacts this pipeline owns, so a stale run is never reused."""
    for pattern in ("*.registry.json", EXE_NAME):
        for item in output.glob(pattern):
            if item.is_file():
                item.unlink()
    for name in ("libs", "app0"):
        target = output / name
        if target.is_dir():
            shutil.rmtree(target)
    for name in ("conversion.log", "conversion_report.json", "audit.json"):
        item = output / name
        if item.is_file():
            item.unlink()


def prepare_phase(config: PipelineConfig, work: Path, log: list) -> tuple:
    """Extract (if needed) and stage eboot/sce_sys/modules.

    Returns ``(prepared_dir, game_folder)`` where *game_folder* is the plain
    game directory when known (used to seed ``app0/``) or ``None`` for a bare
    ELF. Uses ``prepare_run`` for staging so the two tools cannot drift.
    """
    prepared = work / "prepared"
    game_folder: Path | None = None
    suffix = ps5_container.detect_container(config.source)
    if suffix is not None:
        extracted = work / "extracted"
        extracted.mkdir(parents=True, exist_ok=True)
        ps5_container.extract_container(config.source, extracted)
        game_folder = extracted
        staged_from = extracted
    elif config.source.is_dir():
        game_folder = config.source
        staged_from = config.source
    else:
        staged_from = config.source
    try:
        _prepare.prepare_run(staged_from, prepared)
    except PipelineError:
        raise
    except (ps5_container.ContainerError, self_elf.SelfConversionError,
            FileNotFoundError, OSError, ValueError) as exc:
        # Any staging failure (an unreadable container, an encrypted SELF, a
        # missing executable) is reported as the prepare phase, never as an
        # unhandled traceback outside build()'s PipelineError handling.
        raise PipelineError(Phase.PREPARED, str(exc)) from exc
    if not (prepared / "eboot.bin").is_file():
        raise PipelineError(Phase.PREPARED, f"prepare produced no eboot.bin in {prepared}")
    return prepared, game_folder


def _drop_native_duplicates(prepared: Path, libs: Path, log: list) -> None:
    """Remove bundled modules AnyPS5 already provides as native libraries.

    A bundled system library (``libc.prx`` and friends) is redundant: the native
    build in *libs* is what the relinker resolves through the rpath, and it is
    the one Windows can actually load. Converting the bundled copy instead can
    abort the whole relink -- a system ``libc`` contains raw FreeBSD ``syscall``
    instructions the relinker refuses (``Forbidden syscall instruction``) -- even
    though the title only needs the symbols the native library already exports.
    Dropping the duplicate lets the relink succeed and defers any genuinely
    missing symbol to the import audit, instead of failing here.

    Only files whose *name* matches a native ``.prx`` are removed, and only from
    the temporary staging tree (never the user's source). A bundled module that
    AnyPS5 does not provide (a title's own library) is left untouched.
    """
    native = {item.name for item in libs.glob("*.prx")}
    if not native:
        return
    for module_dir in _module_dirs_present(prepared):
        for module in sorted(module_dir.iterdir()):
            if module.is_file() and module.name in native:
                module.unlink()
                log.append(
                    f"[relink] skipped bundled {module.name}: provided natively in {libs}"
                )


def relink_phase(
    config: PipelineConfig,
    relinker: Path,
    prepared: Path,
    output: Path,
    libs: Path,
    log: list,
    runner: Runner = _run,
) -> Path:
    """Invoke the relinker for Windows and return the generated executable."""
    eboot = prepared / "eboot.bin"
    executable = output / EXE_NAME
    _drop_native_duplicates(prepared, libs, log)
    # Only module directories that still hold a file need converting; an
    # emptied one (every bundled module was a native duplicate) is ignored.
    has_modules = any(any(d.iterdir()) for d in _module_dirs_present(prepared))
    cmd = [str(relinker)]
    if config.windows_gui:
        cmd.append("--windows-gui")
    cmd += ["--windows", "--registry", "--rpath", config.rpath]
    if not has_modules:
        # The relinker errors when no module directory holds a module; a bare
        # ELF or a title whose only bundled modules are native duplicates is
        # converted without them.
        cmd.append("--skip-sce-module")
    cmd += [str(eboot), str(executable)]
    try:
        proc = runner(cmd)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PipelineError(Phase.PE_GENERATED, f"could not run relinker: {exc}") from exc
    _append_log(log, "relink", proc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise PipelineError(Phase.PE_GENERATED, f"relinker failed: {detail}")
    if not executable.is_file():
        raise PipelineError(Phase.PE_GENERATED, f"relinker reported success but {executable} is missing")
    if executable.read_bytes()[:2] != b"MZ":
        raise PipelineError(Phase.PE_GENERATED, f"{executable} is not a PE (no MZ header)")
    return executable


def _copy_payload(src: Path, dst: Path, log: list) -> list:
    """Copy *src* children into *dst*, skipping eboot and bundled-module dirs.

    The relinker already wrote the *converted* modules under ``app0/sce_module``;
    copying the original PS5 module directories here would overwrite them with
    binaries Windows cannot load, so they are excluded on purpose.
    """
    copied = []
    skip = {"eboot.bin", *MODULE_DIRS}
    for item in sorted(src.iterdir()):
        if item.name in skip:
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        copied.append(item.name)
    if copied:
        log.append(f"[app0] staged from {src}: {', '.join(copied)}")
    return copied


def stage_libs_phase(config: PipelineConfig, libs: Path, output: Path, log: list) -> Path:
    """Copy the native system libraries (and MinGW runtime DLLs) into ``libs/``."""
    dst = output / "libs"
    dst.mkdir(parents=True, exist_ok=True)
    prx = sorted(libs.glob("*.prx"))
    if not prx:
        raise PipelineError(Phase.LIBS_STAGED, f"no *.prx files in {libs}")
    for item in prx:
        shutil.copy2(item, dst / item.name)
    dlls = [libs / name for name in MINGW_DEPENDENCY_DLLS if (libs / name).is_file()]
    for item in dlls:
        shutil.copy2(item, dst / item.name)
    missing_essential = [n for n in ESSENTIAL_PRX if not (dst / n).is_file()]
    if missing_essential:
        raise PipelineError(
            Phase.LIBS_STAGED,
            "essential system libraries missing from " + str(libs) + ": " + ", ".join(missing_essential),
        )
    if os.name == "nt" and len(dlls) != len(MINGW_DEPENDENCY_DLLS):
        log.append("[libs] warning: not all MinGW runtime DLLs were found beside the libraries")
    log.append(f"[libs] staged {len(prx)} .prx and {len(dlls)} runtime DLL(s) into {dst}")
    return dst


def stage_app0_phase(
    config: PipelineConfig, prepared: Path, game_folder: Path | None, output: Path, log: list
) -> Path:
    """Populate ``app0/`` with the game payload (sce_sys and, optionally, data)."""
    dst = output / "app0"
    dst.mkdir(parents=True, exist_ok=True)
    _copy_payload(prepared, dst, log)
    if config.copy_app_data and game_folder is not None:
        _copy_payload(game_folder, dst, log)
        log.append(f"[app0] copied full game data from {game_folder}")
    return dst


def audit_phase(
    config: PipelineConfig, libs: Path, output: Path, prepared: Path | None, log: list, runner: Runner = _run
) -> tuple:
    """Run ``import_audit.py`` and return ``(audit_dict, ready)``.

    Semantics verified against the auditor source: exit 0 = nothing blocks
    loading; exit 1 = ``absent`` imports or needed libraries without a file;
    exit 2 = an input could not be read. Only exit 0 sets ``ready``.

    ``--modules`` receives the *source* ELF modules staged under *prepared*,
    never the converted copies the relinker wrote to ``app0/sce_module``: those
    are PE (``*.guest.prx``) and the auditor reads ELF dynamic tables, so
    passing them fails with ``unsupported ELF version``. The file names must
    also match the registries' library names (``libcohtml.Prospero.prx``, with
    no ``.guest`` suffix), which only the originals satisfy.
    """
    registries = sorted(output.glob("*.registry.json"))
    if not registries:
        raise PipelineError(Phase.AUDIT_DONE, "no registry was produced by the relinker")
    cmd = [sys.executable, str(IMPORT_AUDIT), *[str(r) for r in registries], "--libs", str(libs)]
    for module_dir in _module_dirs_present(prepared) if prepared else []:
        if any(module_dir.iterdir()):
            cmd += ["--modules", str(module_dir)]
    audit_json = output / "audit.json"
    cmd += ["--json", str(audit_json)]
    try:
        proc = runner(cmd)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PipelineError(Phase.AUDIT_DONE, f"could not run import audit: {exc}") from exc
    _append_log(log, "audit", proc)
    if proc.returncode == 2:
        detail = (proc.stderr or proc.stdout or "").strip() or "exit 2"
        raise PipelineError(Phase.AUDIT_DONE, f"import audit could not read its inputs: {detail}")
    audit = {}
    if audit_json.is_file():
        try:
            audit = json.loads(audit_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            audit = {}
    ready = proc.returncode == 0
    return audit, ready


def build(config: PipelineConfig, runner: Runner = _run,
          progress_callback: Callable | None = None) -> PipelineResult:
    """Run the whole pipeline and return a :class:`PipelineResult`.

    On failure the result carries ``reached`` = the failed phase and ``error``
    = a human-readable message; ``conversion.log`` and ``conversion_report.json``
    are still written so the GUI/CLI can show what happened.

    *progress_callback*, when given, is called as ``progress_callback(phase,
    ok, detail)`` on each completed phase so a GUI can show live advancement;
    it runs on the caller's thread and its failures are swallowed.
    """
    log: list = []
    steps: list[StepRecord] = []
    result = PipelineResult(
        output=config.output,
        executable=config.output / EXE_NAME,
        reached=Phase.ERROR.value,
        ready_to_launch=False,
        steps=steps,
        log="",
    )
    work: Path | None = None

    def record(phase: Phase, ok: bool, detail: str = "", returncode: int | None = None) -> None:
        steps.append(StepRecord(phase.value, ok, detail, returncode))
        result.reached = phase.value
        if progress_callback is not None:
            # A UI progress hook must never abort the conversion: it runs on the
            # worker thread and its own marshalling errors are its problem.
            try:
                progress_callback(phase.value, ok, detail)
            except Exception:  # noqa: BLE001 - progress reporting is best-effort
                pass

    try:
        if not config.source.exists():
            raise PipelineError(Phase.INPUT_VALIDATED, f"input not found: {config.source}")
        src_res = config.source.resolve()
        out_res = config.output.resolve()
        if src_res == out_res or src_res in out_res.parents:
            raise PipelineError(Phase.INPUT_VALIDATED, "output directory must be outside the input")
        relinker = resolve_relinker(config.relinker)
        libs = resolve_libs(config.libs)
        config.output.mkdir(parents=True, exist_ok=True)
        record(Phase.INPUT_VALIDATED, True, f"relinker={relinker} libs={libs}")

        _clean_output(config.output)
        work = Path(tempfile.mkdtemp(prefix="anyps5-winbuild-"))
        prepared, game_folder = prepare_phase(config, work, log)
        record(Phase.PREPARED, True, str(prepared))

        executable = relink_phase(config, relinker, prepared, config.output, libs, log, runner)
        result.executable = executable
        record(Phase.PE_GENERATED, True, str(executable))

        stage_libs_phase(config, libs, config.output, log)
        record(Phase.LIBS_STAGED, True, str(config.output / "libs"))

        stage_app0_phase(config, prepared, game_folder, config.output, log)
        record(Phase.APP0_STAGED, True, str(config.output / "app0"))

        audit, ready = audit_phase(config, libs, config.output, prepared, log, runner)
        result.audit = audit
        record(Phase.AUDIT_DONE, True, "import audit completed")
        record(Phase.DEPS_VERIFIED if ready else Phase.DEPS_INCOMPLETE, ready,
               "no blocking imports" if ready else "absent imports or missing libraries remain")
        result.ready_to_launch = ready
    except PipelineError as exc:
        record(exc.phase, False, str(exc))
        result.error = str(exc)
    finally:
        result.log = "\n".join(log)
        _write_reports(config.output, result)
        if work is not None and not config.keep_work:
            shutil.rmtree(work, ignore_errors=True)
    return result


def _write_reports(output: Path, result: PipelineResult) -> None:
    try:
        (output / "conversion.log").write_text(result.log, encoding="utf-8")
        (output / "conversion_report.json").write_text(
            json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def launch(config: PipelineConfig, popen: Callable = subprocess.Popen):
    """Start the generated executable and return the process handle.

    This only proves the OS created the process; it does not wait for, or imply,
    the game reaching a frame. Prerequisites are checked by the caller.
    """
    executable = config.output / EXE_NAME
    if not executable.is_file():
        raise PipelineError(Phase.LAUNCH_REQUESTED, f"no executable at {executable}")
    if not (config.output / "libs").is_dir():
        raise PipelineError(Phase.LAUNCH_REQUESTED, f"no libs/ directory in {config.output}")
    return popen([str(executable)], cwd=str(config.output))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a PS5 title to a Windows run directory via the AnyPS5 relinker."
    )
    parser.add_argument("source", help="game folder, ELF, or .pkg/.ffpfs/.ffpfsc/.exfat container")
    parser.add_argument("-o", "--output", help="output directory (default: <source>_windows)")
    parser.add_argument("--relinker", help="path to relinker.exe (default: auto-detect)")
    parser.add_argument("--libs", help="directory of native .prx libraries (default: auto-detect)")
    parser.add_argument("--windows-gui", action="store_true", help="build a GUI-subsystem executable")
    parser.add_argument("--rpath", default=DEFAULT_RPATH, help=f"system library search path (default: {DEFAULT_RPATH})")
    parser.add_argument("--copy-app-data", action="store_true", help="also copy the full game data into app0/")
    parser.add_argument("--keep-work", action="store_true", help="keep the temporary staging directory")
    parser.add_argument("--launch", action="store_true", help="launch the executable after a verified conversion")
    return parser


def main(argv: list | None = None) -> int:
    args = _build_parser().parse_args(argv)
    source = Path(args.source).expanduser().resolve()
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else source.parent / (source.name + "_windows")
    )
    config = PipelineConfig(
        source=source,
        output=output,
        relinker=Path(args.relinker) if args.relinker else None,
        libs=Path(args.libs) if args.libs else None,
        windows_gui=args.windows_gui,
        rpath=args.rpath,
        copy_app_data=args.copy_app_data,
        keep_work=args.keep_work,
    )
    result = build(config)
    print(result.log)
    print(f"Reached phase: {result.reached}")
    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)
        return 2
    print(f"Windows output: {result.output}")
    print(f"Executable:     {result.executable}")
    print("Dependencies:   verified by import_audit (no blocking imports)."
          if result.ready_to_launch
          else "Dependencies:   INCOMPLETE — see audit.json; the game may not load.")
    if args.launch:
        if not result.ready_to_launch:
            print("Refusing to launch: dependencies are not verified.", file=sys.stderr)
            return 3
        proc = launch(config)
        print(f"Launched PID {proc.pid} (process start is not proof of a rendered frame).")
    return 0


if __name__ == "__main__":
    sys.exit(main())




