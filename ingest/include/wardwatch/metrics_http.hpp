#pragma once

#include <atomic>
#include <cstdint>
#include <expected>
#include <functional>
#include <string>
#include <string_view>
#include <thread>

namespace wardwatch {

struct HttpResponse {
    int status = 200;
    std::string content_type;
    std::string body;
};

// Routes one request line ("GET /metrics HTTP/1.1") to a response. Kept apart
// from the socket code so it can be tested without a network.
[[nodiscard]] HttpResponse route_metrics_request(std::string_view request_line,
                                                 const std::function<std::string()>& render);

// A deliberately small HTTP/1.0-style server for /metrics and /healthz on its
// own thread and port. It answers one request per connection and closes, which
// is all a Prometheus scrape needs.
class MetricsHttpServer {
  public:
    MetricsHttpServer(std::string bind_address, std::uint16_t port,
                      std::function<std::string()> render);
    MetricsHttpServer(const MetricsHttpServer&) = delete;
    MetricsHttpServer& operator=(const MetricsHttpServer&) = delete;
    MetricsHttpServer(MetricsHttpServer&&) = delete;
    MetricsHttpServer& operator=(MetricsHttpServer&&) = delete;
    ~MetricsHttpServer();

    [[nodiscard]] std::expected<void, std::string> start();
    [[nodiscard]] std::uint16_t port() const noexcept { return bound_port_; }
    void stop();

  private:
    void serve();
    void answer(int client_fd) const;

    std::string bind_address_;
    std::uint16_t port_;
    std::function<std::string()> render_;
    int listener_fd_ = -1;
    std::uint16_t bound_port_ = 0;
    std::atomic<bool> stopping_{false};
    std::thread thread_;
};

}  // namespace wardwatch
