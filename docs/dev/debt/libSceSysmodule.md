# libSceSysmodule technical debt

### Unknown module info

- [sceSysmoduleLoadModule](../../../core/libs/prx/libSceSysmodule/Export.cpp) (libSceSysmodule) - unknown id 293 (0x125) requested by a converted title; it is absent from kModuleTable and from the psxdev Prospero dump, and the public tables checked do not list 0x125. The call logs and returns 0 without loading anything, so the title continues past it. The real module name and whether the console loads something for it are unknown.
