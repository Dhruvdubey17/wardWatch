#include "wardwatch/file_sink.hpp"

#include <format>
#include <system_error>

#include <nlohmann/json.hpp>

namespace wardwatch {

std::expected<std::unique_ptr<FileSink>, std::string> FileSink::open(
    const std::filesystem::path& directory, DeliveryCounters& counters) {
    std::error_code error;
    std::filesystem::create_directories(directory, error);
    if (error) {
        return std::unexpected(
            std::format("cannot create {}: {}", directory.string(), error.message()));
    }
    const auto open_topic = [&](Topic topic) {
        return std::ofstream(directory / std::format("{}.jsonl", topic_name(topic)),
                             std::ios::app | std::ios::binary);
    };
    std::ofstream validated = open_topic(Topic::validated);
    std::ofstream deadletter = open_topic(Topic::deadletter);
    if (!validated || !deadletter) {
        return std::unexpected(std::format("cannot open sink files in {}", directory.string()));
    }
    return std::unique_ptr<FileSink>(
        new FileSink(std::move(validated), std::move(deadletter), counters));
}

bool FileSink::publish(Topic topic, std::string_view key, std::string_view payload) {
    std::ofstream& out = topic == Topic::validated ? validated_ : deadletter_;
    // The payload is already a JSON document, so it is spliced in as is.
    out << R"({"key":)" << nlohmann::json(std::string(key)).dump() << R"(,"value":)" << payload
        << "}\n";
    out.flush();
    const bool written = out.good();
    (written ? counters_->delivered : counters_->failed).fetch_add(1, std::memory_order_relaxed);
    return written;
}

bool FileSink::flush(std::chrono::milliseconds /*timeout*/) {
    validated_.flush();
    deadletter_.flush();
    return validated_.good() && deadletter_.good();
}

}  // namespace wardwatch
