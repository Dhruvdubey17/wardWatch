#include <array>
#include <chrono>
#include <cstdlib>
#include <map>
#include <memory>
#include <string>

#include <gtest/gtest.h>
#include <librdkafka/rdkafka.h>
#include <nlohmann/json.hpp>

#include "contracts.hpp"
#include "faults.hpp"
#include "ingest_process.hpp"
#include "mllp_client.hpp"

namespace wardwatch {
namespace {

using testing::ack_code;
using testing::replace_first;

struct ConsumerDeleter {
    void operator()(rd_kafka_t* consumer) const noexcept {
        rd_kafka_consumer_close(consumer);
        rd_kafka_destroy(consumer);
    }
};

std::unique_ptr<rd_kafka_t, ConsumerDeleter> make_consumer(const std::string& brokers,
                                                           const std::string& group) {
    rd_kafka_conf_t* conf = rd_kafka_conf_new();
    std::array<char, 512> error{};
    rd_kafka_conf_set(conf, "bootstrap.servers", brokers.c_str(), error.data(), error.size());
    rd_kafka_conf_set(conf, "group.id", group.c_str(), error.data(), error.size());
    rd_kafka_conf_set(conf, "auto.offset.reset", "earliest", error.data(), error.size());
    rd_kafka_t* consumer = rd_kafka_new(RD_KAFKA_CONSUMER, conf, error.data(), error.size());
    rd_kafka_poll_set_consumer(consumer);
    rd_kafka_topic_partition_list_t* topics = rd_kafka_topic_partition_list_new(2);
    rd_kafka_topic_partition_list_add(topics, "hl7.validated", RD_KAFKA_PARTITION_UA);
    rd_kafka_topic_partition_list_add(topics, "hl7.deadletter", RD_KAFKA_PARTITION_UA);
    rd_kafka_subscribe(consumer, topics);
    rd_kafka_topic_partition_list_destroy(topics);
    return std::unique_ptr<rd_kafka_t, ConsumerDeleter>(consumer);
}

// Needs a broker: set WARDWATCH_KAFKA_BROKERS (CI starts one as a service).
// Selected with `ctest --preset kafka`; the default integration run skips it.
TEST(KafkaRoundTrip, ValidatedAndDeadLetterRecordsArriveKeyedByMrn) {
    const char* brokers = std::getenv("WARDWATCH_KAFKA_BROKERS");
    if (brokers == nullptr) {
        GTEST_SKIP() << "WARDWATCH_KAFKA_BROKERS is not set, so there is no broker to test against";
    }
    const std::string run_id =
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
    testing::IngestProcess ingest(
        {"--port", "0", "--metrics-port", "0", "--sink", "kafka", "--kafka-brokers", brokers});
    testing::MllpClient client(ingest.port());
    const std::string base = testing::read_contract("hl7/oru_r01.hl7");
    const std::string good_id = "KG" + run_id.substr(run_id.size() - 12);
    const std::string bad_id = "KB" + run_id.substr(run_id.size() - 12);
    client.send_message(replace_first(base, "|SIM000007|", "|" + good_id + "|"));
    client.send_message(replace_first(replace_first(base, "|SIM000007|", "|" + bad_id + "|"),
                                      "LN||112|", "LN||high|"));
    EXPECT_EQ(ack_code(*client.read_ack()), "AA");
    EXPECT_EQ(ack_code(*client.read_ack()), "AE");
    ASSERT_EQ(ingest.terminate(), 0);

    auto consumer = make_consumer(brokers, "wardwatch-ingest-test-" + run_id);
    std::map<std::string, nlohmann::json> found;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(60);
    while (found.size() < 2 && std::chrono::steady_clock::now() < deadline) {
        rd_kafka_message_t* message = rd_kafka_consumer_poll(consumer.get(), 500);
        if (message == nullptr) {
            continue;
        }
        if (message->err == RD_KAFKA_RESP_ERR_NO_ERROR) {
            const std::string key(static_cast<const char*>(message->key), message->key_len);
            auto value = nlohmann::json::parse(
                std::string(static_cast<const char*>(message->payload), message->len));
            const std::string topic = rd_kafka_topic_name(message->rkt);
            const auto control_id = value.at("control_id");
            if (control_id == good_id || control_id == bad_id) {
                value["_topic"] = topic;
                value["_key"] = key;
                found[control_id.get<std::string>()] = value;
            }
        }
        rd_kafka_message_destroy(message);
    }
    ASSERT_TRUE(found.contains(good_id));
    ASSERT_TRUE(found.contains(bad_id));
    EXPECT_EQ(found.at(good_id).at("_topic"), "hl7.validated");
    EXPECT_EQ(found.at(good_id).at("_key"), "MRN0001234");
    EXPECT_EQ(found.at(bad_id).at("_topic"), "hl7.deadletter");
    EXPECT_EQ(found.at(bad_id).at("error_code"), "VALUE_TYPE_MISMATCH");
}

}  // namespace
}  // namespace wardwatch
