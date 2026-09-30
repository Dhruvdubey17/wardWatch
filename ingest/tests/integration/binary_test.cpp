#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <vector>

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

#include "contracts.hpp"
#include "faults.hpp"
#include "http_get.hpp"
#include "ingest_process.hpp"
#include "mllp_client.hpp"

namespace wardwatch {
namespace {

using nlohmann::json;
using testing::ack_code;
using testing::acked_control_id;
using testing::IngestProcess;
using testing::MllpClient;
using testing::read_contract;
using testing::replace_first;

std::string oru_with_control_id(const std::string& control_id) {
    return replace_first(read_contract("hl7/oru_r01.hl7"), "|SIM000007|", "|" + control_id + "|");
}

std::vector<json> read_lines(const std::filesystem::path& path) {
    std::ifstream input(path);
    std::vector<json> lines;
    for (std::string line; std::getline(input, line);) {
        lines.push_back(json::parse(line));
    }
    return lines;
}

class IngestBinary : public ::testing::Test {
  protected:
    void SetUp() override {
        const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
        directory_ = std::filesystem::temp_directory_path() /
                     ("wardwatch-binary-" + std::to_string(::getpid()) + "-" + info->name());
        std::filesystem::remove_all(directory_);
        std::filesystem::create_directories(directory_);
    }
    void TearDown() override { std::filesystem::remove_all(directory_); }

    std::vector<std::string> args(std::vector<std::string> extra = {}) const {
        std::vector<std::string> all{"--port", "0",    "--metrics-port",  "0",
                                     "--sink", "file", "--file-sink-dir", directory_.string()};
        all.insert(all.end(), extra.begin(), extra.end());
        return all;
    }

    std::vector<json> validated() const { return read_lines(directory_ / "hl7.validated.jsonl"); }
    std::vector<json> deadletter() const { return read_lines(directory_ / "hl7.deadletter.jsonl"); }

