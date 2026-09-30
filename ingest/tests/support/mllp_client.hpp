#pragma once

#include <arpa/inet.h>
#include <chrono>
#include <cstdint>
#include <netinet/in.h>
#include <optional>
#include <poll.h>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/socket.h>
#include <unistd.h>
#include <vector>

#include "wardwatch/mllp.hpp"
#include "wardwatch/socket_io.hpp"

namespace wardwatch::testing {

// A blocking MLLP client for tests: send frames, read ACK frames back.
class MllpClient {
  public:
    explicit MllpClient(std::uint16_t port) : framer_(1U << 20U) {
        fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_port = htons(port);
        ::inet_pton(AF_INET, "127.0.0.1", &address.sin_addr);
        if (fd_ < 0 ||
            ::connect(fd_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
            throw std::runtime_error("connect failed");
        }
        // Tests send to servers that are shutting down, and a write after the
        // server closes must fail with EPIPE rather than kill the test binary.
        suppress_sigpipe(fd_);
    }
    MllpClient(const MllpClient&) = delete;
    MllpClient& operator=(const MllpClient&) = delete;
    MllpClient(MllpClient&&) = delete;
    MllpClient& operator=(MllpClient&&) = delete;
    ~MllpClient() {
        if (fd_ >= 0) {
            ::close(fd_);
        }
    }

    void send_raw(std::string_view bytes) const {
        std::size_t sent = 0;
        while (sent < bytes.size()) {
            const auto result = send_no_signal(fd_, bytes.data() + sent, bytes.size() - sent);
            if (result <= 0) {
                throw std::runtime_error("send failed");
            }
            sent += static_cast<std::size_t>(result);
        }
    }

    void send_message(std::string_view message) const {
        const auto framed = mllp_wrap(std::span<const char>(message.data(), message.size()));
        send_raw(std::string_view(framed.data(), framed.size()));
    }

    void shutdown_write() const { ::shutdown(fd_, SHUT_WR); }

    // Reads until one ACK frame arrives or the timeout passes.
    std::optional<std::string> read_ack(
        std::chrono::milliseconds timeout = std::chrono::seconds(10)) {
        const auto deadline = std::chrono::steady_clock::now() + timeout;
        while (acks_.empty()) {
            const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
                deadline - std::chrono::steady_clock::now());
            if (remaining.count() <= 0) {
                return std::nullopt;
            }
            pollfd descriptor{.fd = fd_, .events = POLLIN, .revents = 0};
            if (::poll(&descriptor, 1, static_cast<int>(remaining.count())) <= 0) {
                continue;
            }
            std::vector<char> buffer(65536);
            const auto received = ::recv(fd_, buffer.data(), buffer.size(), 0);
            if (received <= 0) {
                return std::nullopt;
            }
            framer_.feed(std::span<const char>(buffer.data(), static_cast<std::size_t>(received)),
                         [this](Frame&& frame) {
                             acks_.emplace_back(frame.bytes.begin(), frame.bytes.end());
                         });
        }
        std::string ack = std::move(acks_.front());
        acks_.erase(acks_.begin());
        return ack;
    }

  private:
    int fd_ = -1;
    MllpFramer framer_;
    std::vector<std::string> acks_;
};

// MSA-1 of an ACK (AA, AE or AR), or empty when it has no MSA segment.
inline std::string ack_code(std::string_view ack) {
    const std::size_t msa = ack.find("MSA|");
    return msa == std::string_view::npos ? std::string() : std::string(ack.substr(msa + 4, 2));
}

inline std::string acked_control_id(std::string_view ack) {
    const std::size_t msa = ack.find("MSA|");
    if (msa == std::string_view::npos) {
        return {};
    }
    const std::size_t start = msa + 7;
    return std::string(ack.substr(start, ack.find('\r', start) - start));
}

}  // namespace wardwatch::testing
