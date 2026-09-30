#include "wardwatch/encoding.hpp"

#include <string>
#include <string_view>

#include <gtest/gtest.h>

namespace wardwatch {
namespace {

using namespace std::string_view_literals;

EncodingCharacters standard() { return EncodingCharacters{}; }

TEST(EncodingCharacters, ReadsStandardDelimiters) {
    const auto encoding = read_encoding_characters("MSH|^~\\&|APP|");
    ASSERT_TRUE(encoding.has_value());
    EXPECT_EQ(encoding->field, '|');
    EXPECT_EQ(encoding->component, '^');
    EXPECT_EQ(encoding->repetition, '~');
    EXPECT_EQ(encoding->escape, '\\');
    EXPECT_EQ(encoding->subcomponent, '&');
    EXPECT_FALSE(encoding->truncation.has_value());
}

TEST(EncodingCharacters, ReadsCustomDelimitersAndTruncation) {
    const auto encoding = read_encoding_characters("MSH#$*!%@#APP");
    ASSERT_TRUE(encoding.has_value());
    EXPECT_EQ(encoding->field, '#');
    EXPECT_EQ(encoding->component, '$');
    EXPECT_EQ(encoding->repetition, '*');
    EXPECT_EQ(encoding->escape, '!');
    EXPECT_EQ(encoding->subcomponent, '%');
    EXPECT_EQ(encoding->truncation, '@');
}

TEST(EncodingCharacters, AcceptsMshWithNothingAfterMsh2) {
    EXPECT_TRUE(read_encoding_characters("MSH|^~\\&").has_value());
    EXPECT_TRUE(read_encoding_characters("MSH|^~\\&\r").has_value());
}

struct InvalidEncodingCase {
    std::string_view name;
    std::string_view message;
    ErrorCode code;
    std::size_t offset;
};

class InvalidEncoding : public ::testing::TestWithParam<InvalidEncodingCase> {};

TEST_P(InvalidEncoding, IsRejected) {
    const auto& param = GetParam();
    const auto encoding = read_encoding_characters(param.message);
    ASSERT_FALSE(encoding.has_value());
    EXPECT_EQ(encoding.error().code, param.code);
    EXPECT_EQ(encoding.error().offset, param.offset);
    EXPECT_FALSE(encoding.error().detail.empty());
}

INSTANTIATE_TEST_SUITE_P(
    Table, InvalidEncoding,
    ::testing::Values(
        InvalidEncodingCase{"not_msh", "PID|1", ErrorCode::msh_missing, 0},
        InvalidEncodingCase{"empty", "", ErrorCode::msh_missing, 0},
        InvalidEncodingCase{"lowercase", "msh|^~\\&|", ErrorCode::msh_missing, 0},
        InvalidEncodingCase{"only_msh", "MSH", ErrorCode::encoding_characters_invalid, 3},
        InvalidEncodingCase{"too_short", "MSH|^~\\|", ErrorCode::encoding_characters_invalid, 4},
        InvalidEncodingCase{"too_long", "MSH|^~\\&#!|", ErrorCode::encoding_characters_invalid, 4},
        InvalidEncodingCase{"repeated_component", "MSH|^^\\&|",
                            ErrorCode::encoding_characters_invalid, 5},
        InvalidEncodingCase{"field_reused", "MSH|^~|&|", ErrorCode::encoding_characters_invalid, 4},
        InvalidEncodingCase{"alphanumeric", "MSH|^~\\A|", ErrorCode::encoding_characters_invalid,
                            7},
        InvalidEncodingCase{"space", "MSH ^~\\&|", ErrorCode::encoding_characters_invalid, 3},
        InvalidEncodingCase{"cut_by_cr", "MSH|^~\r", ErrorCode::encoding_characters_invalid, 4}),
    [](const auto& info) { return std::string(info.param.name); });

struct EscapeCase {
    std::string_view name;
    std::string_view encoded;
    std::string_view decoded;
};

class EscapeDecoding : public ::testing::TestWithParam<EscapeCase> {};

TEST_P(EscapeDecoding, DecodesToText) {
    const auto& param = GetParam();
    const auto decoded = decode_escapes(param.encoded, standard());
    ASSERT_TRUE(decoded.has_value()) << decoded.error().detail;
    EXPECT_EQ(*decoded, param.decoded);
}

INSTANTIATE_TEST_SUITE_P(
    Table, EscapeDecoding,
    ::testing::Values(
        EscapeCase{"plain", "Heart rate", "Heart rate"}, EscapeCase{"empty", "", ""},
        EscapeCase{"field", "a\\F\\b", "a|b"}, EscapeCase{"component", "a\\S\\b", "a^b"},
        EscapeCase{"subcomponent", "a\\T\\b", "a&b"}, EscapeCase{"repetition", "a\\R\\b", "a~b"},
        EscapeCase{"escape", "a\\E\\b", "a\\b"}, EscapeCase{"hex_one_byte", "\\X41\\", "A"},
        EscapeCase{"hex_lowercase", "\\X0d0a\\", "\r\n"},
        EscapeCase{"hex_utf8", "Z\\XC3A9\\", "Z\xC3\xA9"},
        EscapeCase{"highlight_dropped", "\\H\\bold\\N\\", "bold"},
        EscapeCase{"adjacent", "\\F\\\\S\\", "|^"},
        EscapeCase{"at_edges", "\\E\\middle\\E\\", "\\middle\\"}),
    [](const auto& info) { return std::string(info.param.name); });

struct MalformedEscapeCase {
    std::string_view name;
    std::string_view encoded;
    std::size_t offset;
};

class MalformedEscape : public ::testing::TestWithParam<MalformedEscapeCase> {};

TEST_P(MalformedEscape, IsRejectedWithOffset) {
    const auto& param = GetParam();
    const auto decoded = decode_escapes(param.encoded, standard(), 100);
    ASSERT_FALSE(decoded.has_value());
    EXPECT_EQ(decoded.error().code, ErrorCode::escape_sequence_invalid);
    EXPECT_EQ(decoded.error().offset, 100 + param.offset);
}

INSTANTIATE_TEST_SUITE_P(Table, MalformedEscape,
                         ::testing::Values(MalformedEscapeCase{"unterminated", "ab\\F", 2},
                                           MalformedEscapeCase{"lone_escape", "\\", 0},
                                           MalformedEscapeCase{"empty_sequence", "x\\\\", 1},
                                           MalformedEscapeCase{"unknown_letter", "\\Q\\", 0},
                                           MalformedEscapeCase{"unknown_word", "\\.br\\", 0},
                                           MalformedEscapeCase{"hex_empty", "\\X\\", 0},
                                           MalformedEscapeCase{"hex_odd", "\\X414\\", 0},
                                           MalformedEscapeCase{"hex_not_hex", "ok\\XG1\\", 2},
                                           MalformedEscapeCase{"truncation_undeclared", "\\P\\",
                                                               0}),
                         [](const auto& info) { return std::string(info.param.name); });

TEST(EscapeDecoding, UsesCustomDelimiters) {
    const auto encoding = read_encoding_characters("MSH#$*!%@#");
    ASSERT_TRUE(encoding.has_value());
    const auto decoded = decode_escapes("a!F!b!S!c!T!d!R!e!E!f!P!g\\h", *encoding);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(*decoded, "a#b$c%d*e!f@g\\h");
}

TEST(EscapeDetection, FindsOnlyTheDeclaredEscapeCharacter) {
    EXPECT_FALSE(has_escapes("plain text", standard()));
    EXPECT_TRUE(has_escapes("a\\F\\b", standard()));
    EncodingCharacters custom;
    custom.escape = '!';
    EXPECT_FALSE(has_escapes("a\\b", custom));
}

TEST(EscapeEncoding, EscapesEveryDelimiterAndLineBreak) {
    EXPECT_EQ(encode_escapes("a|b^c&d~e\\f\rg\nh", standard()),
              "a\\F\\b\\S\\c\\T\\d\\R\\e\\E\\f\\X0D\\g\\X0A\\h");
}

TEST(EscapeEncoding, RoundTripsThroughDecoding) {
    EncodingCharacters with_truncation;
    with_truncation.truncation = '#';
    for (const std::string_view text : {"plain"sv, "|^~\\&#"sv, "\r\n"sv, ""sv, "\\E\\"sv}) {
        const auto decoded = decode_escapes(encode_escapes(text, with_truncation), with_truncation);
        ASSERT_TRUE(decoded.has_value());
        EXPECT_EQ(*decoded, text);
    }
}

TEST(EncodingCharacters, IsDelimiter) {
    EncodingCharacters encoding;
    for (const char c : "|^~\\&"sv) {
        EXPECT_TRUE(encoding.is_delimiter(c));
    }
    EXPECT_FALSE(encoding.is_delimiter('#'));
    encoding.truncation = '#';
    EXPECT_TRUE(encoding.is_delimiter('#'));
    EXPECT_FALSE(encoding.is_delimiter('a'));
}

}  // namespace
}  // namespace wardwatch
