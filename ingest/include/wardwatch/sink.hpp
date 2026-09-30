#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <string_view>

namespace wardwatch {

enum class Topic : std::uint8_t { validated, deadletter };

[[nodiscard]] constexpr std::string_view topic_name(Topic topic) noexcept {
    return topic == Topic::validated ? "hl7.validated" : "hl7.deadletter";
}

// Where accepted and rejected messages go. publish is called only from the
// validation thread. A publish that returns true has been handed to the sink;
// flush waits until everything handed over is durable or the timeout passes.
class Sink {
  public:
    Sink() = default;
    Sink(const Sink&) = delete;
    Sink& operator=(const Sink&) = delete;
    Sink(Sink&&) = delete;
    Sink& operator=(Sink&&) = delete;
    virtual ~Sink() = default;

    [[nodiscard]] virtual bool publish(Topic topic, std::string_view key,
                                       std::string_view payload) = 0;
    [[nodiscard]] virtual bool flush(std::chrono::milliseconds timeout) = 0;
};

// Discards everything. Used by benchmarks to measure the pipeline alone.
class NullSink final : public Sink {
  public:
    bool publish(Topic /*topic*/, std::string_view /*key*/, std::string_view /*payload*/) override {
        published_.fetch_add(1, std::memory_order_relaxed);
        return true;
    }
    bool flush(std::chrono::milliseconds /*timeout*/) override { return true; }
    [[nodiscard]] std::uint64_t published() const noexcept {
        return published_.load(std::memory_order_relaxed);
    }

  private:
    std::atomic<std::uint64_t> published_{0};
};

}  // namespace wardwatch
