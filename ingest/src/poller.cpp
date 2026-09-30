#include "wardwatch/poller.hpp"

#include <array>
#include <cerrno>
#include <format>
#include <system_error>
#include <unistd.h>
#include <utility>

#ifdef __linux__
#include <sys/epoll.h>
#elifdef __APPLE__
#include <sys/event.h>
#else
#error "Poller supports Linux (epoll) and macOS (kqueue) only"
#endif

namespace wardwatch {
namespace {

std::string errno_message(std::string_view what) {
    return std::format("{}: {}", what, std::error_code(errno, std::generic_category()).message());
}

constexpr std::size_t kMaxBatch = 256;

}  // namespace

Poller::Poller(Poller&& other) noexcept : fd_(std::exchange(other.fd_, -1)) {}

Poller& Poller::operator=(Poller&& other) noexcept {
    if (this != &other) {
        if (fd_ >= 0) {
            ::close(fd_);
        }
        fd_ = std::exchange(other.fd_, -1);
    }
    return *this;
}

Poller::~Poller() {
    if (fd_ >= 0) {
        ::close(fd_);
    }
}

#ifdef __linux__

std::expected<Poller, std::string> Poller::create() {
    const int fd = ::epoll_create1(EPOLL_CLOEXEC);
    if (fd < 0) {
        return std::unexpected(errno_message("epoll_create1"));
    }
    return Poller(fd);
}

namespace {

epoll_event make_event(std::uint64_t token, bool read, bool write) {
    epoll_event event{};
    // EPOLLRDHUP is level triggered, so it is only requested with read
    // interest; otherwise a half-closed socket would wake the loop forever.
    event.events = (read ? (EPOLLIN | EPOLLRDHUP) : 0U) | (write ? EPOLLOUT : 0U);
    event.data.u64 = token;
    return event;
}

}  // namespace

std::expected<void, std::string> Poller::add(int fd, std::uint64_t token, bool read, bool write) {
    epoll_event event = make_event(token, read, write);
    if (::epoll_ctl(fd_, EPOLL_CTL_ADD, fd, &event) != 0) {
        return std::unexpected(errno_message("epoll_ctl add"));
    }
    return {};
}

std::expected<void, std::string> Poller::modify(int fd, std::uint64_t token, bool read,
                                                bool write) {
    epoll_event event = make_event(token, read, write);
    if (::epoll_ctl(fd_, EPOLL_CTL_MOD, fd, &event) != 0) {
        return std::unexpected(errno_message("epoll_ctl mod"));
    }
    return {};
}

void Poller::remove(int fd) noexcept { ::epoll_ctl(fd_, EPOLL_CTL_DEL, fd, nullptr); }

std::expected<std::size_t, std::string> Poller::wait(std::span<PollEvent> events,
                                                     std::chrono::milliseconds timeout) {
    std::array<epoll_event, kMaxBatch> raw{};
    const int capacity = static_cast<int>(std::min(events.size(), raw.size()));
    const int count = ::epoll_wait(fd_, raw.data(), capacity, static_cast<int>(timeout.count()));
    if (count < 0) {
        if (errno == EINTR) {
            return 0;
        }
        return std::unexpected(errno_message("epoll_wait"));
    }
    const auto filled = static_cast<std::size_t>(count);
    for (std::size_t i = 0; i < filled; ++i) {
        const std::uint32_t flags = raw.at(i).events;
        events[i] = PollEvent{raw.at(i).data.u64, (flags & (EPOLLIN | EPOLLRDHUP)) != 0U,
                              (flags & EPOLLOUT) != 0U, (flags & (EPOLLHUP | EPOLLERR)) != 0U};
    }
    return filled;
}

#elifdef __APPLE__

std::expected<Poller, std::string> Poller::create() {
    const int fd = ::kqueue();
    if (fd < 0) {
        return std::unexpected(errno_message("kqueue"));
    }
    return Poller(fd);
}

namespace {

// kqueue tracks read and write interest as two separate filters.
std::expected<void, std::string> apply(int queue, int fd, std::uint64_t token, bool read,
                                       bool write) {
    std::array<struct kevent, 2> changes{};
    // kqueue hands back a void* of user data, so the token rides in it.
    auto* const udata = reinterpret_cast<void*>(  // NOLINT(performance-no-int-to-ptr): kevent udata
                                                  // carries a token, never dereferenced
        static_cast<std::uintptr_t>(token));
    EV_SET(changes.data(), static_cast<std::uintptr_t>(fd), EVFILT_READ,
           static_cast<std::uint16_t>(read ? (EV_ADD | EV_ENABLE) : (EV_ADD | EV_DISABLE)), 0, 0,
           udata);
    EV_SET(changes.data() + 1, static_cast<std::uintptr_t>(fd), EVFILT_WRITE,
           static_cast<std::uint16_t>(write ? (EV_ADD | EV_ENABLE) : (EV_ADD | EV_DISABLE)), 0, 0,
           udata);
    if (::kevent(queue, changes.data(), 2, nullptr, 0, nullptr) != 0) {
        return std::unexpected(errno_message("kevent change"));
    }
    return {};
}

}  // namespace

std::expected<void, std::string> Poller::add(int fd, std::uint64_t token, bool read, bool write) {
    return apply(fd_, fd, token, read, write);
}

std::expected<void, std::string> Poller::modify(int fd, std::uint64_t token, bool read,
                                                bool write) {
    return apply(fd_, fd, token, read, write);
}

void Poller::remove(int fd) noexcept {
    std::array<struct kevent, 2> changes{};
    EV_SET(changes.data(), static_cast<std::uintptr_t>(fd), EVFILT_READ, EV_DELETE, 0, 0, nullptr);
    EV_SET(changes.data() + 1, static_cast<std::uintptr_t>(fd), EVFILT_WRITE, EV_DELETE, 0, 0,
           nullptr);
    ::kevent(fd_, changes.data(), 2, nullptr, 0, nullptr);
}

std::expected<std::size_t, std::string> Poller::wait(std::span<PollEvent> events,
                                                     std::chrono::milliseconds timeout) {
    std::array<struct kevent, kMaxBatch> raw{};
    const int capacity = static_cast<int>(std::min(events.size(), raw.size()));
    const auto seconds = std::chrono::duration_cast<std::chrono::seconds>(timeout);
    const timespec wait_time{
        .tv_sec = static_cast<time_t>(seconds.count()),
        .tv_nsec = static_cast<long>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(timeout - seconds).count())};
    const int count = ::kevent(fd_, nullptr, 0, raw.data(), capacity, &wait_time);
    if (count < 0) {
        if (errno == EINTR) {
            return 0;
        }
        return std::unexpected(errno_message("kevent wait"));
    }
    const auto filled = static_cast<std::size_t>(count);
    for (std::size_t i = 0; i < filled; ++i) {
        const auto& event = raw.at(i);
        events[i] =
            PollEvent{static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(event.udata)),
                      event.filter == EVFILT_READ, event.filter == EVFILT_WRITE,
                      (event.flags & (EV_EOF | EV_ERROR)) != 0};
    }
    return filled;
}

#endif

}  // namespace wardwatch
