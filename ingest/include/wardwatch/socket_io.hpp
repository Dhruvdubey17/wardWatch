#pragma once

#include <cstddef>
#include <sys/socket.h>
#include <sys/types.h>

namespace wardwatch {

// Sending to a peer that has already closed raises SIGPIPE, which ends the
// whole process by default. Linux turns that off per call with MSG_NOSIGNAL;
// macOS has no such flag and turns it off per socket with SO_NOSIGPIPE. Every
// socket write in the library and its tests goes through these two, so neither
// platform depends on the program having ignored SIGPIPE.

// Call once on each socket before the first send.
inline void suppress_sigpipe([[maybe_unused]] int fd) noexcept {
#ifdef __APPLE__
    const int enabled = 1;
    ::setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled));
#endif
}

// ::send that reports a closed peer as -1 with errno EPIPE instead of a signal.
inline ssize_t send_no_signal(int fd, const void* data, std::size_t size) noexcept {
#ifdef __linux__
    return ::send(fd, data, size, MSG_NOSIGNAL);
#else
    return ::send(fd, data, size, 0);
#endif
}

}  // namespace wardwatch
