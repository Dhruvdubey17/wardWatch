#include "wardwatch/types.hpp"

#include <cmath>
#include <optional>
#include <string>
#include <string_view>

#include <gtest/gtest.h>

namespace wardwatch {
namespace {

// 1 followed by 400 zeros overflows a double.
const std::string kHugeNumber = "1" + std::string(400, '0');

struct DtmCase {
    std::string_view name;
    std::string_view text;
    DtmPrecision precision;
    std::string_view iso;
    std::optional<int> offset_minutes;
};

class ValidDtm : public ::testing::TestWithParam<DtmCase> {};

TEST_P(ValidDtm, ParsesAtEveryPrecision) {
    const auto& param = GetParam();
    const auto value = parse_dtm(param.text);
    ASSERT_TRUE(value.has_value()) << value.error().detail;
    EXPECT_EQ(value->precision, param.precision);
    EXPECT_EQ(value->to_iso8601(), param.iso);
    EXPECT_EQ(value->utc_offset_minutes, param.offset_minutes);
}

INSTANTIATE_TEST_SUITE_P(
    Table, ValidDtm,
    ::testing::Values(
        DtmCase{"year", "2024", DtmPrecision::year, "2024", std::nullopt},
        DtmCase{"month", "202403", DtmPrecision::month, "2024-03", std::nullopt},
        DtmCase{"day", "20240315", DtmPrecision::day, "2024-03-15", std::nullopt},
        DtmCase{"hour", "2024031513", DtmPrecision::hour, "2024-03-15T13", std::nullopt},
        DtmCase{"minute", "202403151307", DtmPrecision::minute, "2024-03-15T13:07", std::nullopt},
        DtmCase{"second", "20240315130705", DtmPrecision::second, "2024-03-15T13:07:05",
                std::nullopt},
        DtmCase{"fraction_1", "20240315130705.1", DtmPrecision::fraction, "2024-03-15T13:07:05.1",
                std::nullopt},
        DtmCase{"fraction_4", "20240315130705.0042", DtmPrecision::fraction,
                "2024-03-15T13:07:05.0042", std::nullopt},
        DtmCase{"utc", "20240315130705+0000", DtmPrecision::second, "2024-03-15T13:07:05+00:00", 0},
        DtmCase{"west", "20240315130705-0500", DtmPrecision::second, "2024-03-15T13:07:05-05:00",
                -300},
        DtmCase{"east_half_hour", "20240315130705+0530", DtmPrecision::second,
                "2024-03-15T13:07:05+05:30", 330},
        DtmCase{"offset_on_day", "20240315-1200", DtmPrecision::day, "2024-03-15-12:00", -720},
        DtmCase{"fraction_and_offset", "20240315130705.25+1400", DtmPrecision::fraction,
                "2024-03-15T13:07:05.25+14:00", 840},
        DtmCase{"leap_day", "20240229", DtmPrecision::day, "2024-02-29", std::nullopt},
        DtmCase{"leap_day_2000", "20000229", DtmPrecision::day, "2000-02-29", std::nullopt},
        DtmCase{"end_of_day", "20241231235959", DtmPrecision::second, "2024-12-31T23:59:59",
                std::nullopt}),
    [](const auto& info) { return std::string(info.param.name); });

struct InvalidCase {
    std::string_view name;
    std::string_view text;
};

class InvalidDtm : public ::testing::TestWithParam<InvalidCase> {};

TEST_P(InvalidDtm, IsTimestampInvalid) {
    const auto value = parse_dtm(GetParam().text, 10);
    ASSERT_FALSE(value.has_value());
    EXPECT_EQ(value.error().code, ErrorCode::timestamp_invalid);
    EXPECT_GE(value.error().offset, 10U);
}

INSTANTIATE_TEST_SUITE_P(
    Table, InvalidDtm,
    ::testing::Values(
        InvalidCase{"empty", ""}, InvalidCase{"short_year", "202"},
        InvalidCase{"odd_length", "2024031"}, InvalidCase{"too_long", "202403151307051"},
        InvalidCase{"letters", "2024AB15"}, InvalidCase{"month_zero", "20240015"},
        InvalidCase{"month_13", "20241315"}, InvalidCase{"day_zero", "20240300"},
        InvalidCase{"april_31", "20240431"}, InvalidCase{"feb_29_non_leap", "20230229"},
        InvalidCase{"feb_29_1900", "19000229"}, InvalidCase{"hour_24", "2024031524"},
        InvalidCase{"minute_60", "202403151360"}, InvalidCase{"second_60", "20240315130760"},
        InvalidCase{"fraction_without_seconds", "202403151307.5"},
        InvalidCase{"fraction_empty", "20240315130705."},
        InvalidCase{"fraction_5_digits", "20240315130705.12345"},
        InvalidCase{"offset_short", "20240315130705+000"},
        InvalidCase{"offset_letters", "20240315130705+00AB"},
        InvalidCase{"offset_minutes_60", "20240315130705+0060"},
        InvalidCase{"offset_too_east", "20240315130705+1500"},
        InvalidCase{"offset_too_west", "20240315130705-1300"}, InvalidCase{"only_offset", "+0000"},
        InvalidCase{"iso_format", "2024-03-15"}, InvalidCase{"spaces", " 20240315"}),
    [](const auto& info) { return std::string(info.param.name); });

struct NmCase {
    std::string_view name;
    std::string_view text;
    double value;
};

class ValidNm : public ::testing::TestWithParam<NmCase> {};

TEST_P(ValidNm, Parses) {
    const auto value = parse_nm(GetParam().text);
    ASSERT_TRUE(value.has_value()) << value.error().detail;
    EXPECT_DOUBLE_EQ(*value, GetParam().value);
}

INSTANTIATE_TEST_SUITE_P(
    Table, ValidNm,
    ::testing::Values(NmCase{"integer", "112", 112.0}, NmCase{"decimal", "38.6", 38.6},
                      NmCase{"leading_point", ".5", 0.5}, NmCase{"trailing_point", "7.", 7.0},
                      NmCase{"plus", "+3", 3.0}, NmCase{"minus", "-0.03", -0.03},
                      NmCase{"zero", "0", 0.0}, NmCase{"leading_zeros", "007.10", 7.1}),
    [](const auto& info) { return std::string(info.param.name); });

class InvalidNm : public ::testing::TestWithParam<InvalidCase> {};

TEST_P(InvalidNm, IsFieldValueInvalid) {
    const auto value = parse_nm(GetParam().text, 5);
    ASSERT_FALSE(value.has_value());
    EXPECT_EQ(value.error().code, ErrorCode::field_value_invalid);
    EXPECT_EQ(value.error().offset, 5U);
}

INSTANTIATE_TEST_SUITE_P(
    Table, InvalidNm,
    ::testing::Values(InvalidCase{"empty", ""}, InvalidCase{"sign_only", "-"},
                      InvalidCase{"point_only", "."}, InvalidCase{"letters", "abc"},
                      InvalidCase{"exponent", "1e5"}, InvalidCase{"two_points", "1.2.3"},
                      InvalidCase{"double_sign", "--1"}, InvalidCase{"space", " 12"},
                      InvalidCase{"trailing_space", "12 "}, InvalidCase{"comma", "1,5"},
                      InvalidCase{"nan", "NaN"}, InvalidCase{"inf", "inf"},
                      InvalidCase{"huge", kHugeNumber}),
    [](const auto& info) { return std::string(info.param.name); });

TEST(Cx, ReadsIdentifierAuthorityAndType) {
    const auto value = parse_cx("MRN0001234^7^^WARDWATCH&1.2.3&ISO^MR", EncodingCharacters{});
    ASSERT_TRUE(value.has_value());
    EXPECT_EQ(value->id, "MRN0001234");
    EXPECT_EQ(value->check_digit, "7");
    EXPECT_EQ(value->assigning_authority, "WARDWATCH");
    EXPECT_EQ(value->identifier_type, "MR");
}

TEST(Cx, DecodesEscapes) {
    const auto value = parse_cx("A\\S\\B^^^WW^MR", EncodingCharacters{});
    ASSERT_TRUE(value.has_value());
    EXPECT_EQ(value->id, "A^B");
}

TEST(Cx, RejectsEmptyIdentifier) {
    const auto value = parse_cx("^^^WW^MR", EncodingCharacters{}, 3);
    ASSERT_FALSE(value.has_value());
    EXPECT_EQ(value.error().code, ErrorCode::field_value_invalid);
    EXPECT_EQ(value.error().offset, 3U);
}

TEST(Cx, PropagatesEscapeErrorsWithOffset) {
    const auto value = parse_cx("MRN^^^W\\Q\\W^MR", EncodingCharacters{}, 100);
    ASSERT_FALSE(value.has_value());
    EXPECT_EQ(value.error().code, ErrorCode::escape_sequence_invalid);
    EXPECT_EQ(value.error().offset, 107U);
}

TEST(Xpn, ReadsNameParts) {
    const auto value = parse_xpn("Lindgren^Ada^M^Jr^Dr^^L", EncodingCharacters{});
    ASSERT_TRUE(value.has_value());
    EXPECT_EQ(value->family, "Lindgren");
    EXPECT_EQ(value->given, "Ada");
    EXPECT_EQ(value->middle, "M");
    EXPECT_EQ(value->suffix, "Jr");
    EXPECT_EQ(value->prefix, "Dr");
    EXPECT_EQ(value->name_type, "L");
}

TEST(Xpn, FamilySurnameSubcomponentIsFirstPart) {
    const auto value = parse_xpn("van Dijk&van&Dijk^Sam", EncodingCharacters{});
    ASSERT_TRUE(value.has_value());
    EXPECT_EQ(value->family, "van Dijk");
}

TEST(Xpn, AcceptsGivenOnly) { EXPECT_TRUE(parse_xpn("^Cher", EncodingCharacters{}).has_value()); }

TEST(Xpn, RejectsEmptyName) {
    const auto value = parse_xpn("^^M", EncodingCharacters{});
    ASSERT_FALSE(value.has_value());
    EXPECT_EQ(value.error().code, ErrorCode::field_value_invalid);
}

TEST(Cwe, ReadsAllSixComponents) {
    const auto value = parse_cwe("8867-4^Heart rate^LN^HR^Pulse^L", EncodingCharacters{});
    ASSERT_TRUE(value.has_value());
    EXPECT_EQ(value->identifier, "8867-4");
    EXPECT_EQ(value->text, "Heart rate");
    EXPECT_EQ(value->coding_system, "LN");
    EXPECT_EQ(value->alternate_identifier, "HR");
    EXPECT_EQ(value->alternate_text, "Pulse");
    EXPECT_EQ(value->alternate_coding_system, "L");
}

TEST(Cwe, TextOnlyIsAccepted) {
    const auto value = parse_cwe("^Heart rate", EncodingCharacters{});
    ASSERT_TRUE(value.has_value());
    EXPECT_TRUE(value->identifier.empty());
}

TEST(Cwe, RejectsEmpty) {
    EXPECT_FALSE(parse_cwe("", EncodingCharacters{}).has_value());
    EXPECT_FALSE(parse_cwe("^^LN", EncodingCharacters{}).has_value());
}

TEST(Cwe, UsesCustomDelimiters) {
    EncodingCharacters encoding;
    encoding.component = '$';
    const auto value = parse_cwe("8867-4$Heart rate$LN", encoding);
    ASSERT_TRUE(value.has_value());
    EXPECT_EQ(value->coding_system, "LN");
}

}  // namespace
}  // namespace wardwatch
