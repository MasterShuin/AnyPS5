#!/usr/bin/env python3
"""Beginner-friendly Tkinter front-end for the AnyPS5 Windows pipeline.

A non-technical user picks a PS5 game (a ``.pkg``/``.ffpfs``/``.ffpfsc``/``.exfat``
container, a game folder containing ``eboot.bin``, or a standalone ELF), presses
**Converti gioco**, and the whole pipeline runs — extraction, preparation,
Windows relinking and the dependency audit — in a worker thread. The
**Solo ELF** button next to it only runs the preparation step (extraction +
SELF → ELF), without the Windows relink, for users who just want the plain
ELF file(s). Technical
options live in an optional **Opzioni avanzate** section.

The window never claims a game is playable: it distinguishes *PE generated*,
*dependencies verified* and *process started*, and only enables **Prova ad
avviare il gioco** when the import audit passed.

Standard library only.

GUI usage: ``python tools/ui_run.py``
Importable API: ``from tools.ui_run import build_app, run_ui``
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter
import tkinter.filedialog
import tkinter.messagebox
import tkinter.scrolledtext
import tkinter.ttk
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import build_windows_game as _bwg  # noqa: E402
import prepare_run as _prepare_mod  # noqa: E402
import ps5_container  # noqa: E402
from prepare_run import prepare_run as _prepare_run  # noqa: E402

#: Container formats the pipeline can extract (see ps5_container).
_CONTAINER_SUFFIXES = ps5_container.SUPPORTED_CONTAINER_SUFFIXES
#: Suffixes that are clearly not a game; rejected with guidance, no command run.
_NON_GAME_SUFFIXES = {
    ".iso", ".zip", ".rar", ".7z", ".gz", ".tar", ".txt", ".pdf", ".png", ".jpg",
    ".jpeg", ".mp4", ".mkv", ".avi", ".exe", ".dll", ".json", ".xml", ".csv", ".log",
}
#: Input kinds the conversion accepts.
_ACCEPTABLE_KINDS = ("container", "folder", "elf")
_INPUT_FILE_TYPES = [
    ("PS5 containers", " ".join(f"*{s}" for s in _CONTAINER_SUFFIXES)),
    ("PS5 ELF", "*.elf"),
    ("All files", "*.*"),
]
#: Default output folder created next to the input.
_OUTPUT_FOLDER_NAME = "AnyPS5_Output"


def describe_input(path) -> tuple:
    """Classify a user-selected path for the beginner UI.

    Returns ``(kind, message)``. ``kind`` is one of ``container`` (extraction
    needed), ``folder`` (game folder with ``eboot.bin``), ``elf`` (standalone
    executable), ``folder_no_eboot``, ``unsupported`` or ``missing``. Pure and
    side-effect free, so it is unit-testable without a display.
    """
    path = Path(path)
    if not path.exists():
        return "missing", "Il percorso selezionato non esiste."
    suffix = ps5_container.detect_container(path)
    if suffix is not None:
        return "container", f"Contenitore PS5 ({suffix}): verrà estratto automaticamente."
    if path.is_dir():
        if _prepare_mod.is_game_folder(path):
            return "folder", "Cartella di gioco con eboot.bin."
        return "folder_no_eboot", (
            "La cartella non contiene eboot.bin. Seleziona la cartella che lo "
            "contiene (di solito insieme a sce_sys)."
        )
    lowered = path.suffix.lower()
    if lowered in _NON_GAME_SUFFIXES:
        return "unsupported", (
            f"Formato non supportato: {path.suffix or path.name}. Scegli un "
            "contenitore .pkg/.ffpfs/.ffpfsc/.exfat, una cartella di gioco con "
            "eboot.bin, oppure un file eseguibile .elf."
        )
    return "elf", "Eseguibile autonomo: verrà convertito direttamente."


def default_output_dir(source) -> Path:
    """Return a sibling ``AnyPS5_Output`` folder next to *source*.

    Never inside the input, so the original game files are never modified.
    """
    return Path(source).resolve().parent / _OUTPUT_FOLDER_NAME


def default_elf_output_dir(source) -> Path:
    """Return a sibling ``AnyPS5_ELF`` folder next to *source*.

    Kept separate from :func:`default_output_dir` so an ELF-only run never
    mixes its files with a full Windows conversion of the same game.
    """
    return Path(source).resolve().parent / "AnyPS5_ELF"


def resolve_components(relinker=None, libs=None) -> tuple:
    """Resolve the relinker and native libraries via ``build_windows_game``.

    Returns ``(relinker_path, libs_path, problems)`` where *problems* is a list
    of human-readable messages for whatever could not be found.
    """
    problems = []
    try:
        relinker_path = _bwg.resolve_relinker(Path(relinker) if relinker else None)
    except _bwg.PipelineError as exc:
        relinker_path = None
        problems.append(str(exc))
    try:
        libs_path = _bwg.resolve_libs(Path(libs) if libs else None)
    except _bwg.PipelineError as exc:
        libs_path = None
        problems.append(str(exc))
    return relinker_path, libs_path, problems


def classify_outcome(result, exc) -> dict:
    """Map a ``build()`` result/exception to a beginner summary and states.

    ``kind`` is ``ok`` (deps verified), ``warn`` (PE generated but not verified)
    or ``error`` (failed). Never conflates a generated PE with a playable game.
    """
    if exc is not None or result is None:
        detail = str(exc) if exc else "Errore sconosciuto."
        return {"kind": "error", "summary": "Conversione non riuscita",
                "detail": detail, "can_open": False, "can_launch": False}
    if result.error:
        return {"kind": "error", "summary": "Conversione non riuscita",
                "detail": result.error, "can_open": Path(result.output).is_dir(),
                "can_launch": False}
    if result.ready_to_launch and result.reached == _bwg.Phase.DEPS_VERIFIED.value:
        return {"kind": "ok", "summary": "Conversione completata",
                "detail": "Eseguibile e librerie pronti; dipendenze verificate dall'audit.",
                "can_open": True, "can_launch": True}
    return {
        "kind": "warn",
        "summary": "Conversione completata, ma il gioco non è stato ancora verificato",
        "detail": ("L'eseguibile Windows è stato generato, ma alcune dipendenze "
                   "risultano mancanti o non verificate (vedi audit.json)."),
        "can_open": True, "can_launch": False,
    }


#: Human-readable label for each pipeline phase, shown as the conversion runs.
_PHASE_LABELS = {
    "input_validated": "Controllo dei prerequisiti",
    "prepared": "Preparazione dei file (estrazione, SELF → ELF)",
    "pe_generated": "Conversione in eseguibile Windows (relink)",
    "libs_staged": "Copia delle librerie di sistema",
    "app0_staged": "Copia dei dati di gioco",
    "audit_done": "Verifica delle dipendenze (audit)",
    "exports_patched": "Aggiunta delle esportazioni mancanti e re-audit",
    "deps_verified": "Dipendenze verificate",
    "deps_incomplete": "Dipendenze incomplete",
    "error": "Errore",
}


def phase_label(phase: str, ok: bool) -> str:
    """Return the beginner-facing line for a completed pipeline phase."""
    name = _PHASE_LABELS.get(phase, phase)
    mark = "✔" if ok else "✖"
    return f"{mark} {name}"


def build_report(result, outcome: dict) -> str:
    """Render a copyable, plain-text conversion report.

    Includes the phase-by-phase outcome, the beginner summary, the import-audit
    counts (when present) and the full pipeline log, so the whole run can be
    pasted into a bug report. Pure and testable without a display.
    """
    lines = ["=== Resoconto conversione AnyPS5 ===", ""]
    lines.append(f"Esito: {outcome['summary']}")
    lines.append(outcome["detail"])
    lines.append("")

    if result is not None:
        lines.append("Fasi:")
        steps = getattr(result, "steps", []) or []
        for step in steps:
            mark = "OK  " if step.ok else "FAIL"
            detail = f" — {step.detail}" if step.detail else ""
            lines.append(f"  [{mark}] {step.phase}{detail}")
        if not steps:
            lines.append("  (nessuna fase completata)")
        lines.append(f"Fase raggiunta: {result.reached}")

        audit = getattr(result, "audit", None)
        if audit:
            counts = audit.get("unique_by_class", {}) or {}
            implemented = counts.get("implemented", 0)
            stub = counts.get("stub", 0)
            absent = counts.get("absent", 0)
            lines.append("")
            lines.append("Audit import:")
            lines.append(f"  implementate: {implemented}   stub: {stub}   assenti: {absent}")
            missing = audit.get("missing_libraries") or []
            if missing:
                lines.append("  librerie senza file: " + ", ".join(
                    str(m.get("library", m)) for m in missing))
        exe = getattr(result, "executable", None)
        if exe:
            lines.append("")
            lines.append(f"Eseguibile: {exe}")

    log = getattr(result, "log", "") if result is not None else ""
    lines.append("")
    lines.append("=== Registro (log) ===")
    lines.append(log.strip() or "(vuoto)")
    return "\n".join(lines)


class RunConfigApp(tkinter.Tk):
    """The main window: pick a game, convert it, open or try to launch it."""

    def __init__(self):
        super().__init__()
        self.title("AnyPS5 — Converti un gioco PS5 per Windows")
        self.geometry("560x600")
        self.minsize(520, 560)
        self._busy = False
        self._source = None
        self._input_kind = None
        self._input_message = ""
        self._win_config = None
        self._win_result = None
        self._win_report = ""
        self._elf_output = None
        self._palette = {
            "info": "#a9b3c2", "ok": "#3fb950", "warn": "#d29922",
            "error": "#f85149", "idle": "#8b949e",
        }
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()

    # ------------------------------------------------------------------ UI -- #
    def _build(self):
        pad = {"padx": 16, "pady": 8}
        self._pad = pad  # kept so later handlers (e.g. the report) can re-pack
        body = tkinter.Frame(self)
        body.pack(fill="both", expand=True)

        tkinter.Label(body, text="Converti un gioco PS5 per Windows",
                      font=("", 14, "bold")).pack(anchor="w", **pad)
        tkinter.Label(body, wraplength=500, justify="left", foreground="#a9b3c2",
                      text="Scegli il gioco e premi «Converti gioco» per Windows, "
                           "oppure «Solo ELF» per ottenere solo i file ELF (SELF → ELF). Le opzioni "
                           "avanzate sono facoltative.").pack(anchor="w", **pad)

        self.choose_btn = tkinter.Button(body, text="📁  Scegli gioco",
                                         command=self._choose_game, height=2)
        self.choose_btn.pack(fill="x", **pad)

        self.selection_var = tkinter.StringVar(value="Nessun gioco selezionato")
        tkinter.Label(body, textvariable=self.selection_var, anchor="w",
                      font=("", 10, "bold")).pack(fill="x", **pad)

        self.convert_btn = tkinter.Button(body, text="▶  Converti gioco",
                                          command=self._on_convert, height=2, state="disabled")
        self.convert_btn.pack(fill="x", **pad)

        self.elf_btn = tkinter.Button(body, text="📦  Solo ELF (SELF → ELF, senza relink)",
                                      command=self._on_prepare_only, height=2, state="disabled")
        self.elf_btn.pack(fill="x", **pad)


        self.progress = tkinter.ttk.Progressbar(body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", **pad)

        self.phase_var = tkinter.StringVar(value="")
        self.phase_label = tkinter.Label(body, textvariable=self.phase_var, anchor="w",
                                         wraplength=500, justify="left", foreground="#8be0a4")
        self.phase_label.pack(fill="x", **pad)

        self.status_var = tkinter.StringVar(value="Pronto.")
        self.status_label = tkinter.Label(body, textvariable=self.status_var, anchor="w",
                                          wraplength=500, justify="left", foreground="#a9b3c2")
        self.status_label.pack(fill="x", **pad)

        self.summary_var = tkinter.StringVar(value="")
        self.summary_label = tkinter.Label(body, textvariable=self.summary_var, anchor="w",
                                           font=("", 12, "bold"), wraplength=500, justify="left")
        self.summary_label.pack(fill="x", **pad)

        row = tkinter.Frame(body)
        row.pack(fill="x", **pad)
        self.open_btn = tkinter.Button(row, text="📂  Apri cartella risultato",
                                       command=self._on_open_output, state="disabled")
        self.open_btn.pack(side="left", fill="x", expand=True)
        self.launch_btn = tkinter.Button(row, text="🎮  Prova ad avviare il gioco",
                                         command=self._on_launch, state="disabled")
        self.launch_btn.pack(side="left", fill="x", expand=True, padx=(8, 0))

        copy_row = tkinter.Frame(body)
        copy_row.pack(fill="x", **pad)
        self.copy_log_btn = tkinter.Button(copy_row, text="📋  Copia log",
                                           command=self._copy_log, state="disabled")
        self.copy_log_btn.pack(side="left", fill="x", expand=True)
        self.copy_report_btn = tkinter.Button(copy_row, text="📋  Copia resoconto",
                                              command=self._copy_report, state="disabled")
        self.copy_report_btn.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.report_box = tkinter.scrolledtext.ScrolledText(
            body, height=10, wrap="word", state="disabled",
            font=("Consolas", 9), foreground="#c9d3e0", background="#0d1117")
        # Not packed yet: shown only after a run, so the window stays compact.

        self.adv_toggle = tkinter.Button(body, text="⚙  Opzioni avanzate",
                                         command=self._toggle_advanced)
        self.adv_toggle.pack(fill="x", **pad)

        self.adv_frame = tkinter.Frame(body)
        self._adv_visible = False
        self._build_advanced(self.adv_frame)

    def _build_advanced(self, parent):
        for label, attr, default in (
            ("Cartella di output", "output", ""),
            ("relinker.exe", "relinker", str(_bwg.DEFAULT_RELINKER)),
            ("libs/ (.prx)", "libs", str(_bwg.DEFAULT_LIBS)),
        ):
            tkinter.Label(parent, text=label, anchor="w").pack(anchor="w")
            var = tkinter.StringVar(value=default)
            setattr(self, f"adv_{attr}_var", var)
            tkinter.Entry(parent, textvariable=var).pack(fill="x")

        self.adv_gui_var = tkinter.BooleanVar(value=False)
        tkinter.Checkbutton(parent, text="Sottosistema GUI (nessuna finestra console)",
                            variable=self.adv_gui_var, anchor="w").pack(anchor="w")
        self.adv_copy_var = tkinter.BooleanVar(value=False)
        tkinter.Checkbutton(parent, text="Copia tutti i dati di gioco in app0/",
                            variable=self.adv_copy_var, anchor="w").pack(anchor="w")

        tkinter.Button(parent, text="Solo preparazione (usa la cartella di output)",
                       command=self._on_prepare_only).pack(fill="x", pady=(6, 0))
        tkinter.Label(parent, text="Registro (log)", anchor="w").pack(anchor="w", pady=(6, 0))
        self.adv_log = tkinter.scrolledtext.ScrolledText(parent, height=8, wrap="word", state="disabled")
        self.adv_log.pack(fill="both", expand=True)

    def _toggle_advanced(self):
        if self._adv_visible:
            self.adv_frame.pack_forget()
            self.adv_toggle.config(text="⚙  Opzioni avanzate")
        else:
            self.adv_frame.pack(fill="both", expand=True, padx=16, pady=8)
            self.adv_toggle.config(text="⚙  Nascondi opzioni avanzate")
        self._adv_visible = not self._adv_visible

    # ----------------------------------------------------------- selecting -- #
    def _choose_game(self):
        dialog = tkinter.Toplevel(self)
        dialog.title("Che cos'è il tuo gioco?")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        tkinter.Label(dialog, text="Il tuo gioco PS5 è…", font=("", 11)).pack(padx=18, pady=(16, 8))
        tkinter.Button(dialog, text="Un file  (.pkg / .ffpfsc / .elf)", height=2,
                       command=lambda: self._close_dialog_and(dialog, self._pick_file)
                       ).pack(fill="x", padx=18, pady=4)
        tkinter.Button(dialog, text="Una cartella del gioco", height=2,
                       command=lambda: self._close_dialog_and(dialog, self._pick_folder)
                       ).pack(fill="x", padx=18, pady=(4, 16))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

    @staticmethod
    def _close_dialog_and(dialog, action):
        dialog.grab_release()
        dialog.destroy()
        action()

    def _pick_file(self):
        value = tkinter.filedialog.askopenfilename(
            title="Seleziona il gioco PS5", filetypes=_INPUT_FILE_TYPES)
        if value:
            self._set_selection(value)

    def _pick_folder(self):
        value = tkinter.filedialog.askdirectory(title="Seleziona la cartella del gioco")
        if value:
            self._set_selection(value)

    def _set_selection(self, path):
        self._source = Path(path)
        kind, message = describe_input(self._source)
        self._input_kind = kind
        self._input_message = message
        self.selection_var.set(self._source.name)
        self.adv_output_var.set(str(default_output_dir(self._source)))
        self._set_summary("", "idle")
        self._set_status(message, "error" if kind in ("missing", "unsupported", "folder_no_eboot") else "info")
        self.convert_btn.config(state="normal" if kind in _ACCEPTABLE_KINDS else "disabled")
        self.elf_btn.config(state="normal" if kind in _ACCEPTABLE_KINDS else "disabled")
        self.open_btn.config(state="disabled")
        self.launch_btn.config(state="disabled")
        self.copy_log_btn.config(state="disabled")
        self.copy_report_btn.config(state="disabled")
        self.report_box.pack_forget()
        self.phase_var.set("")
        self._win_result = None
        self._elf_output = None

    # -------------------------------------------------------------- helpers -- #
    def _set_status(self, text, kind="info"):
        self.status_var.set(text)
        self.status_label.config(foreground=self._palette.get(kind, self._palette["info"]))

    def _set_summary(self, text, kind="idle"):
        self.summary_var.set(text)
        self.summary_label.config(foreground=self._palette.get(kind, self._palette["idle"]))

    def _append_log(self, text):
        self.adv_log.config(state="normal")
        self.adv_log.insert("end", text)
        self.adv_log.see("end")
        self.adv_log.config(state="disabled")

    def _adv_relinker(self):
        value = self.adv_relinker_var.get().strip()
        return value or None

    def _adv_libs(self):
        value = self.adv_libs_var.get().strip()
        return value or None

    # ----------------------------------------------------------- converting -- #
    def _on_convert(self):
        if self._busy:
            return
        if not self._source or self._input_kind not in _ACCEPTABLE_KINDS:
            self._set_status("Seleziona prima un gioco valido.", "error")
            return
        relinker, libs, problems = resolve_components(self._adv_relinker(), self._adv_libs())
        if problems:
            self._set_summary("Conversione non riuscita", "error")
            self._set_status("Prerequisiti mancanti: " + problems[0], "error")
            tkinter.messagebox.showerror("Prerequisiti mancanti", "\n\n".join(problems))
            return
        output = Path(self.adv_output_var.get().strip() or default_output_dir(self._source))
        self._win_config = _bwg.PipelineConfig(
            source=self._source, output=output, relinker=relinker, libs=libs,
            windows_gui=self.adv_gui_var.get(), copy_app_data=self.adv_copy_var.get(),
        )
        self._busy = True
        self.choose_btn.config(state="disabled")
        self.convert_btn.config(state="disabled")
        self.elf_btn.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.launch_btn.config(state="disabled")
        self._set_summary("", "idle")
        self._set_status("Conversione in corso…")
        self.progress.stop()
        self.progress.config(mode="determinate", value=0, maximum=100)
        self.phase_var.set("")
        self.report_box.config(state="normal")
        self.report_box.delete("1.0", "end")
        self.report_box.config(state="disabled")
        self.report_box.pack_forget()
        self.adv_log.config(state="normal")
        self.adv_log.delete("1.0", "end")
        self.adv_log.config(state="disabled")
        self._win_report = ""
        self.copy_log_btn.config(state="disabled")
        self.copy_report_btn.config(state="disabled")
        threading.Thread(target=self._convert_worker, daemon=True).start()

    def _on_phase(self, phase, ok, detail):
        """UI-thread handler for a completed phase (dispatched via ``self.after``)."""
        self.phase_var.set(phase_label(phase, ok))
        # Deterministic advancement across the ordered phases the build drives.
        order = ["input_validated", "prepared", "pe_generated",
                 "libs_staged", "app0_staged", "audit_done", "exports_patched"]
        if phase in order:
            self.progress.config(value=(order.index(phase) + 1) * 100 // len(order))
        line = phase_label(phase, ok)
        self._append_log(line + ("\n" if ok else f" — {detail}\n"))

    def _convert_worker(self):
        # ``build`` runs the phases and calls this hook on the worker thread as
        # each one completes; ``after`` marshals it onto the Tk thread.
        def report_progress(phase, ok, detail):
            self.after(0, self._on_phase, phase, ok, detail)

        try:
            result = _bwg.build(self._win_config, progress_callback=report_progress)
        except Exception as exc:  # noqa: BLE001 - report to the UI thread
            self.after(0, self._convert_done, None, exc)
            return
        self.after(0, self._convert_done, result)

    def _convert_done(self, result=None, exc=None):
        self._busy = False
        self.progress.stop()
        self.progress.config(mode="determinate", value=100, maximum=100)
        self.choose_btn.config(state="normal")
        self.convert_btn.config(state="normal")
        self.elf_btn.config(state="normal")
        outcome = classify_outcome(result, exc)
        if result is not None and result.log:
            self._append_log(result.log + "\n")
        self._set_summary(outcome["summary"], outcome["kind"])
        self._set_status(outcome["detail"], outcome["kind"])
        self._win_result = result
        self.open_btn.config(state="normal" if outcome["can_open"] else "disabled")
        self.launch_btn.config(state="normal" if outcome["can_launch"] else "disabled")
        # A copyable report + log are useful even on failure, so build and
        # enable the copy buttons regardless of the outcome.
        self._win_report = build_report(result, outcome)
        self.report_box.config(state="normal")
        self.report_box.delete("1.0", "end")
        self.report_box.insert("end", self._win_report)
        self.report_box.config(state="disabled")
        self.report_box.pack(fill="both", expand=False, **self._pad)
        self.copy_log_btn.config(state="normal")
        self.copy_report_btn.config(state="normal")
        if outcome["kind"] == "error":
            tkinter.messagebox.showerror("Conversione non riuscita", outcome["detail"] or "Errore.")

    def _copy_log(self):
        """Copy the full pipeline log to the clipboard."""
        text = self.adv_log.get("1.0", "end").strip()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Registro copiato negli appunti.", "info")

    def _copy_report(self):
        """Copy the plain-text conversion report to the clipboard."""
        text = getattr(self, "_win_report", "") or self.report_box.get("1.0", "end").strip()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Resoconto copiato negli appunti.", "info")


    # ------------------------------------------------------- open and launch -- #
    def _on_open_output(self):
        output = self._win_config.output if self._win_config else None
        if output is None and getattr(self, "_elf_output", None):
            output = self._elf_output
        if not output or not Path(output).is_dir():
            self._set_status("Nessuna cartella di risultato da aprire.", "error")
            return
        try:
            if os.name == "nt":
                os.startfile(str(output))  # noqa: S606 - open the folder in Explorer
            elif sys.platform == "darwin":
                subprocess.run(["open", str(output)], check=False)
            else:
                subprocess.run(["xdg-open", str(output)], check=False)
        except OSError as exc:
            tkinter.messagebox.showerror("Impossibile aprire la cartella", str(exc))

    def _on_launch(self):
        if not self._win_result or not self._win_result.ready_to_launch:
            return
        try:
            process = _bwg.launch(self._win_config)
        except _bwg.PipelineError as exc:
            self._set_status(f"Avvio non riuscito: {exc}", "error")
            tkinter.messagebox.showerror("Avvio non riuscito", str(exc))
            return
        message = (f"Processo avviato (PID {process.pid}).\n\n"
                   "L'avvio del programma non garantisce che il gioco abbia "
                   "effettivamente raggiunto un frame renderizzato.")
        self._set_status(f"Gioco avviato (PID {process.pid}); verifica la finestra di gioco.", "ok")
        tkinter.messagebox.showinfo("Avvio", message)

    # ------------------------------------------------ prepare-only (advanced) -- #
    def _on_prepare_only(self):
        """Run only the preparation step (extraction + SELF → ELF).

        This is the user-facing “Solo ELF” action: it needs no relinker and no
        native libs, and leaves a run directory of plain ELF files in a
        dedicated ``AnyPS5_ELF`` sibling folder.
        """
        if self._busy:
            return
        if not self._source or self._input_kind not in _ACCEPTABLE_KINDS:
            self._set_status("Seleziona prima un gioco valido.", "error")
            return
        self._busy = True
        self.choose_btn.config(state="disabled")
        self.convert_btn.config(state="disabled")
        self.elf_btn.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.launch_btn.config(state="disabled")
        self._set_summary("", "idle")
        self._set_status("Conversione in ELF in corso… (estrazione + SELF → ELF)")
        self.phase_var.set("✔ Preparazione dei file (estrazione, SELF → ELF)")
        self.progress.start(12)
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        # An advanced custom output still wins; otherwise the ELF-only run
        # goes to its own sibling folder, never mixed with a full conversion.
        raw = self.adv_output_var.get().strip() if self.adv_output_var.get() else ""
        if raw and Path(raw) != default_output_dir(self._source):
            output = Path(raw)
        else:
            output = default_elf_output_dir(self._source)
        try:
            out = _prepare_run(self._source, output)
        except Exception as exc:  # noqa: BLE001 - report to the UI thread
            self.after(0, self._finish, None, exc)
            return
        self.after(0, self._finish, out)

    def _finish(self, out=None, exc=None):
        self._busy = False
        self.progress.stop()
        self.progress.config(mode="determinate", value=100, maximum=100)
        self.choose_btn.config(state="normal")
        self.convert_btn.config(state="normal")
        self.elf_btn.config(state="normal")
        if exc is not None:
            message = str(exc) or exc.__class__.__name__
            self._set_summary("Conversione ELF non riuscita", "error")
            self._set_status(f"Conversione ELF non riuscita: {message}", "error")
            tkinter.messagebox.showerror("Conversione ELF non riuscita", message)
            return
        self._elf_output = out
        self._set_summary("Conversione ELF completata", "ok")
        self._set_status(f"File ELF pronti in {out}", "ok")
        self.phase_var.set("✔ Preparazione dei file (estrazione, SELF → ELF)")
        self.open_btn.config(state="normal" if Path(out).is_dir() else "disabled")

    def _on_close(self):
        if self._busy and not tkinter.messagebox.askyesno(
                "Operazione in corso", "Un'operazione è ancora in corso. Chiudere comunque?"):
            return
        self.destroy()


def build_app(parent=None):
    """Build and return the main window.

    ``parent`` is kept for API compatibility and ignored: the window owns the
    ``Tk`` root itself. Call :func:`run_ui` to launch, or ``mainloop()`` after.
    """
    del parent
    return RunConfigApp()


def run_ui(argv=None):
    """Entry point: optionally prefill the selection from *argv*, then run."""
    app = RunConfigApp()
    if argv:
        parser = _prepare_mod.build_parser()
        args = parser.parse_args(argv)
        if args.input:
            app._set_selection(str(Path(args.input).expanduser().resolve()))
        if args.output:
            app.adv_output_var.set(str(Path(args.output).expanduser().resolve()))
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run_ui(sys.argv[1:]))




