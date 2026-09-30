#include "wardwatch/metrics_http.hpp"

#include <arpa/inet.h>
#include <array>
#include <cerrno>
#include <chrono>
#include <format>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <system_error>
#include <unistd.h>

#include "wardwatch/socket_io.hpp"

namespace wardwatch {
namespace {

constexpr int kAcceptPollMillis = 200;
constexpr int kReadTimeoutMillis = 1000;
constexpr std::size_t kMaxRequestBytes = 8192;

std::string_view reason_phrase(int status) noexcept {
    switch (status) {
        case 200:
            return "OK";
        case 404:
            return "Not Found";
        case 405:
            return "Method Not Allowed";
        default:
            return "Bad Request";
    }
}

std::string errno_message(std::string_view what) {
    return std::format("{}: {}", what, std::error_code(errno, std::generic_category()).message());
}

}  // namespace

HttpResponse route_metrics_request(std::string_view request_line,
                                   const std::function<std::string()>& render) {
    const std::size_t method_end = request_line.find(' ');
    const std::size_t path_end = request_line.find(' ', method_end + 1);
    if (method_end == std::string_view::npos || path_end == std::string_view::npos) {
        return {400, "text/plain", "bad request\n"};
    }
    const std::string_view method = request_line.substr(0, method_end);
    std::string_view path = request_line.substr(method_end + 1, path_end - method_end - 1);
    path = path.substr(0, path.find('?'));
    if (method != "GET") {
        return {405, "text/plain", "only GET is supported\n"};
    }
    if (path == "/metrics") {
        return {200, "text/plain; version=0.0.4; charset=utf-8", render()};
    }
    if (path == "/healthz") {
        return {200, "text/plain", "ok\n"};
    }
    return {404, "text/plain", "not found\n"};
}

MetricsHttpServer::MetricsHttpServer(std::string bind_address, std::uint16_t port,
                                     std::function<std::string()> render)
    : bind_address_(std::move(bind_address)), port_(port), render_(std::move(render)) {}

MetricsHttpServer::~MetricsHttpServer() { stop(); }

std::expected<void, std::string> MetricsHttpServer::start() {
    listener_fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
    if (listener_fd_ < 0) {
        return std::unexpected(errno_message("metrics socket"));
    }
    const int enabled = 1;
    ::setsockopt(listener_fd_, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port_);
    if (::inet_pton(AF_INET, bind_address_.c_str(), &address.sin_addr) != 1) {
        return std::unexpected(std::format("metrics bind address '{}' is not IPv4", bind_address_));
    }
    if (::bind(listener_fd_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 ||
        ::listen(listener_fd_, 16) != 0) {
        return std::unexpected(
            errno_message(std::format("metrics listen on {}:{}", bind_address_, port_)));
    }
    socklen_t length = sizeof(address);
    ::getsockname(listener_fd_, reinterpret_cast<sockaddr*>(&address), &length);
    bound_port_ = ntohs(address.sin_port);
    thread_ = std::thread([this] { serve(); });
    return {};
}

void MetricsHttpServer::stop() {
    stopping_.store(true, std::memory_order_relaxed);
    if (thread_.joinable()) {
        thread_.join();
    }
    if (listener_fd_ >= 0) {
        ::close(listener_fd_);
        listener_fd_ = -1;
    }
}

void MetricsHttpServer::serve() {
    // Polling with a short timeout lets stop() end the thread without
    // signals or a second descriptor.
    while (!stopping_.load(std::memory_order_relaxed)) {
        pollfd listener{.fd = listener_fd_, .events = POLLIN, .revents = 0};
        if (::poll(&listener, 1, kAcceptPollMillis) <= 0) {
            continue;
        }
        const int client = ::accept(listener_fd_, nullptr, nullptr);
        if (client < 0) {
            continue;
        }
        suppress_sigpipe(client);
        answer(client);
        ::close(client);
    }
}

void MetricsHttpServer::answer(int client_fd) const {
    std::string request;
    std::array<char, 1024> buffer{};
    const auto deadline =
        std::chrono::steady_clock::now() + std::chrono::milliseconds(kReadTimeoutMillis);
    while (!request.contains("\r\n\r\n") && request.size() < kMaxRequestBytes) {
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now());
        pollfd readable{.fd = client_fd, .events = POLLIN, .revents = 0};
        if (remaining.count() <= 0 ||
            ::poll(&readable, 1, static_cast<int>(remaining.count())) <= 0) {
            return;
        }
        const auto received = ::recv(client_fd, buffer.data(), buffer.size(), 0);
        if (received <= 0) {
            return;
        }
        request.append(buffer.data(), static_cast<std::size_t>(received));
    }
    const auto response =
        route_metrics_request(std::string_view(request).substr(0, request.find("\r\n")), render_);
    const std::string head = std::format(
        "HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        response.status, reason_phrase(response.status), response.content_type,
        response.body.size());
    const std::string full = head + response.body;
    std::size_t sent = 0;
    while (sent < full.size()) {
        const auto written = send_no_signal(client_fd, full.data() + sent, full.size() - sent);
        if (written <= 0) {
            return;
        }
        sent += static_cast<std::size_t>(written);
    }
}

}  // namespace wardwatch