    std::filesystem::path directory_;
};

TEST_F(IngestBinary, WellFormedStreamIsAcceptedAndWrittenInOrder) {
    IngestProcess ingest(args());
    MllpClient client(ingest.port());
    for (const char* sample : {"hl7/adt_a01.hl7", "hl7/oru_r01.hl7", "hl7/adt_a03.hl7"}) {
        client.send_message(read_contract(sample));
    }
    for (int i = 0; i < 100; ++i) {
        client.send_message(oru_with_control_id("W" + std::to_string(i)));
    }
    for (int i = 0; i < 103; ++i) {
        const auto ack = client.read_ack();
        ASSERT_TRUE(ack.has_value()) << i;
        EXPECT_EQ(ack_code(*ack), "AA") << *ack;
    }
    EXPECT_EQ(ingest.terminate(), 0);

    const auto lines = validated();
    ASSERT_EQ(lines.size(), 103U);
    EXPECT_EQ(lines[0].at("value").at("control_id"), "SIM000001");
    EXPECT_EQ(lines[1].at("value").at("control_id"), "SIM000007");
    EXPECT_EQ(lines[102].at("value").at("control_id"), "W99");
    for (const auto& line : lines) {
        EXPECT_EQ(line.at("key"), "MRN0001234");
    }
    auto example = testing::read_contract_json("examples/hl7.validated/oru_r01.json");
    auto written = lines[1].at("value");
    example.erase("received_at");
    written.erase("received_at");
    EXPECT_EQ(written, example);
    EXPECT_TRUE(deadletter().empty());
}

// Every catalogued fault, sent through the real binary, produces the ACK code
// and the dead-letter entry or warning that contracts/fault_catalog.json lists.
TEST_F(IngestBinary, EveryCataloguedFaultProducesItsAckAndRecord) {
    IngestProcess ingest(args({"--max-frame-bytes", "2048"}));
    MllpClient client(ingest.port());
    const auto catalog = testing::read_contract_json("fault_catalog.json");
    const auto& transforms = testing::fault_transforms();

    struct Expectation {
        std::string name;
        std::string control_id;
        json fault;
    };
    std::vector<Expectation> sent;
    // The duplicate fault reuses the control ID of a message accepted first.
    const std::string duplicated_id = "FDUP";
    client.send_message(oru_with_control_id(duplicated_id));
    const auto original = client.read_ack();
    ASSERT_TRUE(original.has_value());
    ASSERT_EQ(ack_code(*original), "AA");

    int index = 0;
    for (const auto& fault : catalog.at("faults")) {
        const auto name = fault.at("name").get<std::string>();
        const std::string control_id =
            name == "duplicate_control_id" ? duplicated_id : "F" + std::to_string(index++);
        const std::string base = oru_with_control_id(control_id);
        std::string faulty;
        if (name == "duplicate_control_id") {
            faulty = replace_first(base, "LN||112|", "LN||113|");
        } else if (name == "oversized_frame") {
            faulty = base + "NTE|1||" + std::string(4096, 'x') + "\r";
        } else {
            faulty = transforms.at(name)(base);
        }
        client.send_message(faulty);
        sent.push_back({name, control_id, fault});
    }

    for (const auto& expectation : sent) {
        SCOPED_TRACE(expectation.name);
        const auto ack = client.read_ack();
        ASSERT_TRUE(ack.has_value());
        EXPECT_EQ(ack_code(*ack), expectation.fault.at("ack_code").get<std::string>()) << *ack;
        // A message whose MSH-2 is wrong or that is cut short may not yield
        // its control ID, but these faults leave MSH-10 readable.
        EXPECT_EQ(acked_control_id(*ack), expectation.control_id);
    }
    EXPECT_EQ(ingest.terminate(), 0);

    std::map<std::string, json> dead_by_control_id;
    for (const auto& line : deadletter()) {
        dead_by_control_id[line.at("value").at("control_id").get<std::string>()] = line.at("value");
    }
    std::map<std::string, json> valid_by_control_id;
    for (const auto& line : validated()) {
        valid_by_control_id[line.at("value").at("control_id").get<std::string>()] =
            line.at("value");
    }
    for (const auto& expectation : sent) {
        SCOPED_TRACE(expectation.name);
        const auto code = expectation.fault.at("code").get<std::string>();
        if (expectation.fault.at("outcome") == "error") {
            ASSERT_TRUE(dead_by_control_id.contains(expectation.control_id));
            const auto& record = dead_by_control_id.at(expectation.control_id);
            EXPECT_EQ(record.at("error_code"), code);
            EXPECT_EQ(record.at("stage"), expectation.fault.at("stage"));
        } else {
            ASSERT_TRUE(valid_by_control_id.contains(expectation.control_id));
            bool found = false;
            for (const auto& warning :
                 valid_by_control_id.at(expectation.control_id).at("warnings")) {
                found = found || warning.at("code") == code;
            }
            EXPECT_TRUE(found);
        }
    }
}

TEST_F(IngestBinary, TwoHundredConcurrentConnections) {
    IngestProcess ingest(args());
    constexpr int kConnections = 200;
    constexpr int kMessagesEach = 20;
    std::atomic<int> acked{0};
    std::atomic<int> out_of_order{0};
    std::vector<std::thread> clients;
    clients.reserve(kConnections);
    for (int c = 0; c < kConnections; ++c) {
        clients.emplace_back([&, c] {
            MllpClient client(ingest.port());
            for (int m = 0; m < kMessagesEach; ++m) {
                client.send_message(
                    oru_with_control_id("K" + std::to_string(c) + "-" + std::to_string(m)));
            }
            for (int m = 0; m < kMessagesEach; ++m) {
                // 4,000 messages queue behind one validation thread, and under
                // the sanitizers the last client can wait tens of seconds.
                const auto ack = client.read_ack(std::chrono::seconds(60));
                if (!ack || ack_code(*ack) != "AA") {
                    return;
                }
                if (acked_control_id(*ack) != "K" + std::to_string(c) + "-" + std::to_string(m)) {
                    ++out_of_order;
                }
                ++acked;
            }
        });
    }
    for (auto& client : clients) {
        client.join();
    }
    EXPECT_EQ(acked.load(), kConnections * kMessagesEach);
    EXPECT_EQ(out_of_order.load(), 0);
    const std::string page = testing::http_get(ingest.metrics_port(), "/metrics");
    EXPECT_EQ(testing::metric_value(page, "wardwatch_ingest_connections_accepted_total"),
              kConnections);
    EXPECT_EQ(ingest.terminate(), 0);
    EXPECT_EQ(validated().size(), static_cast<std::size_t>(kConnections * kMessagesEach));
}

// The validated file is a named pipe that this test drains slowly, so the
// sink blocks, the validation thread stalls, the ring fills and the I/O
// thread must stop reading. Nothing may be lost.
TEST_F(IngestBinary, SlowSinkCausesBackpressureWithoutLoss) {
    const auto pipe_path = directory_ / "hl7.validated.jsonl";
    ASSERT_EQ(::mkfifo(pipe_path.c_str(), 0600), 0);
    std::vector<std::string> received_lines;
    std::thread slow_reader([&] {
        std::ifstream input(pipe_path);
        for (std::string line; std::getline(input, line);) {
            received_lines.push_back(line);
            std::this_thread::sleep_for(std::chrono::microseconds(300));
        }
    });

    constexpr int kMessages = 400;
    {
        IngestProcess ingest(args({"--ring-capacity", "4"}));
        MllpClient client(ingest.port());
        std::thread sender([&] {
            for (int i = 0; i < kMessages; ++i) {
                client.send_message(oru_with_control_id("S" + std::to_string(i)));
            }
        });
        std::set<std::string> acked;
        for (int i = 0; i < kMessages; ++i) {
            const auto ack = client.read_ack(std::chrono::seconds(30));
            ASSERT_TRUE(ack.has_value()) << i;
            EXPECT_EQ(ack_code(*ack), "AA");
            acked.insert(acked_control_id(*ack));
        }
        sender.join();
        const std::string page = testing::http_get(ingest.metrics_port(), "/metrics");
        EXPECT_GT(testing::metric_value(page, "wardwatch_ingest_backpressure_events_total"), 0);
        EXPECT_EQ(acked.size(), static_cast<std::size_t>(kMessages));
        EXPECT_EQ(ingest.terminate(), 0);
    }
    slow_reader.join();
    EXPECT_EQ(received_lines.size(), static_cast<std::size_t>(kMessages));
}

TEST_F(IngestBinary, SigtermMidStreamKeepsEveryAaMessage) {
    IngestProcess ingest(args());
    MllpClient client(ingest.port());
    std::atomic<bool> stop_sending{false};
    std::thread sender([&] {
        for (int i = 0; i < 20000 && !stop_sending.load(); ++i) {
            try {
                client.send_message(oru_with_control_id("T" + std::to_string(i)));
            } catch (const std::runtime_error&) {
                return;
            }
        }
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    std::thread terminator([&] { EXPECT_EQ(ingest.terminate(), 0); });

    std::set<std::string> acked;
    while (const auto ack = client.read_ack(std::chrono::seconds(3))) {
        if (ack_code(*ack) == "AA") {
            acked.insert(acked_control_id(*ack));
        }
    }
    stop_sending.store(true);
    terminator.join();
    sender.join();

    std::set<std::string> written;
    for (const auto& line : validated()) {
        written.insert(line.at("value").at("control_id").get<std::string>());
    }
    EXPECT_GT(acked.size(), 0U);
    for (const auto& control_id : acked) {
        EXPECT_TRUE(written.contains(control_id)) << control_id;
    }
}

TEST_F(IngestBinary, RejectsBadArguments) {
    EXPECT_THROW(IngestProcess({"--sink", "nowhere"}), std::runtime_error);
}

}  // namespace
}  // namespace wardwatch
