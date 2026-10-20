#ifndef CORE_LIBS_PRX_LIBC_INCLUDE_GENERAL_DIAGNOSTICLOG_HPP
#define CORE_LIBS_PRX_LIBC_INCLUDE_GENERAL_DIAGNOSTICLOG_HPP

#include <atomic>
#include <chrono>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>

namespace APS5::Diagnostic {

enum class Level : std::uint8_t {
    Error = 0,
    Warn = 1,
    Info = 2,
    Debug = 3,
    Trace = 4,
};

inline constexpr Level error = Level::Error;
inline constexpr Level warn = Level::Warn;
inline constexpr Level info = Level::Info;
inline constexpr Level debug = Level::Debug;
inline constexpr Level trace = Level::Trace;

inline Level CurrentLevel() {
    static const Level level = [] {
        const char* env = std::getenv("ANYPS5_LOG_LEVEL");
        if (!env) return Level::Info;
        std::string value(env);
        if (value == "error") return Level::Error;
        if (value == "warn") return Level::Warn;
        if (value == "info") return Level::Info;
        if (value == "debug") return Level::Debug;
        if (value == "trace") return Level::Trace;
        return Level::Info;
    }();
    return level;
}

inline std::FILE* LogFile() {
    static std::FILE* file = []() -> std::FILE* {
        const char* path = std::getenv("ANYPS5_LOG_FILE");
        if (!path || !*path) return nullptr;
        return std::fopen(path, "a");
    }();
    return file;
}

inline std::mutex& LogMutex() {
    static std::mutex mutex;
    return mutex;
}

inline std::string ThreadId() {
    std::ostringstream out;
    out << std::this_thread::get_id();
    return out.str();
}

inline long long TimestampMicros() {
    using Clock = std::chrono::steady_clock;
    static const auto started = Clock::now();
    return static_cast<long long>(
        std::chrono::duration_cast<std::chrono::microseconds>(
            Clock::now() - started
        ).count()
    );
}

inline void FormatTimestamp(char* out, std::size_t capacity, long long micros) {
    std::snprintf(out, capacity, "[+%llu.%06llu s] ",
        static_cast<unsigned long long>(micros / 1000000),
        static_cast<unsigned long long>(micros % 1000000));
}

inline const char* LevelName(Level level) {
    switch (level) {
        case Level::Error: return "ERROR";
        case Level::Warn: return "WARN";
        case Level::Info: return "INFO";
        case Level::Debug: return "DEBUG";
        case Level::Trace: return "TRACE";
    }
    return "????";
}

#ifdef __MINGW_PRINTF_FORMAT
[[gnu::format(__MINGW_PRINTF_FORMAT, 4, 5)]]
#else
[[gnu::format(printf, 4, 5)]]
#endif
inline void Log(Level level, const char* component, std::uintptr_t guestAddr,
                const char* format, ...) {
    if (static_cast<std::uint8_t>(level) > static_cast<std::uint8_t>(CurrentLevel()))
        return;

    std::lock_guard<std::mutex> lock(LogMutex());

    char timebuf[32];
    FormatTimestamp(timebuf, sizeof(timebuf), TimestampMicros());

    char small[2048];
    std::size_t prefixLength = std::strlen(timebuf);
    std::memcpy(small, timebuf, prefixLength);

    int written = guestAddr != 0
        ? std::snprintf(small + prefixLength, sizeof(small) - prefixLength,
            "[%s %s tid=%s addr=0x%llx] ", LevelName(level), component,
            ThreadId().c_str(), static_cast<unsigned long long>(guestAddr))
        : std::snprintf(small + prefixLength, sizeof(small) - prefixLength,
            "[%s %s tid=%s] ", LevelName(level), component, ThreadId().c_str());
    if (written < 0) written = 0;
    prefixLength += static_cast<std::size_t>(written);

    std::va_list arguments;
    va_start(arguments, format);
    std::va_list copy;
    va_copy(copy, arguments);
    int length = std::vsnprintf(small + prefixLength, sizeof(small) - prefixLength, format, arguments);
    va_end(arguments);
    std::string large;
    if (length >= static_cast<int>(sizeof(small) - prefixLength)) {
        large.resize(prefixLength + static_cast<std::size_t>(length) + 1);
        if (prefixLength != 0) std::memcpy(large.data(), small, prefixLength);
        std::vsnprintf(large.data() + prefixLength, large.size() - prefixLength, format, copy);
    }
    va_end(copy);
    if (length < 0) length = 0;

    const std::size_t total = prefixLength + static_cast<std::size_t>(length);
    const char* text = large.empty() ? small : large.data();
    if (std::FILE* file = LogFile()) {
        std::fwrite(text, 1, total, file);
        std::fputc('\n', file);
        std::fflush(file);
    }
    std::fwrite(text, 1, total, stdout);
    std::fputc('\n', stdout);
    std::fflush(stdout);
}

#define APS5_DIAG_LOG(level, component, fmt, ...) \
    APS5::Diagnostic::Log(APS5::Diagnostic::level, component, 0, fmt __VA_OPT__(,) __VA_ARGS__)

#define APS5_DIAG_LOG_ADDR(level, component, guest_addr, fmt, ...) \
    APS5::Diagnostic::Log(APS5::Diagnostic::level, component, guest_addr, fmt __VA_OPT__(,) __VA_ARGS__)

}  // namespace APS5::Diagnostic

#endif  // CORE_LIBS_PRX_LIBC_INCLUDE_GENERAL_DIAGNOSTICLOG_HPP