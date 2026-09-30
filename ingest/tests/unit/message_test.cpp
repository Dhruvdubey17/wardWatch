#include "wardwatch/message.hpp"

#include <fstream>
#include <iterator>
#include <string>
#include <string_view>

#include <gtest/gtest.h>

namespace wardwatch {
namespace {

using namespace std::string_view_literals;

constexpr std::string_view kMsh =
    "MSH|^~\\&|WWSIM|WARDWATCH_ICU|WARDWATCH|WARDWATCH|20240315130005+0000||ORU^R01^ORU_R01|"
    "SIM000007|P|2.5.1";

std::string read_contract(std::string_view name) {
    std::ifstream input(std::string(WARDWATCH_CONTRACTS_DIR) + "/hl7/" + std::string(name),
                        std::ios::binary);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

Message parse_ok(std::string_view text) {
    auto message = Message::parse(text);
    EXPECT_TRUE(message.has_value()) << (message ? "" : message.error().detail);
    return std::move(*message);
}

bool has_warning(const Message& message, WarningCode code) {
    for (const auto& warning : message.warnings()) {
        if (warning.code == code) {
            return true;
        }
    }
    return false;
}

TEST(Message, ParsesContractOruSample) {
    const auto message = parse_ok(read_contract("oru_r01.hl7"));
    ASSERT_EQ(message.segments().size(), 17U);
    EXPECT_EQ(message.count("OBX"), 13U);
    EXPECT_TRUE(message.warnings().empty());

    const auto* msh = message.find("MSH");
    ASSERT_NE(msh, nullptr);
    EXPECT_EQ(msh->field(1), "|");
    EXPECT_EQ(msh->field(2), "^~\\&");
    EXPECT_EQ(msh->field(3), "WWSIM");
    EXPECT_EQ(msh->field(7), "20240315130005+0000");
    EXPECT_EQ(msh->field(9), "ORU^R01^ORU_R01");
    EXPECT_EQ(msh->field(10), "SIM000007");
    EXPECT_EQ(msh->field(12), "2.5.1");
    EXPECT_EQ(msh->field(13), "");

    const auto obx = message.find_all("OBX");
    ASSERT_EQ(obx.size(), 13U);
    EXPECT_EQ(obx[0]->occurrence, 1U);
    EXPECT_EQ(obx[12]->occurrence, 13U);
    EXPECT_EQ(obx[0]->field(5), "112");
    EXPECT_EQ(obx[0]->field(11), "F");
    EXPECT_EQ(obx[0]->field(14), "20240315130000+0000");
    EXPECT_EQ(subcomponent(obx[0]->field(3), message.encoding(), 1, 1), "8867-4");
    EXPECT_EQ(subcomponent(obx[0]->field(3), message.encoding(), 1, 3), "LN");
}

TEST(Message, NonMshSegmentsNumberFieldsAfterTheId) {
    const auto message = parse_ok(std::string(kMsh) + "\rPID|1||MRN1^^^WW^MR||Doe^Jane\r");
    const auto* pid = message.find("PID");
    ASSERT_NE(pid, nullptr);
    EXPECT_EQ(pid->field(0), "");
    EXPECT_EQ(pid->field(1), "1");
    EXPECT_EQ(pid->field(2), "");
    EXPECT_EQ(pid->field(3), "MRN1^^^WW^MR");
    EXPECT_EQ(pid->field(5), "Doe^Jane");
    EXPECT_EQ(pid->field(6), "");
    EXPECT_EQ(pid->fields.size(), 5U);
    EXPECT_EQ(pid->offset, kMsh.size() + 1);
    EXPECT_EQ(message.offset_of(pid->field(3)), kMsh.size() + 1 + 7);
}

TEST(Message, SegmentWithOnlyAnIdHasNoFields) {
    const auto message = parse_ok(std::string(kMsh) + "\rEVN\r");
    const auto* evn = message.find("EVN");
    ASSERT_NE(evn, nullptr);
    EXPECT_TRUE(evn->fields.empty());
    EXPECT_EQ(evn->field(1), "");
}

TEST(Message, EmptyFieldsArePreserved) {
    const auto message = parse_ok(std::string(kMsh) + "\rNTE||||x\r");
    const auto* nte = message.find("NTE");
    ASSERT_NE(nte, nullptr);
    ASSERT_EQ(nte->fields.size(), 4U);
    EXPECT_EQ(nte->field(1), "");
    EXPECT_EQ(nte->field(3), "");
    EXPECT_EQ(nte->field(4), "x");
}

TEST(Message, CrTerminatorsProduceNoWarning) {
    const auto message = parse_ok(std::string(kMsh) + "\rPID|1\r");
    EXPECT_FALSE(has_warning(message, WarningCode::segment_terminator_not_cr));
}

TEST(Message, MissingFinalTerminatorIsAccepted) {
    const auto message = parse_ok(std::string(kMsh) + "\rPID|1");
    EXPECT_EQ(message.segments().size(), 2U);
    EXPECT_TRUE(message.warnings().empty());
}

TEST(Message, LfTerminatorsAreAcceptedWithWarning) {
    const auto message = parse_ok(std::string(kMsh) + "\nPID|1\nPV1|1\n");
    EXPECT_EQ(message.segments().size(), 3U);
    ASSERT_EQ(message.warnings().size(), 1U);
    EXPECT_EQ(message.warnings()[0].code, WarningCode::segment_terminator_not_cr);
    EXPECT_EQ(message.warnings()[0].detail, "segments end with LF");
}

TEST(Message, CrlfTerminatorsAreAcceptedWithWarning) {
    const auto message = parse_ok(std::string(kMsh) + "\r\nPID|1\r\nPV1|1\r\n");
    ASSERT_EQ(message.segments().size(), 3U);
    EXPECT_EQ(message.segments()[1].id, "PID");
    EXPECT_EQ(message.segments()[1].field(1), "1");
    ASSERT_EQ(message.warnings().size(), 1U);
    EXPECT_EQ(message.warnings()[0].detail, "segments end with CRLF");
}

TEST(Message, BlankLinesBetweenSegmentsAreSkipped) {
    const auto message = parse_ok(std::string(kMsh) + "\r\rPID|1\n\n");
    EXPECT_EQ(message.segments().size(), 2U);
}

TEST(Message, TrailingSeparatorIsAWarningWithLocation) {
    const auto message = parse_ok(std::string(kMsh) + "\rPID|1|||\rOBX|1|NM||\r");
    ASSERT_TRUE(has_warning(message, WarningCode::trailing_separator));
    const auto& warning = message.warnings()[0];
    EXPECT_EQ(warning.location, "PID[1]");
    const auto* pid = message.find("PID");
    EXPECT_EQ(pid->fields.size(), 4U);
    EXPECT_EQ(message.warnings().size(), 1U);
}

TEST(Message, NonAsciiBytesWarnWithoutCharset) {
    const auto message = parse_ok(std::string(kMsh) + "\rPID|1||MRN1||M\xC3\xBCller^Jo\r");
    ASSERT_TRUE(has_warning(message, WarningCode::non_ascii_bytes));
}

TEST(Message, NonAsciiBytesAllowedWhenCharsetDeclared) {
    const std::string msh = std::string(kMsh) + "||||||UNICODE UTF-8";
    const auto message = parse_ok(msh + "\rPID|1||MRN1||M\xC3\xBCller^Jo\r");
    EXPECT_EQ(message.find("MSH")->field(18), "UNICODE UTF-8");
    EXPECT_FALSE(has_warning(message, WarningCode::non_ascii_bytes));
}

TEST(Message, CustomDelimitersSplitFields) {
    const auto message = parse_ok("MSH#$*!%#APP#FAC\rPID#1##MRN1$$$WW$MR*MRN2$$$WW$PI\r");
    const auto* pid = message.find("PID");
    ASSERT_NE(pid, nullptr);
    EXPECT_EQ(pid->field(3), "MRN1$$$WW$MR*MRN2$$$WW$PI");
    EXPECT_EQ(subcomponent(pid->field(3), message.encoding(), 2, 1), "MRN2");
    EXPECT_EQ(subcomponent(pid->field(3), message.encoding(), 2, 5), "PI");
    EXPECT_EQ(message.find("MSH")->field(2), "$*!%");
}

TEST(Message, VeryLongFieldIsKeptWhole) {
    const std::string long_value(1'000'000, 'x');
    const auto message = parse_ok(std::string(kMsh) + "\rNTE|1||" + long_value + "\r");
    EXPECT_EQ(message.find("NTE")->field(3).size(), long_value.size());
}

TEST(Message, MoveKeepsViewsValid) {
    auto first = parse_ok(std::string(kMsh) + "\rPID|1\r");
    const std::string_view before = first.find("PID")->field(1);
    Message moved = std::move(first);
    EXPECT_EQ(moved.find("PID")->field(1), "1");
    EXPECT_EQ(moved.find("PID")->field(1).data(), before.data());
}

TEST(Message, FindReturnsNullForAbsentSegment) {
    const auto message = parse_ok(std::string(kMsh) + "\r");
    EXPECT_EQ(message.find("PID"), nullptr);
    EXPECT_TRUE(message.find_all("OBX").empty());
    EXPECT_EQ(message.count("OBX"), 0U);
}

struct InvalidMessageCase {
    std::string_view name;
    std::string_view text;
    ErrorCode code;
    std::size_t offset;
};

class InvalidMessage : public ::testing::TestWithParam<InvalidMessageCase> {};

TEST_P(InvalidMessage, IsRejected) {
    const auto& param = GetParam();
    const auto message = Message::parse(param.text);
    ASSERT_FALSE(message.has_value());
    EXPECT_EQ(message.error().code, param.code);
    EXPECT_EQ(message.error().offset, param.offset);
}

INSTANTIATE_TEST_SUITE_P(
    Table, InvalidMessage,
    ::testing::Values(InvalidMessageCase{"empty", "", ErrorCode::msh_missing, 0},
                      InvalidMessageCase{"starts_with_pid", "PID|1\r", ErrorCode::msh_missing, 0},
                      InvalidMessageCase{"leading_blank_line", "\rMSH|^~\\&|A\r",
                                         ErrorCode::msh_missing, 0},
                      InvalidMessageCase{"lowercase_segment", "MSH|^~\\&|A\rpid|1\r",
                                         ErrorCode::segment_id_invalid, 11},
                      InvalidMessageCase{"short_segment_id", "MSH|^~\\&|A\rPI|1\r",
                                         ErrorCode::segment_id_invalid, 11},
                      InvalidMessageCase{"long_segment_id", "MSH|^~\\&|A\rPIDX|1\r",
                                         ErrorCode::segment_id_invalid, 11},
                      InvalidMessageCase{"digit_first", "MSH|^~\\&|A\r1ID|1\r",
                                         ErrorCode::segment_id_invalid, 11},
                      InvalidMessageCase{"bad_encoding", "MSH|^^\\&|A\r",
                                         ErrorCode::encoding_characters_invalid, 5}),
    [](const auto& info) { return std::string(info.param.name); });

TEST(Pieces, NthPieceAndCount) {
    EXPECT_EQ(nth_piece("a^b^c", '^', 1), "a");
    EXPECT_EQ(nth_piece("a^b^c", '^', 3), "c");
    EXPECT_EQ(nth_piece("a^b^c", '^', 4), "");
    EXPECT_EQ(nth_piece("a^b^c", '^', 0), "");
    EXPECT_EQ(nth_piece("^^x", '^', 3), "x");
    EXPECT_EQ(nth_piece("", '^', 1), "");
    EXPECT_EQ(piece_count("", '^'), 0U);
    EXPECT_EQ(piece_count("a", '^'), 1U);
    EXPECT_EQ(piece_count("a^^", '^'), 3U);
}

TEST(Pieces, SubcomponentAddressing) {
    const EncodingCharacters encoding;
    const std::string_view field = "MRN1^^^WW&1.2.3&ISO^MR~MRN2^^^OTHER^PI";
    EXPECT_EQ(subcomponent(field, encoding, 1, 4, 1), "WW");
    EXPECT_EQ(subcomponent(field, encoding, 1, 4, 2), "1.2.3");
    EXPECT_EQ(subcomponent(field, encoding, 1, 4, 3), "ISO");
    EXPECT_EQ(subcomponent(field, encoding, 2, 1), "MRN2");
    EXPECT_EQ(subcomponent(field, encoding, 3, 1), "");
}

}  // namespace
}  // namespace wardwatch
