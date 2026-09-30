#include "wardwatch/metrics.hpp"

#include <algorithm>
#include <format>
#include <iterator>
#include <string_view>

namespace wardwatch {
namespace {

void append_header(std::string& out, std::string_view name, std::string_view type,
                   std::string_view help) {
    std::format_to(std::back_inserter(out), "# HELP {} {}\n# TYPE {} {}\n", name, help, name, type);
}

template <typename Value>
void append_sample(std::string& out, std::string_view name, std::string_view type,
                   std::string_view help, Value value) {
    append_header(out, name, type, help);
    std::format_to(std::back_inserter(out), "{} {}\n", name, value);
}

}  // namespace

void LatencyHistogram::observe(double micros) noexcept {
    const auto* bound = std::ranges::lower_bound(kBoundsMicros, micros);
    const auto index = static_cast<std::size_t>(bound - kBoundsMicros.begin());
    buckets_[index].fetch_add(1, std::memory_order_relaxed);
    count_.fetch_add(1, std::memory_order_relaxed);
    const double nanos = std::max(0.0, micros * 1000.0);
    sum_nanos_.fetch_add(static_cast<std::uint64_t>(nanos), std::memory_order_relaxed);
}

std::string render_metrics(const Metrics& metrics, const RuntimeGauges& gauges) {
    std::string out;
    const auto load = [](const auto& counter) { return counter.load(std::memory_order_relaxed); };

    append_sample(out, "wardwatch_ingest_bytes_read_total", "counter",
                  "Bytes read from MLLP connections.", load(metrics.bytes_read));
    append_sample(out, "wardwatch_ingest_connections_accepted_total", "counter",
                  "MLLP connections accepted.", load(metrics.connections_accepted));
    append_sample(out, "wardwatch_ingest_connections_open", "gauge", "MLLP connections open now.",
                  load(metrics.connections_open));
    append_sample(out, "wardwatch_ingest_messages_received_total", "counter",
                  "Framed messages taken off the wire.", load(metrics.messages_received));
    append_sample(out, "wardwatch_ingest_messages_accepted_total", "counter",
                  "Messages accepted and published to hl7.validated.",
                  load(metrics.messages_accepted));
    append_sample(out, "wardwatch_ingest_messages_retransmitted_total", "counter",
                  "Retransmitted messages acknowledged again without republishing.",
                  load(metrics.messages_retransmitted));

    append_header(out, "wardwatch_ingest_messages_rejected_total", "counter",
                  "Messages rejected, by error code.");
    for (std::size_t i = 0; i < kErrorCodeCount; ++i) {
        std::format_to(std::back_inserter(out),
                       "wardwatch_ingest_messages_rejected_total{{code=\"{}\"}} {}\n",
                       to_string(static_cast<ErrorCode>(i)), load(metrics.messages_rejected.at(i)));
    }
    append_header(out, "wardwatch_ingest_warnings_total", "counter",
                  "Validation warnings attached to accepted messages, by code.");
    for (std::size_t i = 0; i < kWarningCodeCount; ++i) {
        std::format_to(std::back_inserter(out),
                       "wardwatch_ingest_warnings_total{{code=\"{}\"}} {}\n",
                       to_string(static_cast<WarningCode>(i)), load(metrics.warnings.at(i)));
    }

    append_sample(out, "wardwatch_ingest_backpressure_events_total", "counter",
                  "Times a connection stopped being read because the inbound ring was full.",
                  load(metrics.backpressure_events));
    append_sample(out, "wardwatch_ingest_bytes_outside_frames_total", "counter",
                  "Bytes received outside any MLLP frame and dropped.",
                  load(metrics.bytes_outside_frames));
    append_sample(out, "wardwatch_ingest_sink_delivered_total", "counter",
                  "Records the sink confirmed as delivered.", load(metrics.delivery.delivered));
    append_sample(out, "wardwatch_ingest_sink_failed_total", "counter",
                  "Records the sink failed to deliver after accepting them.",
                  load(metrics.delivery.failed));
    append_sample(out, "wardwatch_ingest_sink_refused_total", "counter",
                  "Records the sink refused at publish time; those messages were not acknowledged.",
                  load(metrics.sink_refused));

    append_header(out, "wardwatch_ingest_ring_occupancy", "gauge",
                  "Items waiting in each SPSC ring.");
    std::format_to(std::back_inserter(out),
                   "wardwatch_ingest_ring_occupancy{{ring=\"inbound\"}} {}\n"
                   "wardwatch_ingest_ring_occupancy{{ring=\"outbound\"}} {}\n",
                   gauges.inbound_ring_size, gauges.outbound_ring_size);
    append_header(out, "wardwatch_ingest_ring_capacity", "gauge", "Capacity of each SPSC ring.");
    std::format_to(std::back_inserter(out),
                   "wardwatch_ingest_ring_capacity{{ring=\"inbound\"}} {}\n"
                   "wardwatch_ingest_ring_capacity{{ring=\"outbound\"}} {}\n",
                   gauges.inbound_ring_capacity, gauges.outbound_ring_capacity);

    const auto& histogram = metrics.parse_latency;
    append_header(out, "wardwatch_ingest_parse_latency_seconds", "histogram",
                  "Time to parse, validate and serialize one message.");
    std::uint64_t cumulative = 0;
    for (std::size_t i = 0; i < LatencyHistogram::kBoundsMicros.size(); ++i) {
        cumulative += histogram.bucket(i);
        std::format_to(std::back_inserter(out),
                       "wardwatch_ingest_parse_latency_seconds_bucket{{le=\"{}\"}} {}\n",
                       LatencyHistogram::kBoundsMicros.at(i) / 1e6, cumulative);
    }
    cumulative += histogram.bucket(LatencyHistogram::kBoundsMicros.size());
    std::format_to(std::back_inserter(out),
                   "wardwatch_ingest_parse_latency_seconds_bucket{{le=\"+Inf\"}} {}\n"
                   "wardwatch_ingest_parse_latency_seconds_sum {}\n"
                   "wardwatch_ingest_parse_latency_seconds_count {}\n",
                   cumulative, histogram.sum_micros() / 1e6, histogram.count());
    return out;
}

}  // namespace wardwatch
