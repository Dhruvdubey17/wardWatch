#include "wardwatch/metrics.hpp"

#include <regex>
#include <set>
#include <sstream>
#include <string>

#include <gtest/gtest.h>

#include "wardwatch/metrics_http.hpp"

namespace wardwatch {
namespace {

bool contains_line(const std::string& text, const std::string& line) {
    std::istringstream stream(text);
    for (std::string current; std::getline(stream, current);) {
        if (current == line) {
            return true;
        }
    }
    return false;
}

TEST(LatencyHistogram, BucketsByUpperBound) {
    LatencyHistogram histogram;
    histogram.observe(0.5);     // le 1
    histogram.observe(1.0);     // le 1, bounds are inclusive
    histogram.observe(1.5);     // le 2
    histogram.observe(500000);  // above every bound
    EXPECT_EQ(histogram.bucket(0), 2U);
    EXPECT_EQ(histogram.bucket(1), 1U);
    EXPECT_EQ(histogram.bucket(LatencyHistogram::kBoundsMicros.size()), 1U);
    EXPECT_EQ(histogram.count(), 4U);
    EXPECT_DOUBLE_EQ(histogram.sum_micros(), 0.5 + 1.0 + 1.5 + 500000);
}

TEST(RenderMetrics, ReportsCountersGaugesAndLabels) {
    Metrics metrics;
    metrics.messages_received = 10;
    metrics.messages_accepted = 7;
    metrics.bytes_read = 12345;
    metrics.backpressure_events = 2;
    metrics.connections_open = 3;
    metrics.delivery.delivered = 6;
    metrics.count_rejection(ErrorCode::timestamp_invalid);
    metrics.count_rejection(ErrorCode::timestamp_invalid);
    metrics.count_warning(WarningCode::value_implausible);
    const RuntimeGauges gauges{.inbound_ring_size = 5,
                               .inbound_ring_capacity = 4096,
                               .outbound_ring_size = 1,
                               .outbound_ring_capacity = 4096};
    const std::string text = render_metrics(metrics, gauges);

    EXPECT_TRUE(contains_line(text, "wardwatch_ingest_messages_received_total 10"));
    EXPECT_TRUE(contains_line(text, "wardwatch_ingest_messages_accepted_total 7"));
    EXPECT_TRUE(contains_line(text, "wardwatch_ingest_bytes_read_total 12345"));
    EXPECT_TRUE(contains_line(text, "wardwatch_ingest_backpressure_events_total 2"));
    EXPECT_TRUE(contains_line(text, "wardwatch_ingest_connections_open 3"));
    EXPECT_TRUE(contains_line(text, "wardwatch_ingest_sink_delivered_total 6"));
    EXPECT_TRUE(contains_line(
        text, R"(wardwatch_ingest_messages_rejected_total{code="TIMESTAMP_INVALID"} 2)"));
    EXPECT_TRUE(contains_line(
        text, R"(wardwatch_ingest_messages_rejected_total{code="FRAME_OVERSIZE"} 0)"));
    EXPECT_TRUE(
        contains_line(text, R"(wardwatch_ingest_warnings_total{code="VALUE_IMPLAUSIBLE"} 1)"));
    EXPECT_TRUE(contains_line(text, R"(wardwatch_ingest_ring_occupancy{ring="inbound"} 5)"));
    EXPECT_TRUE(contains_line(text, R"(wardwatch_ingest_ring_capacity{ring="outbound"} 4096)"));
    EXPECT_TRUE(contains_line(text, "# TYPE wardwatch_ingest_parse_latency_seconds histogram"));
}

TEST(RenderMetrics, HistogramIsCumulativeAndEndsWithInf) {
    Metrics metrics;
    metrics.parse_latency.observe(3);    // le 5us
    metrics.parse_latency.observe(3);    // le 5us
    metrics.parse_latency.observe(40);   // le 50us
    metrics.parse_latency.observe(2e6);  // +Inf
    const std::string text = render_metrics(metrics, RuntimeGauges{});
    EXPECT_TRUE(
        contains_line(text, R"(wardwatch_ingest_parse_latency_seconds_bucket{le="2e-06"} 0)"));
    EXPECT_TRUE(
        contains_line(text, R"(wardwatch_ingest_parse_latency_seconds_bucket{le="5e-06"} 2)"));
    EXPECT_TRUE(
        contains_line(text, R"(wardwatch_ingest_parse_latency_seconds_bucket{le="5e-05"} 3)"));
    EXPECT_TRUE(
        contains_line(text, R"(wardwatch_ingest_parse_latency_seconds_bucket{le="0.1"} 3)"));
    EXPECT_TRUE(
        contains_line(text, R"(wardwatch_ingest_parse_latency_seconds_bucket{le="+Inf"} 4)"));
    EXPECT_TRUE(contains_line(text, "wardwatch_ingest_parse_latency_seconds_count 4"));
}

// Every line must fit the text exposition format, and every sample must
// follow a TYPE line for its family.
TEST(RenderMetrics, FollowsExpositionFormat) {
    Metrics metrics;
    metrics.parse_latency.observe(12);
    const std::string text = render_metrics(metrics, RuntimeGauges{});
    const std::regex sample(
        R"(^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[a-z_]+="[^"]*"(,[a-z_]+="[^"]*")*\})? (-?[0-9.e+-]+|\+Inf|NaN)$)");
    const std::regex comment(R"(^# (HELP|TYPE) ([a-zA-Z_:][a-zA-Z0-9_:]*) .+$)");
    std::set<std::string> typed;
    std::istringstream stream(text);
    int samples = 0;
    for (std::string line; std::getline(stream, line);) {
        std::smatch match;
        if (line.starts_with("#")) {
            ASSERT_TRUE(std::regex_match(line, match, comment)) << line;
            if (match[1] == "TYPE") {
                typed.insert(match[2]);
            }
            continue;
        }
        ASSERT_TRUE(std::regex_match(line, match, sample)) << line;
        std::string family = match[1];
        for (const std::string suffix : {"_bucket", "_sum", "_count"}) {
            if (family.ends_with(suffix) && !typed.contains(family)) {
                family.resize(family.size() - suffix.size());
            }
        }
        EXPECT_TRUE(typed.contains(family)) << line;
        ++samples;
    }
    EXPECT_GT(samples, 30);
    EXPECT_TRUE(text.ends_with("\n"));
}

TEST(MetricsRoute, ServesMetricsAndHealth) {
    const auto render = [] { return std::string("x 1\n"); };
    const auto metrics = route_metrics_request("GET /metrics HTTP/1.1", render);
    EXPECT_EQ(metrics.status, 200);
    EXPECT_EQ(metrics.body, "x 1\n");
    EXPECT_EQ(metrics.content_type, "text/plain; version=0.0.4; charset=utf-8");
    EXPECT_EQ(route_metrics_request("GET /metrics?x=1 HTTP/1.0", render).status, 200);
    EXPECT_EQ(route_metrics_request("GET /healthz HTTP/1.1", render).body, "ok\n");
    EXPECT_EQ(route_metrics_request("GET /other HTTP/1.1", render).status, 404);
    EXPECT_EQ(route_metrics_request("POST /metrics HTTP/1.1", render).status, 405);
    EXPECT_EQ(route_metrics_request("garbage", render).status, 400);
    EXPECT_EQ(route_metrics_request("", render).status, 400);
}

}  // namespace
}  // namespace wardwatch
