#include "wardwatch/file_sink.hpp"

#include <filesystem>
#include <fstream>
#include <string>

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

namespace wardwatch {
namespace {

class FileSinkTest : public ::testing::Test {
  protected:
    void SetUp() override {
        directory_ = std::filesystem::temp_directory_path() /
                     ("wardwatch-file-sink-" +
                      std::to_string(::testing::UnitTest::GetInstance()->random_seed()) + "-" +
                      ::testing::UnitTest::GetInstance()->current_test_info()->name());
        std::filesystem::remove_all(directory_);
    }
    void TearDown() override { std::filesystem::remove_all(directory_); }

    std::vector<nlohmann::json> lines(std::string_view topic) const {
        std::ifstream input(directory_ / (std::string(topic) + ".jsonl"));
        std::vector<nlohmann::json> out;
        for (std::string line; std::getline(input, line);) {
            out.push_back(nlohmann::json::parse(line));
        }
        return out;
    }

    std::filesystem::path directory_;
};

TEST_F(FileSinkTest, WritesOneLinePerRecordPerTopic) {
    DeliveryCounters counters;
    auto sink = FileSink::open(directory_, counters);
    ASSERT_TRUE(sink.has_value()) << sink.error();
    EXPECT_TRUE((*sink)->publish(Topic::validated, "MRN1", R"({"control_id":"A"})"));
    EXPECT_TRUE((*sink)->publish(Topic::validated, "MRN2", R"({"control_id":"B"})"));
    EXPECT_TRUE((*sink)->publish(Topic::deadletter, "C\"1", R"({"stage":"parse"})"));
    EXPECT_TRUE((*sink)->flush(std::chrono::milliseconds(10)));

    const auto validated = lines("hl7.validated");
    ASSERT_EQ(validated.size(), 2U);
    EXPECT_EQ(validated[0].at("key"), "MRN1");
    EXPECT_EQ(validated[1].at("value").at("control_id"), "B");
    const auto deadletter = lines("hl7.deadletter");
    ASSERT_EQ(deadletter.size(), 1U);
    EXPECT_EQ(deadletter[0].at("key"), "C\"1");
    EXPECT_EQ(counters.delivered.load(), 3U);
    EXPECT_EQ(counters.failed.load(), 0U);
}

TEST_F(FileSinkTest, AppendsAcrossReopen) {
    DeliveryCounters counters;
    {
        auto sink = FileSink::open(directory_, counters);
        ASSERT_TRUE(sink.has_value());
        EXPECT_TRUE((*sink)->publish(Topic::validated, "A", "{}"));
    }
    auto sink = FileSink::open(directory_, counters);
    ASSERT_TRUE(sink.has_value());
    EXPECT_TRUE((*sink)->publish(Topic::validated, "B", "{}"));
    EXPECT_EQ(lines("hl7.validated").size(), 2U);
}

TEST_F(FileSinkTest, ReportsUnusableDirectory) {
    std::filesystem::create_directories(directory_);
    const auto blocker = directory_ / "file";
    std::ofstream(blocker) << "x";
    DeliveryCounters counters;
    const auto sink = FileSink::open(blocker / "nested", counters);
    ASSERT_FALSE(sink.has_value());
    EXPECT_NE(sink.error().find("cannot create"), std::string::npos);
}

}  // namespace
}  // namespace wardwatch
