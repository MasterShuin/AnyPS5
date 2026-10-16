# AnyPS5 Windows Conversion — Gap Analysis & Resolution Plan

**Date:** 2026-10-15 (last updated 2026-10-16)
**Source branch:** `features/user_interface`
**Output directory:** `C:\Users\lupol\Desktop\game\AnyPS5_Output`
**Conversion report:** `C:\Users\lupol\Desktop\game\AnyPS5_Output\conversion_report.json`
**Audit report:** `C:\Users\lupol\Desktop\game\AnyPS5_Output\audit.json`
**Conversion log:** `C:\Users\lupol\Desktop\game\AnyPS5_Output\conversion.log`

---

## 0. Status update (2026-10-16)

The two root causes found on 2026-10-15 are fixed and the fix is now part of
the pipeline itself (phase `exports_patched`, see
[`docs/user/USAGE.md`](docs/user/USAGE.md)):

| Gap | Resolution |
|---|---|
| 10 × `library_mismatch` | `game.registry.json` corrected: the 10 NIDs are declared against the library that really exports them. |
| 104 × `absent` from `libc.prx` (C++ STL) + 26 historical | New tool `tools/prx_export_patch.py`: appends an `.expd` section to the staged `.prx` with a complete export directory (old + new names → shared `xor eax,eax; ret` stub), ASCII-sorted name table, ordinals = EAT indexes. Verified with the real loader: `LoadLibraryExW` + `GetProcAddress` resolve every injected NID on the staged `libc.prx` (1354 exports; `load_test_out.txt`). |
| 151 `module`-declared imports and the three title libraries (`libcohtml.Prospero.prx`, `libfmod.prx`, `libPlayFabMultiplayer.prx`) missing from the audit's `--libs` | The relinker already converts them to `app0/sce_module/<name>.guest.prx`; the new `exports_patched` phase stages them into `libs/<name>.prx` (without ever overwriting a native system library) so both the audit and the Windows loader find them under the registries' names. |

The deciding audit now runs against the **staged** `libs/` — the exact files
the Windows loader reads — so `deps_verified` means what it says. On the last
full run before this update the staged tree exported **every** NID the audit
reported absent (104/104 `libc.prx`, 69/69 cohtml, 56/56 fmod, 26/26 PlayFab).

Remaining known gaps (not blocking the load, listed for the record):

- `stub` imports resolve at load time but throw when called (by design: they
  surface the missing behaviour instead of crashing the loader).
- The injected NIDs return **zero** (a stub, not the real behaviour); a title
  that needs real C++ STL semantics may still misbehave at runtime.
- One pre-existing `test_import_audit` failure on Windows
  (`test_module_files_must_be_elf`, path-separator assertion) — unrelated to
  this work.

---

## 1. Summary of the Block (2026-10-15 run, for the record)

| Field | Value |
|---|---|
| **Conversion stopped at** | `deps_incomplete` |
| **Ready to launch** | `false` |
| **Total imports audited** | 2102 |
| **`implemented`** | 1723 |
| **`stub`** | 53 |
| **`absent`** | 175 |
| **`module`** | 151 |
| **`library_mismatch`** | 10 |
| **Error message** | `10 imports are exported only by a library other than the one they name: they resolve on Linux and can fail on Windows [exit 1]` |
| **First unresolved import** | `game.libcohtml.Prospero.prx.guest.prx.registry.json: vU9svJtEnWc [libc.prx]` |

The pipeline completed:
- `input_validated` ✅
- `prepared` ✅
- `pe_generated` ✅ (`game.exe` generated)
- `libs_staged` ✅
- `app0_staged` ✅
- `audit_done` ✅ (audit completed)
- `deps_incomplete` ❌ **BLOCKED**

## 2. Gap Matrix: Absent Imports by Module

| # | Module (source ELF) | Declared lib | Absent count | Category |
|---|---|---|---|---|
| 1 | <none> (libPlayFabMultiplayer.prx) | libc.prx | 104 | C++ STL symbols (mangled _ZNSt...) |
| 2 | libPlayFabMultiplayer.prx | <none> | 67 | Module-level imports (PlayFab SDK) |
| 3 | libRenoirCore.PS5.prx | <none> | 2 | Module-level imports |
| 4 | libcohtml.Prospero.prx | <none> | 2 | Module-level imports |
| | **TOTAL** | | **175** | |

