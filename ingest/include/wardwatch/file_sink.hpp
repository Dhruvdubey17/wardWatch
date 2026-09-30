#pragma once

#include <expected>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>

#include "wardwatch/metrics.hpp"
#include "wardwatch/sink.hpp"

namespace wardwatch {

// Appends one JSON line per record to <directory>/<topic>.jsonl, in the form
// {"key": "...", "value": {...payload...}}. Each line is flushed as it is
// written so tests can read the files while the server runs.
class FileSink final : public Sink {
  public:
    [[nodiscard]] static std::expected<std::unique_ptr<FileSink>, std::string> open(
        const std::filesystem::path& directory, DeliveryCounters& counters);

    bool publish(Topic topic, std::string_view key, std::string_view payload) override;
    bool flush(std::chrono::milliseconds timeout) override;

  private:
    FileSink(std::ofstream validated, std::ofstream deadletter, DeliveryCounters& counters)
        : validated_(std::move(validated)),
          deadletter_(std::move(deadletter)),
          counters_(&counters) {}

    std::ofstream validated_;
    std::ofstream deadletter_;
    DeliveryCounters* counters_;
};

}  // namespace wardwatch
