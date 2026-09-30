#include "wardwatch/metrics_http.hpp"

#include <string>

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