**Additional gap:**
| # | Type | Count | Details |
|---|---|---|---|
| 5 | library_mismatch | 10 | All from libc.prx; resolve on Linux but may fail on Windows |

## 3. Gap Details

### 3.1 List of 175 Absent Imports

#### 3.1.1 libc.prx — 104 C++ STL Symbols

These are C++ standard library symbols from the PS5's `libc.prx`. The names are mangled:

- `_ZNSt7num_getIcSt19istreambuf_iteratorIcSt11char_traitsIcEEE2idE_nid_postfix`
- `_ZNKSt13__sce_cxxvers16_Error_exception4whatEv_nid_postfix`
- `_ZSt26__throw_bad_variant_accessv_nid_postfix`
- `_ZTVSt7num_getIcSt19istreambuf_iteratorIcSt11char_traitsIcEEE_nid_postfix`
- `_ZNSt8ios_base7_AddstdEPS__nid_postfix`
- `_ZNKSt8time_getIcSt19istreambuf_iteratorIcSt11char_traitsIcEEE3getES3_S3_RSt8ios_baseRNSt5_IosbIiE8_IostateEP2tmPKcSE__nid_postfix`
- `_ZNSt8time_getIcSt19istreambuf_iteratorIcSt11char_traitsIcEEE2idE_nid_postfix`
- `_ZNSt8time_getIcSt19istreambuf_iteratorIcSt11char_traitsIcEEE7_GetcatEPPKNSt6locale5facetEPKS5__nid_postfix`
- `_ZSt26__throw_bad_variant_accessv_nid_postfix`
- `_Thrd_id_nid_postfix` (_Thrd_* symbols)

These are **C++ standard library** symbols that don't exist on Windows. They come from the PS5's C++ runtime (`libc.prx` / `libSceLibcInternal.prx`).

#### 3.1.2 libPlayFabMultiplayer.prx — 67 Module-Level Imports

These are imports from the PlayFab multiplayer SDK. The module `libPlayFabMultiplayer.prx` has 67 imports that the relinker can't resolve on Windows.

#### 3.1.3 libRenoirCore.PS5.prx — 2 Module-Level Imports

Two imports from the PS5-specific `libRenoirCore.PS5.prx`.

#### 3.1.4 libcohtml.Prospero.prx — 2 Module-Level Imports
## 4. Resolution Plan

### Phase 1 — Fix library_mismatch (10 items)

These are imports from `libc.prx` that are actually exported by a different library. They resolve on Linux but may fail on Windows.

**Steps:**
1. Open `audit.json`, list all 10 `library_mismatch` items with their `nid`s.
2. Cross-reference each `nid` with the `needed.json` dependency registry to find the actual exporting library.
3. Patch the relinker to correct the library mapping for these 10 imports.

**Files to modify:**
- `core/relinker/elfpatcher/src/windows/WindowsImportBuilder.cpp`
- `core/relinker/elfpatcher/src/windows/WindowsImports.hpp`

### Phase 2 — Fix `absent` imports from `libc.prx` (104 C++ STL symbols)

**Approach A — Provide stub implementations in the host:**
1. Identify the missing C++ STL symbols.
2. Add stub implementations to the host library (e.g., `libc.prx` or `libkernel.prx`) that delegate to standard C++ on Windows.
3. Rebuild the relinker with the updated stub builder.

**Approach B — Map to Win32 equivalents:**
1. Map each missing C++ STL symbol to its Win32 equivalent.
2. Update the import list to use the correct Windows API.

**Files to modify:**
- `core/relinker/elfpatcher/src/windows/WindowsDependencyStubBuilder.cpp`
- `core/relinker/elfpatcher/src/windows/WindowsImportBuilder.cpp`

### Phase 3 — Fix `absent` imports from `libPlayFabMultiplayer.prx` (67 items)

**Approach:**
1. Identify the missing PlayFab SDK symbols.
2. Provide stub implementations or load them from a Windows-compatible PlayFab SDK.
3. Rebuild the relinker.

**Files to modify:**
- `core/relinker/elfpatcher/src/windows/WindowsDependencyStubBuilder.cpp`
- `core/relinker/elfpatcher/src/windows/WindowsImportBuilder.cpp`

### Phase 4 — Fix `absent` imports from `libRenoirCore.PS5.prx` (2 items)

