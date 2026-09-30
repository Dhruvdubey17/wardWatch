#pragma once

#include <chrono>
#include <expected>
#include <memory>
#include <string>

#include "wardwatch/metrics.hpp"
#include "wardwatch/sink.hpp"

struct rd_kafka_s;
struct rd_kafka_topic_s;

namespace wardwatch {

struct KafkaSinkConfig {
    std::string bootstrap_servers = "127.0.0.1:9092";
    std::string client_id = "wardwatch-ingest";
    // How long publish() keeps retrying while the local producer queue is full.
    std::chrono::milliseconds queue_full_timeout{2000};
};

// Publishes with librdkafka's idempotent producer (acks=all, ordered,
// no duplicates on retry). Records are keyed by MRN so one patient's messages
// stay in one partition and in order. Delivery results arrive in a callback
// and are counted in DeliveryCounters.
class KafkaSink final : public Sink {
  public:
    [[nodiscard]] static std::expected<std::unique_ptr<KafkaSink>, std::string> create(
        const KafkaSinkConfig& config, DeliveryCounters& counters);

    KafkaSink(const KafkaSink&) = delete;
    KafkaSink& operator=(const KafkaSink&) = delete;
    KafkaSink(KafkaSink&&) = delete;
    KafkaSink& operator=(KafkaSink&&) = delete;
    ~KafkaSink() override;

    bool publish(Topic topic, std::string_view key, std::string_view payload) override;
    bool flush(std::chrono::milliseconds timeout) override;

  private:
    KafkaSink(rd_kafka_s* producer, rd_kafka_topic_s* validated, rd_kafka_topic_s* deadletter,
              std::chrono::milliseconds queue_full_timeout)
        : producer_(producer),
          validated_(validated),
          deadletter_(deadletter),
          queue_full_timeout_(queue_full_timeout) {}

    rd_kafka_s* producer_;
    rd_kafka_topic_s* validated_;
    rd_kafka_topic_s* deadletter_;
    std::chrono::milliseconds queue_full_timeout_;
};

}  // namespace wardwatch
