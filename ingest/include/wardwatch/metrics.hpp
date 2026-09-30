#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>

#include "wardwatch/error.hpp"

namespace wardwatch {

// A histogram with fixed bucket bounds, updated with relaxed atomics. Readers
// may see a count and sum from slightly different moments, which Prometheus
// tolerates.
class LatencyHistogram {
  public:
    // Upper bounds in microseconds. Parsing and validating one hourly ORU
    // takes a few microseconds, so the low buckets matter most.
    static constexpr std::array<double, 12> kBoundsMicros{1,   2,   5,    10,   25,    50,
                                                          100, 250, 1000, 2500, 10000, 100000};

    void observe(double micros) noexcept;

    [[nodiscard]] std::uint64_t bucket(std::size_t index) const noexcept {
        return buckets_[index].load(std::memory_order_relaxed);
    }
    [[nodiscard]] std::uint64_t count() const noexcept {
        return count_.load(std::memory_order_relaxed);
    }
    [[nodiscard]] double sum_micros() const noexcept {
        return static_cast<double>(sum_nanos_.load(std::memory_order_relaxed)) / 1000.0;
    }

  private:
    // Non-cumulative per-bucket counts; the last slot counts values above
    // every bound. Rendering makes them cumulative.
    std::array<std::atomic<std::uint64_t>, kBoundsMicros.size() + 1> buckets_{};
    std::atomic<std::uint64_t> count_{0};
    std::atomic<std::uint64_t> sum_nanos_{0};
};

// Outcomes a sink reports once it knows them. For Kafka that is in the
// delivery callback, well after publish() returned.
struct DeliveryCounters {
    std::atomic<std::uint64_t> delivered{0};
    std::atomic<std::uint64_t> failed{0};
};

// Counters shared by the I/O and validation threads. Every update is a relaxed
// increment: the values are only read for reporting, never to coordinate.
struct Metrics {
    std::atomic<std::uint64_t> bytes_read{0};
    std::atomic<std::uint64_t> connections_accepted{0};
    std::atomic<std::int64_t> connections_open{0};
    std::atomic<std::uint64_t> messages_received{0};
    std::atomic<std::uint64_t> messages_accepted{0};
    std::atomic<std::uint64_t> messages_retransmitted{0};
    std::array<std::atomic<std::uint64_t>, kErrorCodeCount> messages_rejected{};
    std::array<std::atomic<std::uint64_t>, kWarningCodeCount> warnings{};
    std::atomic<std::uint64_t> backpressure_events{0};
    std::atomic<std::uint64_t> bytes_outside_frames{0};
    // Records the sink refused outright; the message went unacknowledged.
    std::atomic<std::uint64_t> sink_refused{0};
    DeliveryCounters delivery;
    LatencyHistogram parse_latency;

    void count_rejection(ErrorCode code) noexcept {
        messages_rejected[static_cast<std::size_t>(code)].fetch_add(1, std::memory_order_relaxed);
    }
    void count_warning(WarningCode code) noexcept {
        warnings[static_cast<std::size_t>(code)].fetch_add(1, std::memory_order_relaxed);
    }
};

// Gauges that belong to the server rather than the counters above.
struct RuntimeGauges {
    std::size_t inbound_ring_size = 0;
    std::size_t inbound_ring_capacity = 0;
    std::size_t outbound_ring_size = 0;
    std::size_t outbound_ring_capacity = 0;
};

// Prometheus text exposition format 0.0.4.
[[nodiscard]] std::string render_metrics(const Metrics& metrics, const RuntimeGauges& gauges);

}  // namespace wardwatch
