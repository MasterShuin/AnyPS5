# Relinker Analysis Report

## Build System`r`n- CMake build system`r`n- Targets: relinker.exe, libc.prx, libkernel.prx`r`n- Output: build/core/libs/libs/*.prx`r`n`r`n## Component Map`r`n- cli/ - Relinker CLI`r`n- codegen/ - Guest code generation`r`n- domain/ - Shared types`r`n- elfpatcher/ - ELF manipulation`r`n- relinker/src/pipeline/RelinkerPipeline.cpp - Main pipeline`r`n
`r`n## Import Resolution Pipeline`r`n1. `Relink()` reads ELF headers and program headers`r`n2. Executable segments are extracted`r`n3. Call sites are resolved using `CallSiteResolver` and `NidRefs` are processed`r`n4. Weak imports are handled`r`n5. Relocation entries are extracted`r`n
`r`n## Running the Build`r`n1. Open PowerShell as Administrator`r`n2. Navigate to repository: `cd C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5` `r`n3. Run: `python tools/build_windows_game.py -o AnyPS5_Output` `r`n
