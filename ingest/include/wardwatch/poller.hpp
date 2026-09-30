#pragma once

#include <chrono>
#include <cstdint>
#include <expected>
#include <span>
#include <string>

namespace wardwatch {

struct PollEvent {
    std::uint64_t token = 0;
    // Data is waiting, or the peer shut its write side (a read returns 0).
    bool readable = false;
    bool writable = false;
    // Both directions are closed or the socket errored; nothing more can be
    // written. Unread data may still be readable in the same event.
    bool hangup = false;
};

// Readiness notification for non-blocking descriptors: epoll on Linux, the
// production target, and kqueue on macOS for local development. Level
// triggered on both, so a descriptor with unread data keeps reporting.
class Poller {
  public:
    static std::expected<Poller, std::string> create();

    Poller(Poller&& other) noexcept;
    Poller& operator=(Poller&& other) noexcept;
    Poller(const Poller&) = delete;
    Poller& operator=(const Poller&) = delete;
    ~Poller();

    [[nodiscard]] std::expected<void, std::string> add(int fd, std::uint64_t token, bool read,
                                                       bool write);
    [[nodiscard]] std::expected<void, std::string> modify(int fd, std::uint64_t token, bool read,
                                                          bool write);
    void remove(int fd) noexcept;

    // Waits up to `timeout` and fills `events`; returns how many were filled.
    [[nodiscard]] std::expected<std::size_t, std::string> wait(std::span<PollEvent> events,
                                                               std::chrono::milliseconds timeout);

  private:
    explicit Poller(int fd) noexcept : fd_(fd) {}
    int fd_ = -1;
};

}  // namespace wardwatch
