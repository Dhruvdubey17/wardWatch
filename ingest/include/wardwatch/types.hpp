#pragma once

#include <cstddef>
#include <cstdint>
#include <expected>
#include <optional>
#include <string>
#include <string_view>

#include "wardwatch/encoding.hpp"
#include "wardwatch/error.hpp"

namespace wardwatch {

enum class DtmPrecision : std::uint8_t { year, month, day, hour, minute, second, fraction };

// An HL7 DTM value (HL7 v2.5.1 section 2.A.22). Fields below the stated
// precision hold their minimum value and are not printed.
struct DateTime {
    int year = 0;
    int month = 1;
    int day = 1;
    int hour = 0;
    int minute = 0;
    int second = 0;
    // Fractional seconds as written, for example 1234 with 4 digits for .1234.
    int fraction = 0;
    int fraction_digits = 0;
    DtmPrecision precision = DtmPrecision::year;
    std::optional<int> utc_offset_minutes;

    // ISO 8601 at the same precision, with the offset as +hh:mm when present.
    [[nodiscard]] std::string to_iso8601() const;
};

// YYYY[MM[DD[HH[MM[SS[.S[S[S[S]]]]]]]]][+/-ZZZZ]. Errors are TIMESTAMP_INVALID.
[[nodiscard]] std::expected<DateTime, ParseError> parse_dtm(std::string_view text,
                                                            std::size_t base_offset = 0);

// An HL7 NM value: optional sign, digits, optional decimal point. No exponent
// and no surrounding spaces. Errors are FIELD_VALUE_INVALID.
[[nodiscard]] std::expected<double, ParseError> parse_nm(std::string_view text,
                                                         std::size_t base_offset = 0);

// The CX components the pipeline reads (HL7 v2.5.1 section 2.A.12).
struct Cx {
    std::string id;
    std::string check_digit;
    // Namespace ID of CX-4, the assigning authority.
    std::string assigning_authority;
    std::string identifier_type;
};

// XPN (HL7 v2.5.1 section 2.A.88). `given` is XPN-2, `middle` is XPN-3.
struct Xpn {
    std::string family;
    std::string given;
    std::string middle;
    std::string suffix;
    std::string prefix;
    std::string name_type;
};

// CWE (HL7 v2.5.1 section 2.A.13).
struct Cwe {
    std::string identifier;
    std::string text;
    std::string coding_system;
    std::string alternate_identifier;
    std::string alternate_text;
    std::string alternate_coding_system;
};

// Each composite parser takes one repetition of a field, still escaped, and
// returns decoded components. Errors are FIELD_VALUE_INVALID or
// ESCAPE_SEQUENCE_INVALID.
[[nodiscard]] std::expected<Cx, ParseError> parse_cx(std::string_view repetition,
                                                     const EncodingCharacters& encoding,
                                                     std::size_t base_offset = 0);
[[nodiscard]] std::expected<Xpn, ParseError> parse_xpn(std::string_view repetition,
                                                       const EncodingCharacters& encoding,
                                                       std::size_t base_offset = 0);
[[nodiscard]] std::expected<Cwe, ParseError> parse_cwe(std::string_view repetition,
                                                       const EncodingCharacters& encoding,
                                                       std::size_t base_offset = 0);

}  // namespace wardwatch
