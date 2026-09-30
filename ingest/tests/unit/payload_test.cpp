#include "wardwatch/payload.hpp"

#include <chrono>
#include <string>
#include <string_view>

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

#include "wardwatch/validator.hpp"

#include "contracts.hpp"

namespace wardwatch {
namespace {

using nlohmann::json;
using testing::read_contract;
using testing::read_contract_json;
using namespace std::chrono_literals;

std::chrono::system_clock::time_point example_time() {
    return std::chrono::sys_days{std::chrono::year{2024} / 3 / 15} + 13h + 0min + 5s + 12345us;
}

json validated_payload(std::string_view text) {
    Validator validator;
    auto processed = validator.process(std::vector<char>(text.begin(), text.end()));
    EXPECT_EQ(processed.result.disposition, Disposition::accepted);
    const auto payload =
        build_validated_payload(*processed.message, processed.result, example_time());
    EXPECT_TRUE(payload.has_value());
    return json::parse(*payload);
}

// A minimal check of the contract's JSON Schema that needs no validator
// library: every required property is present and, where the schema forbids
// extra properties, nothing else is. The Python contract tests run the full
// schema over the same example files.
void expect_matches_schema_shape(const json& payload, std::string_view topic) {
    const auto schema = read_contract_json("schemas/" + std::string(topic) + ".schema.json");
    for (const auto& required : schema.at("required")) {
        EXPECT_TRUE(payload.contains(required.get<std::string>())) << required;
    }
    if (schema.value("additionalProperties", true) == false) {
        for (const auto& [key, value] : payload.items()) {
            EXPECT_TRUE(schema.at("properties").contains(key)) << key;
        }
    }
}

class ValidatedExample : public ::testing::TestWithParam<std::string_view> {};

TEST_P(ValidatedExample, MatchesContractExample) {
    const std::string name(GetParam());
    auto payload = validated_payload(read_contract("hl7/" + name + ".hl7"));
    auto example = read_contract_json("examples/hl7.validated/" + name + ".json");
    expect_matches_schema_shape(payload, "hl7.validated");
    EXPECT_EQ(payload.at("received_at"), "2024-03-15T13:00:05.012345Z");
    payload.erase("received_at");
    example.erase("received_at");
    EXPECT_EQ(payload, example) << payload.dump(2);
}

INSTANTIATE_TEST_SUITE_P(Contracts, ValidatedExample, ::testing::Values("oru_r01", "adt_a01"));

TEST(ValidatedPayload, CarriesWarningsWithLocation) {
    auto text = read_contract("hl7/oru_r01.hl7");
    text.replace(text.find("LN||112|"), 8, "LN||412|");
    const auto payload = validated_payload(text);
    ASSERT_EQ(payload.at("warnings").size(), 1U);
    EXPECT_EQ(payload.at("warnings")[0].at("code"), "VALUE_IMPLAUSIBLE");
    EXPECT_EQ(payload.at("warnings")[0].at("location"), "OBX[1]-5");
}

TEST(ValidatedPayload, WarningWithoutLocationOmitsIt) {
    auto text = read_contract("hl7/adt_a03.hl7");
    for (char& c : text) {
        if (c == '\r') {
            c = '\n';
        }
    }
    const auto payload = validated_payload(text);
    ASSERT_EQ(payload.at("warnings").size(), 1U);
    EXPECT_FALSE(payload.at("warnings")[0].contains("location"));
}

TEST(ValidatedPayload, DecodesEscapesAndSplitsEveryLevel) {
    auto text = read_contract("hl7/adt_a01.hl7");
    text.replace(text.find("12 Harbor Rd"), 12, "12 Harbor \\T\\ Pier~Unit 4&B");
    const auto payload = validated_payload(text);
    const auto& pid = payload.at("segments")[2];
    ASSERT_EQ(pid.at("id"), "PID");
    const auto& address = pid.at("fields")[10];
    ASSERT_EQ(address.size(), 2U);
    EXPECT_EQ(address[0][0][0], "12 Harbor & Pier");
    EXPECT_EQ(address[1][0], json::array({"Unit 4", "B"}));
}

TEST(ValidatedPayload, EscapeErrorInUnreadFieldIsReported) {
    auto text = read_contract("hl7/adt_a01.hl7");
    text.replace(text.find("12 Harbor Rd"), 12, "12 \\Q\\ Rd");
    Validator validator;
    auto processed = validator.process(std::vector<char>(text.begin(), text.end()));
    ASSERT_EQ(processed.result.disposition, Disposition::accepted);
    const auto payload =
        build_validated_payload(*processed.message, processed.result, example_time());
    ASSERT_FALSE(payload.has_value());
    EXPECT_EQ(payload.error().code, ErrorCode::escape_sequence_invalid);
}

TEST(ValidatedPayload, InvalidUtf8BecomesReplacementCharacter) {
    auto text = read_contract("hl7/oru_r01.hl7");
    text.replace(text.find("Lindgren"), 8, "Lindgr\xE9n");
    const auto payload = validated_payload(text);
    EXPECT_EQ(payload.at("segments")[1].at("fields")[4][0][0][0], "Lindgr\xEF\xBF\xBDn");
}

struct TextCase {
    std::string_view name;
    std::string_view family;
    std::string_view expected;
};

class JsonText : public ::testing::TestWithParam<TextCase> {};

// PID-5.1 carries the text; the payload must be valid JSON that decodes back
// to the expected string.
TEST_P(JsonText, IsEscapedAndSanitized) {
    auto text = read_contract("hl7/oru_r01.hl7");
    text.replace(text.find("Lindgren"), 8, GetParam().family);
    Validator validator;
    auto processed = validator.process(std::vector<char>(text.begin(), text.end()));
    ASSERT_TRUE(processed.message.has_value());
    const auto payload =
        build_validated_payload(*processed.message, processed.result, example_time());
    ASSERT_TRUE(payload.has_value());
    ASSERT_TRUE(json::accept(*payload)) << *payload;
    EXPECT_EQ(json::parse(*payload).at("segments")[1].at("fields")[4][0][0][0],
              GetParam().expected);
}

INSTANTIATE_TEST_SUITE_P(
    Table, JsonText,
    ::testing::Values(TextCase{"quote_and_backslash", "O\"Brien\\E\\x", "O\"Brien\\x"},
                      TextCase{"control_characters",
                               "a\tb\x01"
                               "c",
                               "a\tb\x01"
                               "c"},
                      TextCase{"hex_escape_newline", "a\\X0A\\b", "a\nb"},
                      TextCase{"two_byte_utf8", "M\xC3\xBCller", "M\xC3\xBCller"},
                      TextCase{"four_byte_utf8", "x\xF0\x9F\x98\x80y", "x\xF0\x9F\x98\x80y"},
                      TextCase{"lone_latin1", "Lindgr\xE9n", "Lindgr\xEF\xBF\xBDn"},
                      TextCase{"overlong_slash",
                               "a\xC0\xAF"
                               "b",
                               "a\xEF\xBF\xBD\xEF\xBF\xBD"
                               "b"},
                      TextCase{"surrogate",
                               "a\xED\xA0\x80"
                               "b",
                               "a\xEF\xBF\xBD\xEF\xBF\xBD\xEF\xBF\xBD"
                               "b"},
                      TextCase{"above_max",
                               "a\xF4\x90\x80\x80"
                               "b",
                               "a\xEF\xBF\xBD\xEF\xBF\xBD\xEF\xBF\xBD\xEF\xBF\xBD"
                               "b"},
                      TextCase{"truncated_sequence", "ab\xE2\x82", "ab\xEF\xBF\xBD\xEF\xBF\xBD"}),
    [](const auto& info) { return std::string(info.param.name); });

TEST(DeadLetterPayload, MatchesContractExample) {
    const std::string raw =
        "MSH|^~\\&|WWSIM|WARDWATCH_ICU|WARDWATCH|WARDWATCH|20241315130005+0000||ORU^R01^ORU_R01|"
        "SIM000008|P|2.5.1\r";
    Validator validator;
    const auto processed = validator.process(std::vector<char>(raw.begin(), raw.end()));
    ASSERT_TRUE(processed.result.error.has_value());
    auto payload = json::parse(build_deadletter_payload(
        DeadLetterStage::validate, *processed.result.error, processed.result.summary.control_id,
        std::span<const char>(raw.data(), raw.size()), false, example_time()));
    auto example = read_contract_json("examples/hl7.deadletter/invalid_timestamp.json");
    expect_matches_schema_shape(payload, "hl7.deadletter");
    payload.erase("received_at");
    example.erase("received_at");
    EXPECT_EQ(payload, example) << payload.dump(2);
}

TEST(DeadLetterPayload, UnknownControlIdIsNull) {
    const ParseError error{ErrorCode::msh_missing, 0, "message does not start with MSH"};
    const auto payload = json::parse(build_deadletter_payload(
        DeadLetterStage::parse, error, "", std::span<const char>("x", 1), false, example_time()));
    EXPECT_TRUE(payload.at("control_id").is_null());
    EXPECT_EQ(payload.at("stage"), "parse");
    expect_matches_schema_shape(payload, "hl7.deadletter");
}

TEST(DeadLetterPayload, StageNamesMatchContract) {
    const auto schema = read_contract_json("schemas/hl7.deadletter.schema.json");
    const auto& stages = schema.at("properties").at("stage").at("enum");
    for (const auto stage :
         {DeadLetterStage::frame, DeadLetterStage::parse, DeadLetterStage::validate}) {
        EXPECT_NE(std::ranges::find(stages, json(std::string(to_string(stage)))), stages.end());
    }
}

TEST(Base64, KnownVectors) {
    // RFC 4648 section 10 test vectors.
    const auto encode = [](std::string_view text) {
        return base64_encode(std::span<const char>(text.data(), text.size()));
    };
    EXPECT_EQ(encode(""), "");
    EXPECT_EQ(encode("f"), "Zg==");
    EXPECT_EQ(encode("fo"), "Zm8=");
    EXPECT_EQ(encode("foo"), "Zm9v");
    EXPECT_EQ(encode("foob"), "Zm9vYg==");
    EXPECT_EQ(encode("fooba"), "Zm9vYmE=");
    EXPECT_EQ(encode("foobar"), "Zm9vYmFy");
    EXPECT_EQ(encode("\xff\xfe"), "//4=");
}

TEST(ReceivedAt, UsesMicrosecondsAndZ) {
    EXPECT_EQ(format_received_at(example_time()), "2024-03-15T13:00:05.012345Z");
    EXPECT_EQ(format_received_at(std::chrono::sys_days{std::chrono::year{2024} / 1 / 1}),
              "2024-01-01T00:00:00.000000Z");
}

}  // namespace
}  // namespace wardwatch
