# libSceSysmodule technical debt

### Unknown module info

- [sceSysmoduleLoadModule](../../../core/libs/prx/libSceSysmodule/Export.cpp) (libSceSysmodule) - unknown id 293 (0x125) requested by a converted title; it is absent from kModuleTable and from the psxdev Prospero dump, and the public tables checked do not list 0x125. The call logs and returns 0 without loading anything, so the title continues past it. The real module name and whether the console loads something for it are unknown.

### Deadlock after unknown id 293

- Minecraft PPSA17221 (SDK 0x1200) stalls immediately after `sceSysmoduleLoadModule(293)` returns 0; `run.log` emits nothing further.
- Instrumented stubs to identify the caller: `sceKernelAddWriteEvent`, `sceKernelAddReadEvent` (Equeue.cpp), `select_nid_postfix` (Socket.cpp), `sceKernelWaitSema` (Semaphore.cpp), plus `[stub]` logging in `NotImplemented_nid_no_patch` (General.cpp). None of these are invoked after id 293 before the stall.
- `APS5_TRACE_SYNC=1` tracing for `sceKernelWaitEventFlag` was enabled; no event-flag wait is logged either.
- Conclusion: the stall happens in code that produces no diagnostic output (a kernel wait primitive not instrumented, a title-side spin loop, or a host synchronization primitive). Root cause not identified.
- Next step: run `game.exe` under WinDbg/x64dbg and inspect the call stack after `sceSysmoduleLoadModule` returns; if a specific stub is confirmed, implement it or add it to this debt file.
