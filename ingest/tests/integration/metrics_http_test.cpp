#include "wardwatch/metrics_http.hpp"

#include <arpa/inet.h>
#include <array>
#include <csignal>
#include <cstddef>
#include <netinet/in.h>
#include <string>
#include <sys/socket.h>
#include <unistd.h>

#include <gtest/gtest.h>

#include "wardwatch/server.hpp"

#include "contracts.hpp"
#include "http_get.hpp"
#include "memory_sink.hpp"
#include "mllp_client.hpp"

namespace wardwatch {
namespace {

using testing::http_get;

TEST(MetricsHttp, ScrapeReflectsTraffic) {
    testing::MemorySink sink;
    Metrics metrics;
    ServerConfig config;
    config.port = 0;
    Server server(config, sink, metrics);
    ASSERT_TRUE(server.start().has_value());
    MetricsHttpServer http("127.0.0.1", 0,
                           [&] { return render_metrics(metrics, server.gauges()); });
    ASSERT_TRUE(http.start().has_value());

    testing::MllpClient client(server.port());
    client.send_message(testing::read_contract("hl7/oru_r01.hl7"));
    ASSERT_TRUE(client.read_ack().has_value());

    const std::string response = http_get(http.port(), "/metrics");
    EXPECT_TRUE(response.starts_with("HTTP/1.1 200 OK\r\n"));
    EXPECT_NE(response.find("Content-Type: text/plain; version=0.0.4"), std::string::npos);
    EXPECT_NE(response.find("\nwardwatch_ingest_messages_accepted_total 1\n"), std::string::npos);
    EXPECT_NE(response.find("\nwardwatch_ingest_connections_accepted_total 1\n"),
              std::string::npos);
    EXPECT_TRUE(http_get(http.port(), "/healthz").ends_with("\r\n\r\nok\n"));
    EXPECT_TRUE(http_get(http.port(), "/nope").starts_with("HTTP/1.1 404"));

    http.stop();
    server.request_stop();
    server.wait();
}

// A scraper that hangs up while the response is still being written costs that
// one response: the server stops writing to it and answers the next scrape.
// SIGPIPE is at its default action, as in a program that embeds the library
// without ignoring it.
TEST(MetricsHttp, SurvivesAScraperThatHangsUpMidResponse) {
    const auto previous = std::signal(SIGPIPE, SIG_DFL);
    ASSERT_NE(previous, SIG_ERR);
    constexpr std::size_t kBodyBytes = std::size_t{32} * 1024 * 1024;
    MetricsHttpServer http("127.0.0.1", 0, [] { return std::string(kBodyBytes, 'x'); });
    ASSERT_TRUE(http.start().has_value());

    const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    ASSERT_GE(fd, 0);
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(http.port());
    ::inet_pton(AF_INET, "127.0.0.1", &address.sin_addr);
    ASSERT_EQ(::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)), 0);
    const std::string request = "GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n";
    ASSERT_EQ(::send(fd, request.data(), request.size(), 0), static_cast<ssize_t>(request.size()));
    // Wait for the response to start, so the server is mid-write when the
    // reset arrives, then close with a zero linger to send RST, not FIN.
    std::array<char, 4096> buffer{};
    ASSERT_GT(::recv(fd, buffer.data(), buffer.size(), 0), 0);
    const linger reset{.l_onoff = 1, .l_linger = 0};
    ::setsockopt(fd, SOL_SOCKET, SO_LINGER, &reset, sizeof(reset));
    ::close(fd);

    // The server gives up on that response and answers the next scrape.
    const std::string health = http_get(http.port(), "/healthz");
    EXPECT_TRUE(health.starts_with("HTTP/1.1 200 OK\r\n"));
    std::signal(SIGPIPE, previous);
}

TEST(MetricsHttp, ReportsBindFailure) {
    MetricsHttpServer first("127.0.0.1", 0, [] { return std::string(); });
    ASSERT_TRUE(first.start().has_value());
    MetricsHttpServer second("127.0.0.1", first.port(), [] { return std::string(); });
    EXPECT_FALSE(second.start().has_value());
    MetricsHttpServer bad("not-an-address", 0, [] { return std::string(); });
    EXPECT_FALSE(bad.start().has_value());
}

}  // namespace
}  // namespace wardwatch
