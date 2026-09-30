#include "wardwatch/kafka_sink.hpp"

#include <array>
#include <format>
#include <iostream>

#include <librdkafka/rdkafka.h>

namespace wardwatch {
namespace {

// rd_kafka_conf_t is freed by rd_kafka_new on success and by us otherwise.
struct ConfDeleter {
    void operator()(rd_kafka_conf_t* conf) const noexcept { rd_kafka_conf_destroy(conf); }
};

void on_delivery(rd_kafka_t* /*producer*/, const rd_kafka_message_t* message, void* opaque) {
    auto* counters = static_cast<DeliveryCounters*>(opaque);
    if (message->err == RD_KAFKA_RESP_ERR_NO_ERROR) {
        counters->delivered.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    counters->failed.fetch_add(1, std::memory_order_relaxed);
    std::cerr << std::format(R"({{"event":"kafka_delivery_failed","topic":"{}","error":"{}"}})",
                             rd_kafka_topic_name(message->rkt), rd_kafka_err2str(message->err))
              << '\n';
}

}  // namespace

std::expected<std::unique_ptr<KafkaSink>, std::string> KafkaSink::create(
    const KafkaSinkConfig& config, DeliveryCounters& counters) {
    std::unique_ptr<rd_kafka_conf_t, ConfDeleter> conf(rd_kafka_conf_new());
    std::array<char, 512> error{};
    const std::array<std::pair<const char*, std::string>, 5> settings{{
        {"bootstrap.servers", config.bootstrap_servers},
        {"client.id", config.client_id},
        // Idempotence implies acks=all and max.in.flight <= 5 with ordering
        // kept, so a retried batch never duplicates or reorders a patient.
        {"enable.idempotence", "true"},
        {"linger.ms", "5"},
        {"message.timeout.ms", "30000"},
    }};
    for (const auto& [name, value] : settings) {
        if (rd_kafka_conf_set(conf.get(), name, value.c_str(), error.data(), error.size()) !=
            RD_KAFKA_CONF_OK) {
            return std::unexpected(std::format("kafka config {}: {}", name, error.data()));
        }
    }
    rd_kafka_conf_set_dr_msg_cb(conf.get(), on_delivery);
    rd_kafka_conf_set_opaque(conf.get(), &counters);

    rd_kafka_t* producer = rd_kafka_new(RD_KAFKA_PRODUCER, conf.get(), error.data(), error.size());
    if (producer == nullptr) {
        return std::unexpected(std::format("kafka producer: {}", error.data()));
    }
    [[maybe_unused]] auto* owned_by_producer = conf.release();
    rd_kafka_topic_t* validated =
        rd_kafka_topic_new(producer, std::string(topic_name(Topic::validated)).c_str(), nullptr);
    rd_kafka_topic_t* deadletter =
        rd_kafka_topic_new(producer, std::string(topic_name(Topic::deadletter)).c_str(), nullptr);
    if (validated == nullptr || deadletter == nullptr) {
        if (validated != nullptr) {
            rd_kafka_topic_destroy(validated);
        }
        if (deadletter != nullptr) {
            rd_kafka_topic_destroy(deadletter);
        }
        rd_kafka_destroy(producer);
        return std::unexpected(std::string("kafka topic handles could not be created"));
    }
    return std::unique_ptr<KafkaSink>(
        new KafkaSink(producer, validated, deadletter, config.queue_full_timeout));
}

KafkaSink::~KafkaSink() {
    rd_kafka_flush(producer_, 5000);
    rd_kafka_topic_destroy(validated_);
    rd_kafka_topic_destroy(deadletter_);
    rd_kafka_destroy(producer_);
}

bool KafkaSink::publish(Topic topic, std::string_view key, std::string_view payload) {
    const auto deadline = std::chrono::steady_clock::now() + queue_full_timeout_;
    while (true) {
        // RD_KAFKA_MSG_F_COPY: librdkafka copies the bytes, so the caller's
        // buffers can be reused as soon as this returns. The unassigned
        // partition lets the default partitioner hash the key.
        // The C API takes a non-const pointer but never writes with F_COPY.
        auto* bytes =
            const_cast<char*>(payload.data());  // NOLINT(cppcoreguidelines-pro-type-const-cast)
        rd_kafka_topic_t* handle = topic == Topic::validated ? validated_ : deadletter_;
        const int result = rd_kafka_produce(handle, RD_KAFKA_PARTITION_UA, RD_KAFKA_MSG_F_COPY,
                                            bytes, payload.size(), key.data(), key.size(), nullptr);
        // Serve delivery callbacks for earlier records without blocking.
        rd_kafka_poll(producer_, 0);
        if (result == RD_KAFKA_RESP_ERR_NO_ERROR) {
            return true;
        }
        if (result != RD_KAFKA_RESP_ERR__QUEUE_FULL ||
            std::chrono::steady_clock::now() >= deadline) {
            return false;
        }
        rd_kafka_poll(producer_, 50);
    }
}

bool KafkaSink::flush(std::chrono::milliseconds timeout) {
    return rd_kafka_flush(producer_, static_cast<int>(timeout.count())) ==
           RD_KAFKA_RESP_ERR_NO_ERROR;
}

}  // namespace wardwatch
