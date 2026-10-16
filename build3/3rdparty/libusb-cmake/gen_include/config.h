/* #define to the attribute for default visibility. */
#define DEFAULT_VISIBILITY __attribute__((visibility("default")))

/* #define to 1 to start with debug message logging enabled. */
/* #undef ENABLE_DEBUG_LOGGING */

/* #define to 1 to enable message logging. */
#define ENABLE_LOGGING 1

/* #define to 1 if you have the <asm/types.h> header file. */
/* #undef HAVE_ASM_TYPES_H */

/* #define to 1 if you have the `clock_gettime' function. */
#define HAVE_CLOCK_GETTIME 1

/* #define to 1 if the system has eventfd functionality. */
/* #undef HAVE_EVENTFD */

/* #define to 1 if the system has the type `nfds_t'. */
/* #undef HAVE_NFDS_T */

/* #define to 1 if you have the `pipe2' function. */
/* #undef HAVE_PIPE2 */

/* #define to 1 if you have the `pthread_condattr_setclock' function. */
/* #undef HAVE_PTHREAD_CONDATTR_SETCLOCK */

/* #define to 1 if you have the `pthread_setname_np' function. */
/* #undef HAVE_PTHREAD_SETNAME_NP */

/* #define to 1 if you have the `pthread_threadid_np' function. */
/* #undef HAVE_PTHREAD_THREADID_NP */

/* #define to 1 if the system has the type `struct timespec'. */
#define HAVE_STRUCT_TIMESPEC 1

/* #define to 1 if you have the `syslog' function. */
/* #undef HAVE_SYSLOG */

/* #define to 1 if you have the <sys/time.h> header file. */
/* #undef HAVE_SYS_TIME_H */

/* #define to 1 if the system has timerfd functionality. */
/* #undef HAVE_TIMERFD */

/* #define to 1 if compiling for a POSIX platform. */
/* #undef PLATFORM_POSIX */

/* #define to 1 if compiling for a Windows platform. */
#define PLATFORM_WINDOWS 1

/* #define to 1 to enable Windows hotplug support. */
/* #undef LIBUSB_WINDOWS_HOTPLUG */


#if defined(__GNUC__)
 #define PRINTF_FORMAT(a, b) __attribute__ ((format (__printf__, a, b)))
#else
 #define PRINTF_FORMAT(a, b)
#endif

/* #define to 1 if you have the ANSI C header files. */
/* #undef STDC_HEADERS */

/* #define to 1 to output logging messages to the systemwide log. */
/* #undef USE_SYSTEM_LOGGING_FACILITY */

/* Enable GNU extensions. */
#define _GNU_SOURCE 1
