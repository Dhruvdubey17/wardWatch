#include "wardwatch/ack.hpp"

#include <chrono>
#include <optional>
#include <string>

#include <gtest/gtest.h>

#include "wardwatch/message.hpp"

namespace wardwatch {
namespace {

using std::chrono::sys_days;
using namespace std::chrono_literals;

std::chrono::system_clock::time_point at_noon() {
    return sys_days{std::chrono::year{2024} / 3 / 15} + 12h + 34min + 56s + 789ms;
}

MessageSummary original() {
    MessageSummary summary;
    summary.sending_application = "WWSIM";
    summary.sending_facility = "WARDWATCH_ICU";
    summary.receiving_application = "WARDWATCH";
    summary.receiving_facility = "WARDWATCH";
    summary.control_id = "SIM000007";
    summary.message_type = "ORU";
    summary.trigger_event = "R01";
    summary.processing_id = "P";
    summary.version = "2.5.1";
    return summary;
}

TEST(Ack, AcceptedMessage) {
    EXPECT_EQ(build_ack(original(), AckCode::aa, std::nullopt, "ACK000001", at_noon()),
              "MSH|^~\\&|WARDWATCH|WARDWATCH|WWSIM|WARDWATCH_ICU|20240315123456+0000||"
              "ACK^R01^ACK|ACK000001|P|2.5.1\r"
              "MSA|AA|SIM000007\r");
}

TEST(Ack, ApplicationErrorCarriesErr) {
    const ParseError error{ErrorCode::value_type_mismatch, 412, "OBX[1]-5: 'high' is not numeric"};
    EXPECT_EQ(build_ack(original(), AckCode::ae, error, "ACK000002", at_noon()),
              "MSH|^~\\&|WARDWATCH|WARDWATCH|WWSIM|WARDWATCH_ICU|20240315123456+0000||"
              "ACK^R01^ACK|ACK000002|P|2.5.1\r"
              "MSA|AE|SIM000007\r"
              "ERR|||102^Data type error^HL70357|E|VALUE_TYPE_MISMATCH^^WWERR|||"
              "OBX[1]-5: 'high' is not numeric\r");
}

TEST(Ack, RejectForUnreadableMessageUsesDefaults) {
    MessageSummary sniffed;
    sniffed.control_id = "X1";
    const ParseError error{ErrorCode::encoding_characters_invalid, 5,
                           "delimiters in MSH-1 and MSH-2 are not distinct"};
    EXPECT_EQ(build_ack(sniffed, AckCode::ar, error, "ACK000003", at_noon()),
              "MSH|^~\\&|WARDWATCH_INGEST|WARDWATCH|||20240315123456+0000||ACK^^ACK|ACK000003|P|"
              "2.5.1\r"
              "MSA|AR|X1\r"
              "ERR|||102^Data type error^HL70357|E|ENCODING_CHARACTERS_INVALID^^WWERR|||"
              "delimiters in MSH-1 and MSH-2 are not distinct\r");
}

TEST(Ack, EscapesDelimitersInDetailAndIds) {
    MessageSummary summary = original();
    summary.control_id = "A|B";
    const ParseError error{ErrorCode::timestamp_invalid, 0, "value '2024^01' & more"};
    const std::string ack = build_ack(summary, AckCode::ae, error, "ACK4", at_noon());
    EXPECT_NE(ack.find("MSA|AE|A\\F\\B\r"), std::string::npos);
    EXPECT_NE(ack.find("|||value '2024\\S\\01' \\T\\ more\r"), std::string::npos);
}

TEST(Ack, ParsesAsHl7) {
    const ParseError error{ErrorCode::control_id_duplicate, 0, "reused"};
    const std::string ack = build_ack(original(), AckCode::ae, error, "ACK5", at_noon());
    const auto message = Message::parse(ack);
    ASSERT_TRUE(message.has_value());
    EXPECT_EQ(message->find("MSA")->field(1), "AE");
    EXPECT_EQ(message->find("MSA")->field(2), "SIM000007");
    EXPECT_EQ(message->find("ERR")->field(3), "205^Duplicate key identifier^HL70357");
    EXPECT_EQ(message->find("ERR")->field(4), "E");
    EXPECT_EQ(message->find("MSH")->field(9), "ACK^R01^ACK");
}

TEST(Ack, EveryErrorCodeHasACondition) {
    for (std::size_t i = 0; i < kErrorCodeCount; ++i) {
        const auto condition = hl7_error_condition(static_cast<ErrorCode>(i));
        EXPECT_EQ(condition.code.size(), 3U);
        EXPECT_FALSE(condition.text.empty());
    }
}

TEST(Ack, TimestampTruncatesToSeconds) {
    EXPECT_EQ(format_hl7_timestamp(at_noon()), "20240315123456+0000");
    EXPECT_EQ(format_hl7_timestamp(sys_days{std::chrono::year{1999} / 12 / 31} + 23h + 59min + 59s),
              "19991231235959+0000");
}

}  // namespace
}  // namespace wardwatch
