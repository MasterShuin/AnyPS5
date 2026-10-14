# About

Tool for automatic executables porting to Linux and Windows.

Includes a [relinker](core/relinker) that converts executable to the target system's native format and implementations of [system prx libraries](core/libs/prx) suitable for dynamic linking. No emulation or separate runtime process.

[Usage](docs/user/USAGE.md), [Build instructions](docs/dev/BUILD.md), [Architecture](docs/dev/ARCHITECTURE.md), [Technical debt of the project](docs/dev/TechnicalDebt.md), [code style conventions](docs/dev/CONVENTIONS.md), [contributing](CONTRIBUTING.md)

## Status

[![libraries](https://boykopovar.github.io/AnyPS5/badge-libraries.svg)](https://boykopovar.github.io/AnyPS5/) [![shaders](https://boykopovar.github.io/AnyPS5/badge-shaders.svg)](https://boykopovar.github.io/AnyPS5/)

[![progress map](https://boykopovar.github.io/AnyPS5/progress.svg)](https://boykopovar.github.io/AnyPS5/)

[![progress history](https://boykopovar.github.io/AnyPS5/history.svg)](https://boykopovar.github.io/AnyPS5/history.html)

<sub>* System libraries: percentage of the functions known to the project so far (declared in [core/libs/prx](core/libs/prx)), not of every PS5 system function. The total grows as more functions are declared.</sub>

Unsupported or unexpected states strictly throw `std::runtime_error`. `what()` is printed to stderr and the process terminates.

The [shader recompiler](core/shader/recompiler/Recompiler.cpp) successfully produces SPIR-V (validated via [Spirv-Tools](3rdparty/SPIRV-Tools) when built with `ANYPS5_ENABLE_SPIRV_TOOLS`).

## Compatibility

Per-game progress (stage reached, open blockers, libraries and GPU coverage) is tracked on the [blocker map](https://alxcbtw.github.io/anyps5-roadmap/), a community project built from public GitHub data ([source](https://github.com/AlxcBTW/anyps5-roadmap)). Test reports go in the [Game bring-up](https://github.com/boykopovar/AnyPS5/discussions/categories/game-bring-up) discussions, one thread per title.

## Input mapping

SDL-mapped game controllers are supported, including analog sticks and triggers. Keyboard and mouse controls can be configured with an `anyps5-input.ini` file. See [input mapping](docs/user/INPUT_MAPPING.md) for the supported devices and configuration format.

## Windowed configuration UI

The graphical front-end is built for non-technical users. Launch it with:

```text
python tools/ui_run.py
```

The main window has only what is needed: a **Scegli gioco** button (a file — `.pkg`/`.ffpfs`/`.ffpfsc`/`.exfat`/`.elf` — or a game folder with `eboot.bin`), the selected name, a **Converti gioco** button, a progress bar, and a final summary that is one of *Conversione completata*, *Conversione non riuscita*, or *Conversione completata, ma il gioco non è stato ancora verificato*. Two buttons follow the result: **Apri cartella risultato** and **Prova ad avviare il gioco** — the latter is enabled only when the import audit verifies the dependencies. Output goes to an `AnyPS5_Output` folder next to the input; the relinker, the native `libs/` and the output path are auto-detected, and every technical option (relinker path, libs path, GUI subsystem, copying app data, a detailed log) is hidden under an optional **Opzioni avanzate** section. The window is pure `tkinter` (standard library).

The conversion runs the full pipeline ([`tools/build_windows_game.py`](tools/build_windows_game.py)) in a worker thread — extraction, preparation, Windows relinking and the dependency audit — and never reports a generated PE or a started process as a playable game. See [USAGE.md](docs/user/USAGE.md#windows-conversion-pipeline).

You can also prefill the fields from an existing CLI invocation:

```text
python tools/ui_run.py input.pkg --executable eboot.bin --modules modules/
```

Importable API:

```python
from tools.ui_run import build_app, run_ui
```

`build_app()` returns a `tkinter.Tk` window; `run_ui(argv)` creates the window, optionally
prefills it from CLI arguments, and runs the event loop.

## Disclaimer

This project is intended for interoperability, research, preservation, and compatibility purposes. It does not include, distribute, or require copyrighted software, firmware, cryptographic keys, or proprietary libraries. Users are responsible for ensuring that any binaries used with this project are obtained and used in accordance with applicable laws and their respective license terms.

## License

This project is licensed under the GNU General Public License version 2 only.
