#include "wardwatch/cli.hpp"

#include <string_view>
#include <vector>

#include <gtest/gtest.h>

namespace wardwatch {
namespace {

std::expected<CliOptions, std::string> parse(std::vector<std::string_view> args) {
    return parse_cli(args);
}

TEST(Cli, DefaultsBindToLoopback) {
    const auto options = parse({});
    ASSERT_TRUE(options.has_value());
    EXPECT_EQ(options->server.bind_address, "127.0.0.1");
    EXPECT_EQ(options->metrics_bind_address, "127.0.0.1");
    EXPECT_EQ(options->server.port, 2575);
    EXPECT_EQ(options->sink, SinkKind::null);
}

TEST(Cli, ParsesEveryOption) {
    const auto options = parse({"--bind",
                                "127.0.0.2",
                                "--port",
                                "0",
                                "--metrics-bind",
                                "127.0.0.3",
                                "--metrics-port",
                                "9999",
                                "--sink",
                                "file",
                                "--file-sink-dir",
                                "/tmp/x",
                                "--kafka-brokers",
                                "kafka:9092",
                                "--max-frame-bytes",
                                "2048",
                                "--ring-capacity",
                                "64",
                                "--duplicate-window",
                                "10",
                                "--drain-timeout-ms",
                                "250"});
    ASSERT_TRUE(options.has_value()) << options.error();
    EXPECT_EQ(options->server.bind_address, "127.0.0.2");
    EXPECT_EQ(options->server.port, 0);
    EXPECT_EQ(options->metrics_bind_address, "127.0.0.3");
    EXPECT_EQ(options->metrics_port, 9999);
    EXPECT_EQ(options->sink, SinkKind::file);
    EXPECT_EQ(options->file_sink_directory, "/tmp/x");
    EXPECT_EQ(options->kafka_brokers, "kafka:9092");
    EXPECT_EQ(options->server.max_frame_bytes, 2048U);
    EXPECT_EQ(options->server.ring_capacity, 64U);
    EXPECT_EQ(options->server.duplicate_window, 10U);
    EXPECT_EQ(options->server.drain_timeout, std::chrono::milliseconds(250));
}

TEST(Cli, Help) {
    const auto options = parse({"--help"});
    ASSERT_TRUE(options.has_value());
    EXPECT_TRUE(options->show_help);
    EXPECT_NE(usage().find("--sink"), std::string_view::npos);
}

struct BadArgs {
    std::string_view name;
    std::vector<std::string_view> args;
    std::string_view message;
};

class InvalidCli : public ::testing::TestWithParam<BadArgs> {};

TEST_P(InvalidCli, IsRejected) {
    const auto options = parse(GetParam().args);
    ASSERT_FALSE(options.has_value());
    EXPECT_NE(options.error().find(GetParam().message), std::string::npos) << options.error();
}

INSTANTIATE_TEST_SUITE_P(
    Table, InvalidCli,
    ::testing::Values(BadArgs{"unknown", {"--nope", "1"}, "unknown option --nope"},
                      BadArgs{"missing_value", {"--port"}, "--port needs a value"},
                      BadArgs{"port_text", {"--port", "abc"}, "whole number"},
                      BadArgs{"port_too_big", {"--port", "70000"}, "whole number"},
                      BadArgs{"negative_size", {"--ring-capacity", "-1"}, "whole number"},
                      BadArgs{"ring_not_power_of_two", {"--ring-capacity", "100"}, "power of two"},
                      BadArgs{"bad_sink", {"--sink", "s3"}, "--sink must be"},
                      BadArgs{"drain_text", {"--drain-timeout-ms", "soon"}, "whole number"}),
    [](const auto& info) { return std::string(info.param.name); });

}  // namespace
}  // namespace wardwatch
