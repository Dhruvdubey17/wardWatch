#include "wardwatch/server.hpp"

#include <chrono>
#include <set>
#include <string>
#include <thread>

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

#include "contracts.hpp"
#include "faults.hpp"
#include "memory_sink.hpp"
#include "mllp_client.hpp"

namespace wardwatch {
namespace {

using testing::ack_code;
using testing::acked_control_id;
using testing::MemorySink;
using testing::MllpClient;
using testing::read_contract;
using testing::replace_first;

std::string oru_with_control_id(int index) {
    return replace_first(read_contract("hl7/oru_r01.hl7"), "|SIM000007|",
                         "|C" + std::to_string(index) + "|");
}

ServerConfig test_config() {
    ServerConfig config;
    config.port = 0;
    return config;
}

TEST(Server, AcknowledgesAndPublishesAValidMessage) {
    MemorySink sink;
    Metrics metrics;
    Server server(test_config(), sink, metrics);
    ASSERT_TRUE(server.start().has_value());

    MllpClient client(server.port());
    client.send_message(read_contract("hl7/oru_r01.hl7"));
    const auto ack = client.read_ack();
    ASSERT_TRUE(ack.has_value());
    EXPECT_EQ(ack_code(*ack), "AA");
    EXPECT_EQ(acked_control_id(*ack), "SIM000007");

    server.request_stop();
    server.wait();
    const auto records = sink.records();
    ASSERT_EQ(records.size(), 1U);
    EXPECT_EQ(records[0].topic, Topic::validated);
    EXPECT_EQ(records[0].key, "MRN0001234");
    EXPECT_EQ(nlohmann::json::parse(records[0].payload).at("control_id"), "SIM000007");
    EXPECT_EQ(metrics.messages_accepted.load(), 1U);
    EXPECT_GE(sink.flushes(), 1);
}

TEST(Server, RejectsMalformedMessageWithArAndDeadLetter) {
    MemorySink sink;
    Metrics metrics;
    Server server(test_config(), sink, metrics);
    ASSERT_TRUE(server.start().has_value());

    MllpClient client(server.port());
    client.send_message(testing::fault_transforms().at("wrong_encoding_characters")(
        read_contract("hl7/oru_r01.hl7")));
    const auto ack = client.read_ack();
    ASSERT_TRUE(ack.has_value());
    EXPECT_EQ(ack_code(*ack), "AR");
    EXPECT_EQ(acked_control_id(*ack), "SIM000007");
    EXPECT_NE(ack->find("ENCODING_CHARACTERS_INVALID"), std::string::npos);

    server.request_stop();
    server.wait();
    const auto records = sink.records();
    ASSERT_EQ(records.size(), 1U);
    EXPECT_EQ(records[0].topic, Topic::deadletter);
    const auto payload = nlohmann::json::parse(records[0].payload);
    EXPECT_EQ(payload.at("stage"), "parse");
    EXPECT_EQ(payload.at("error_code"), "ENCODING_CHARACTERS_INVALID");
    EXPECT_EQ(metrics.messages_rejected
                  .at(static_cast<std::size_t>(ErrorCode::encoding_characters_invalid))
                  .load(),
              1U);
}

TEST(Server, OversizeFrameIsRejectedWithItsControlId) {
    MemorySink sink;
    Metrics metrics;
    ServerConfig config = test_config();
    config.max_frame_bytes = 512;
    config.oversize_keep_bytes = 256;
    Server server(config, sink, metrics);
    ASSERT_TRUE(server.start().has_value());

    MllpClient client(server.port());
    client.send_message(read_contract("hl7/oru_r01.hl7") + "NTE|1||" + std::string(2000, 'x') +
                        "\r");
    const auto ack = client.read_ack();
    ASSERT_TRUE(ack.has_value());
    EXPECT_EQ(ack_code(*ack), "AR");
    EXPECT_EQ(acked_control_id(*ack), "SIM000007");

    server.request_stop();
    server.wait();
    const auto records = sink.records();
    ASSERT_EQ(records.size(), 1U);
    const auto payload = nlohmann::json::parse(records[0].payload);
    EXPECT_EQ(payload.at("stage"), "frame");
    EXPECT_EQ(payload.at("error_code"), "FRAME_OVERSIZE");
    EXPECT_TRUE(payload.at("raw_truncated").get<bool>());
}

TEST(Server, AcksArriveInOrderOnOneConnection) {
    MemorySink sink;
    Metrics metrics;
    Server server(test_config(), sink, metrics);
    ASSERT_TRUE(server.start().has_value());

    MllpClient client(server.port());
    constexpr int kMessages = 500;
    for (int i = 0; i < kMessages; ++i) {
        client.send_message(oru_with_control_id(i));
    }
    for (int i = 0; i < kMessages; ++i) {
        const auto ack = client.read_ack();
        ASSERT_TRUE(ack.has_value()) << i;
        EXPECT_EQ(acked_control_id(*ack), "C" + std::to_string(i));
    }
    server.request_stop();
    server.wait();
    EXPECT_EQ(sink.records().size(), static_cast<std::size_t>(kMessages));
}

TEST(Server, SlowSinkCausesBackpressureButLosesNothing) {
    MemorySink sink(std::chrono::microseconds(200));
    Metrics metrics;
    ServerConfig config = test_config();
    config.ring_capacity = 4;
    Server server(config, sink, metrics);
    ASSERT_TRUE(server.start().has_value());

    MllpClient client(server.port());
    constexpr int kMessages = 300;
    std::thread sender([&] {
        for (int i = 0; i < kMessages; ++i) {
            client.send_message(oru_with_control_id(i));
        }
    });
    std::set<std::string> acked;
    for (int i = 0; i < kMessages; ++i) {
        const auto ack = client.read_ack();
        ASSERT_TRUE(ack.has_value()) << i;
        EXPECT_EQ(ack_code(*ack), "AA");
        acked.insert(acked_control_id(*ack));
    }
    sender.join();
    server.request_stop();
    server.wait();
    EXPECT_EQ(acked.size(), static_cast<std::size_t>(kMessages));
    EXPECT_EQ(sink.records().size(), static_cast<std::size_t>(kMessages));
    EXPECT_GT(metrics.backpressure_events.load(), 0U);
}

TEST(Server, StopMidStreamPublishesEverythingAckedAa) {
    MemorySink sink(std::chrono::microseconds(50));
    Metrics metrics;
    Server server(test_config(), sink, metrics);
    ASSERT_TRUE(server.start().has_value());

    MllpClient client(server.port());
    std::thread sender([&] {
        for (int i = 0; i < 2000; ++i) {
            try {
                client.send_message(oru_with_control_id(i));
            } catch (const std::runtime_error&) {
                return;  // the server stopped reading and closed the socket
            }
        }
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    server.request_stop();

    std::set<std::string> acked;
    while (const auto ack = client.read_ack(std::chrono::seconds(2))) {
        if (ack_code(*ack) == "AA") {
            acked.insert(acked_control_id(*ack));
        }
    }
    server.wait();
    sender.join();

    std::set<std::string> published;
    for (const auto& record : sink.records()) {
        published.insert(nlohmann::json::parse(record.payload).at("control_id").get<std::string>());
    }
    EXPECT_FALSE(acked.empty());
    for (const auto& control_id : acked) {
        EXPECT_TRUE(published.contains(control_id)) << control_id;
    }
}

TEST(Server, RetransmissionIsAckedButPublishedOnce) {
    MemorySink sink;
    Metrics metrics;
    Server server(test_config(), sink, metrics);
    ASSERT_TRUE(server.start().has_value());
    MllpClient client(server.port());
    client.send_message(read_contract("hl7/oru_r01.hl7"));
    client.send_message(read_contract("hl7/oru_r01.hl7"));
    EXPECT_EQ(ack_code(*client.read_ack()), "AA");
    EXPECT_EQ(ack_code(*client.read_ack()), "AA");
    server.request_stop();
    server.wait();
    EXPECT_EQ(sink.records().size(), 1U);
    EXPECT_EQ(metrics.messages_retransmitted.load(), 1U);
}

TEST(Server, HalfClosedClientStillGetsItsAcks) {
    MemorySink sink;
    Metrics metrics;
    Server server(test_config(), sink, metrics);
    ASSERT_TRUE(server.start().has_value());
    MllpClient client(server.port());
    for (int i = 0; i < 10; ++i) {
        client.send_message(oru_with_control_id(i));
    }
    client.shutdown_write();
    for (int i = 0; i < 10; ++i) {
        const auto ack = client.read_ack();
        ASSERT_TRUE(ack.has_value()) << i;
    }
    server.request_stop();
    server.wait();
}

TEST(Server, ReportsBindFailure) {
    MemorySink sink;
    Metrics metrics;
    Server first(test_config(), sink, metrics);
    ASSERT_TRUE(first.start().has_value());
    ServerConfig config = test_config();
    config.port = first.port();
    Server second(config, sink, metrics);
    const auto started = second.start();
    ASSERT_FALSE(started.has_value());
    EXPECT_NE(started.error().find("listen on"), std::string::npos);
}

}  // namespace
}  // namespace wardwatch
