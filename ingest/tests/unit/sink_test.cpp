#include "wardwatch/sink.hpp"

#include <chrono>

#include <gtest/gtest.h>

namespace wardwatch {
namespace {

TEST(NullSink, CountsAndAcceptsEverything) {
    NullSink sink;
    EXPECT_TRUE(sink.publish(Topic::validated, "k", "{}"));
    EXPECT_TRUE(sink.publish(Topic::deadletter, "k", "{}"));
    EXPECT_TRUE(sink.flush(std::chrono::milliseconds(0)));
    EXPECT_EQ(sink.published(), 2U);
}

TEST(Topic, NamesMatchContracts) {
    EXPECT_EQ(topic_name(Topic::validated), "hl7.validated");
    EXPECT_EQ(topic_name(Topic::deadletter), "hl7.deadletter");
}

}  // namespace
}  // namespace wardwatch
