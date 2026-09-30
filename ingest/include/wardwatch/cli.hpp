#pragma once

#include <cstdint>
#include <expected>
#include <span>
#include <string>
#include <string_view>

#include "wardwatch/server.hpp"

namespace wardwatch {

enum class SinkKind : std::uint8_t { null, file, kafka };

struct CliOptions {
    ServerConfig server;
    SinkKind sink = SinkKind::null;
    std::string file_sink_directory = ".";
    std::string kafka_brokers = "127.0.0.1:9092";
    std::string metrics_bind_address = "127.0.0.1";
    std::uint16_t metrics_port = 9464;
    bool show_help = false;
};

[[nodiscard]] std::string_view usage() noexcept;

// Parses --name value pairs. Unknown flags and malformed numbers are errors.
[[nodiscard]] std::expected<CliOptions, std::string> parse_cli(
    std::span<const std::string_view> args);

}  // namespace wardwatch
