#include "wardwatch/cli.hpp"

#include <bit>
#include <charconv>
#include <format>
#include <limits>

namespace wardwatch {
namespace {

template <typename Number>
std::expected<Number, std::string> parse_number(std::string_view flag, std::string_view text) {
    Number value{};
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size()) {
        return std::unexpected(std::format("{} needs a whole number, got '{}'", flag, text));
    }
    return value;
}

}  // namespace

std::string_view usage() noexcept {
    return "usage: wardwatch-ingest [options]\n"
           "  --bind ADDRESS          MLLP listen address (default 127.0.0.1)\n"
           "  --port PORT             MLLP listen port, 0 for any (default 2575)\n"
           "  --metrics-bind ADDRESS  /metrics listen address (default 127.0.0.1)\n"
           "  --metrics-port PORT     /metrics listen port (default 9464)\n"
           "  --sink null|file|kafka  where messages go (default null)\n"
           "  --file-sink-dir DIR     directory for the file sink (default .)\n"
           "  --kafka-brokers LIST    bootstrap servers for the Kafka sink\n"
           "  --max-frame-bytes N     largest accepted MLLP frame (default 1048576)\n"
           "  --ring-capacity N       power-of-two ring size (default 4096)\n"
           "  --duplicate-window N    control IDs remembered for duplicates (default 100000)\n"
           "  --drain-timeout-ms N    shutdown wait for ACKs and the sink (default 5000)\n"
           "  --help                  print this text\n";
}

std::expected<CliOptions, std::string> parse_cli(std::span<const std::string_view> args) {
    CliOptions options;
    for (std::size_t i = 0; i < args.size(); ++i) {
        const std::string_view flag = args[i];
        if (flag == "--help" || flag == "-h") {
            options.show_help = true;
            continue;
        }
        if (i + 1 >= args.size()) {
            return std::unexpected(std::format("{} needs a value", flag));
        }
        const std::string_view value = args[++i];
        const auto port = [&](std::uint16_t& target) -> std::expected<void, std::string> {
            auto parsed = parse_number<std::uint16_t>(flag, value);
            if (!parsed) {
                return std::unexpected(parsed.error());
            }
            target = *parsed;
            return {};
        };
        const auto size = [&](std::size_t& target) -> std::expected<void, std::string> {
            auto parsed = parse_number<std::size_t>(flag, value);
            if (!parsed) {
                return std::unexpected(parsed.error());
            }
            target = *parsed;
            return {};
        };
        std::expected<void, std::string> applied;
        if (flag == "--bind") {
            options.server.bind_address = value;
        } else if (flag == "--port") {
            applied = port(options.server.port);
        } else if (flag == "--metrics-bind") {
            options.metrics_bind_address = value;
        } else if (flag == "--metrics-port") {
            applied = port(options.metrics_port);
        } else if (flag == "--sink") {
            if (value == "null") {
                options.sink = SinkKind::null;
            } else if (value == "file") {
                options.sink = SinkKind::file;
            } else if (value == "kafka") {
                options.sink = SinkKind::kafka;
            } else {
                return std::unexpected(
                    std::format("--sink must be null, file or kafka, got '{}'", value));
            }
        } else if (flag == "--file-sink-dir") {
            options.file_sink_directory = value;
        } else if (flag == "--kafka-brokers") {
            options.kafka_brokers = value;
        } else if (flag == "--max-frame-bytes") {
            applied = size(options.server.max_frame_bytes);
        } else if (flag == "--ring-capacity") {
            applied = size(options.server.ring_capacity);
            if (applied && !std::has_single_bit(options.server.ring_capacity)) {
                return std::unexpected("--ring-capacity must be a power of two");
            }
        } else if (flag == "--duplicate-window") {
            applied = size(options.server.duplicate_window);
        } else if (flag == "--drain-timeout-ms") {
            auto parsed = parse_number<std::int64_t>(flag, value);
            if (!parsed) {
                return std::unexpected(parsed.error());
            }
            options.server.drain_timeout = std::chrono::milliseconds(*parsed);
        } else {
            return std::unexpected(std::format("unknown option {}", flag));
        }
        if (!applied) {
            return std::unexpected(applied.error());
        }
    }
    return options;
}

}  // namespace wardwatch