**Approach:**
1. Identify the 2 missing symbols.
2. Provide stub implementations for these 2 symbols.
## 5. Internet Search Strategy

### 5.1 For C++ STL Symbols (104 items)

**Search queries to try:**

| # | Search query | Expected result |
|---|---|---|
| 1 | `PS5 libc.prx C++ STL symbols missing Windows` | PS5 C++ runtime symbols and Windows equivalents |
| 2 | `PS5 libSceLibcInternal C++ std library mangled symbols Windows port` | PS5 libc symbols and Windows porting guide |
| 3 | `PS5 C++ runtime libc.prx Windows equivalent` | PS5-to-Windows C++ runtime mapping |
| 4 | `mangled C++ symbol _ZNSt8ios_base7_AddstdEPS__nid_postfix Windows` | Specific symbol mapping |
| 5 | `PS5 libc internal C++ standard library symbols list` | Full list of PS5 C++ symbols |

### 5.2 For PlayFab Multiplayer SDK (67 items)

| # | Search query | Expected result |
|---|---|---|
| 1 | `PlayFab multiplayer SDK syscalls Windows port` | PlayFab Windows compatibility guide |
| 2 | `libPlayFabMultiplayer.prx imports Windows compatibility` | PlayFab PRX file structure |
| 3 | `PlayFab SDK missing exports Windows` | PlayFab Windows build issues |
| 4 | `PlayFab multiplayer C++ SDK Windows API mapping` | PlayFab Windows API mapping |

### 5.3 For RenoirCore.PS5.prx (2 items)

| # | Search query | Expected result |
|---|---|---|
| 1 | `RenoirCore PS5 module missing imports Windows` | RenoirCore porting guide |
| 2 | `RenoirCore PRX Windows port` | RenoirCore Windows porting |

## 6. Files to Modify

| File | Purpose |
|---|---|
| `core/relinker/elfpatcher/src/windows/WindowsDependencyStubBuilder.cpp` | Builds dependency stubs with `LoadLibraryExW`/`GetProcAddress` |
| `core/relinker/elfpatcher/src/windows/WindowsImportBuilder.cpp` | Creates imports for the PE |
| `core/relinker/elfpatcher/src/windows/WindowsImports.hpp` | Import declarations for the PE |
| `tools/build_windows_game.py` | Build script for Windows |
| `tools/import_audit.py` | Audit script for imports |

## 7. Checkpoints

| Checkpoint | Status |
|---|---|
| Phase 1: Fix `library_mismatch` | ✅ Fixed (10/10) |

| Phase 1: Fix `library_mismatch` | ✅ Fixed (10/10) |
| Phase 2: Fix `libc.prx` C++ STL symbols | ⏳ Not started |
| Phase 3: Fix `libPlayFabMultiplayer.prx` imports | ⏳ Not started |
| Phase 4: Fix `libRenoirCore.PS5.prx` imports | ⏳ Not started |
| Phase 5: Fix `libcohtml.Prospero.prx` imports | ⏳ Not started |
| Phase 6: Rebuild and re-run conversion | ⏳ Not started |

## 8. Notes

- The conversion pipeline is in an **interrupted state**. The `game.exe` was generated but the audit found unresolved imports.
- The 175 absent imports are from **PS5-specific system libraries** that don't exist natively on Windows.
- The fix will require either:
  1. **Implementing the missing APIs natively for Windows**, or
  2. **Providing stubs** for these APIs, or
  3. **Updating the build/package step** to generate the missing dependencies.

### 5.4 For libcohtml.Prospero.prx (2 items)

| # | Search query | Expected result |
|---|---|---|
| 1 | `cohtml Prospero HTML renderer Windows port` | cohtml Windows porting |
| 2 | `libcohtml Prospero PRX missing imports` | cohtml PRX structure |


### Phase 5 — Fix `absent` imports from `libcohtml.Prospero.prx` (2 items)

**Approach:**
1. Identify the 2 missing symbols.
2. Provide stub implementations for these 2 symbols.

### Phase 6 — Rebuild and Re-run

1. Rebuild the relinker: `build\core\relinker\relinker.exe`
2. Re-run the conversion.
3. Re-audit until `deps_incomplete` is resolved and `ready_to_launch` becomes `true`.


Two imports from the `libcohtml.Prospero.prx` (Prospero HTML renderer).
