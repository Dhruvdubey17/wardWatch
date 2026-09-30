#include "wardwatch/types.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <format>
#include <system_error>

#include "wardwatch/message.hpp"

namespace wardwatch {
namespace {

constexpr int kMaxOffsetMinutes = 14 * 60;
constexpr int kMinOffsetMinutes = -12 * 60;
constexpr std::size_t kOffsetLength = 5;
constexpr std::size_t kMaxFractionDigits = 4;

ParseError timestamp_error(std::size_t offset, std::string detail) {
    return ParseError{ErrorCode::timestamp_invalid, offset, std::move(detail)};
}

ParseError value_error(std::size_t offset, std::string detail) {
    return ParseError{ErrorCode::field_value_invalid, offset, std::move(detail)};
}

bool all_digits(std::string_view text) noexcept {
    return std::ranges::all_of(text, [](char c) { return c >= '0' && c <= '9'; });
}

int to_int(std::string_view digits) noexcept {
    int value = 0;
    for (const char c : digits) {
        value = (value * 10) + (c - '0');
    }
    return value;
}

bool is_leap_year(int year) noexcept {
    return (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
}

int days_in_month(int year, int month) {
    constexpr std::array<int, 12> kDays{31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    if (month == 2 && is_leap_year(year)) {
        return 29;
    }
    return kDays.at(static_cast<std::size_t>(month - 1));
}

std::expected<std::string, ParseError> decoded_component(std::string_view repetition,
                                                         const EncodingCharacters& encoding,
                                                         std::size_t number,
                                                         std::size_t base_offset) {
    const std::string_view raw = nth_piece(repetition, encoding.component, number);
    // Only the first subcomponent is read; the pipeline has no use for the rest.
    const std::string_view first = nth_piece(raw, encoding.subcomponent, 1);
    const std::size_t offset =
        base_offset +
        (first.empty() ? 0 : static_cast<std::size_t>(first.data() - repetition.data()));
    return decode_escapes(first, encoding, offset);
}

// Which component number fills which member of a composite type.
template <typename Composite>
struct ComponentSlot {
    std::size_t component;
    std::string Composite::* member;
};

template <typename Composite, std::size_t N>
std::expected<void, ParseError> read_components(
    std::string_view repetition, const EncodingCharacters& encoding, std::size_t base_offset,
    const std::array<ComponentSlot<Composite>, N>& layout, Composite& value) {
    for (const auto& slot : layout) {
        auto decoded = decoded_component(repetition, encoding, slot.component, base_offset);
        if (!decoded) {
            return std::unexpected(std::move(decoded.error()));
        }
        value.*slot.member = std::move(*decoded);
    }
    return {};
}

}  // namespace

std::string DateTime::to_iso8601() const {
    std::string out = std::format("{:04}", year);
    if (precision >= DtmPrecision::month) {
        out += std::format("-{:02}", month);
    }
    if (precision >= DtmPrecision::day) {
        out += std::format("-{:02}", day);
    }
    if (precision >= DtmPrecision::hour) {
        out += std::format("T{:02}", hour);
    }
    if (precision >= DtmPrecision::minute) {
        out += std::format(":{:02}", minute);
    }
    if (precision >= DtmPrecision::second) {
        out += std::format(":{:02}", second);
    }
    if (precision == DtmPrecision::fraction) {
        out += std::format(".{:0{}}", fraction, fraction_digits);
    }
    if (utc_offset_minutes.has_value()) {
        const int magnitude = *utc_offset_minutes < 0 ? -*utc_offset_minutes : *utc_offset_minutes;
        out += std::format("{}{:02}:{:02}", *utc_offset_minutes < 0 ? '-' : '+', magnitude / 60,
                           magnitude % 60);
    }
    return out;
}

std::expected<DateTime, ParseError> parse_dtm(std::string_view text, std::size_t base_offset) {
    DateTime value;
    std::string_view body = text;

    const std::size_t sign = body.find_first_of("+-");
    if (sign != std::string_view::npos) {
        const std::string_view zone = body.substr(sign);
        if (zone.size() != kOffsetLength || !all_digits(zone.substr(1))) {
            return std::unexpected(
                timestamp_error(base_offset + sign, "UTC offset must be +ZZZZ or -ZZZZ"));
        }
        const int hours = to_int(zone.substr(1, 2));
        const int minutes = to_int(zone.substr(3, 2));
        const int total = ((hours * 60) + minutes) * (zone[0] == '-' ? -1 : 1);
        if (minutes >= 60 || total > kMaxOffsetMinutes || total < kMinOffsetMinutes) {
            return std::unexpected(timestamp_error(base_offset + sign, "UTC offset out of range"));
        }
        value.utc_offset_minutes = total;
        body = body.substr(0, sign);
    }

    std::string_view fraction;
    const std::size_t point = body.find('.');
    if (point != std::string_view::npos) {
        fraction = body.substr(point + 1);
        body = body.substr(0, point);
        if (fraction.empty() || fraction.size() > kMaxFractionDigits || !all_digits(fraction)) {
            return std::unexpected(
                timestamp_error(base_offset + point, "fractional seconds need 1 to 4 digits"));
        }
        if (body.size() != 14) {
            return std::unexpected(timestamp_error(
                base_offset + point, "fractional seconds need a full YYYYMMDDHHMMSS"));
        }
    }

    if (!all_digits(body) || body.size() < 4 || body.size() > 14 || body.size() % 2 != 0) {
        return std::unexpected(timestamp_error(
            base_offset, std::format("'{}' is not YYYY[MM[DD[HH[MM[SS]]]]]", body)));
    }

    value.year = to_int(body.substr(0, 4));
    value.precision = DtmPrecision::year;
    if (body.size() >= 6) {
        value.month = to_int(body.substr(4, 2));
        value.precision = DtmPrecision::month;
        if (value.month < 1 || value.month > 12) {
            return std::unexpected(timestamp_error(base_offset + 4, "month out of range"));
        }
    }
    if (body.size() >= 8) {
        value.day = to_int(body.substr(6, 2));
        value.precision = DtmPrecision::day;
        if (value.day < 1 || value.day > days_in_month(value.year, value.month)) {
            return std::unexpected(timestamp_error(base_offset + 6, "day out of range"));
        }
    }
    if (body.size() >= 10) {
        value.hour = to_int(body.substr(8, 2));
        value.precision = DtmPrecision::hour;
        if (value.hour > 23) {
            return std::unexpected(timestamp_error(base_offset + 8, "hour out of range"));
        }
    }
    if (body.size() >= 12) {
        value.minute = to_int(body.substr(10, 2));
        value.precision = DtmPrecision::minute;
        if (value.minute > 59) {
            return std::unexpected(timestamp_error(base_offset + 10, "minute out of range"));
        }
    }
    if (body.size() == 14) {
        value.second = to_int(body.substr(12, 2));
        value.precision = DtmPrecision::second;
        if (value.second > 59) {
            return std::unexpected(timestamp_error(base_offset + 12, "second out of range"));
        }
    }
    if (!fraction.empty()) {
        value.fraction = to_int(fraction);
        value.fraction_digits = static_cast<int>(fraction.size());
        value.precision = DtmPrecision::fraction;
    }
    return value;
}

std::expected<double, ParseError> parse_nm(std::string_view text, std::size_t base_offset) {
    std::string_view digits = text;
    bool negative = false;
    if (!digits.empty() && (digits[0] == '+' || digits[0] == '-')) {
        negative = digits[0] == '-';
        digits.remove_prefix(1);
    }
    const std::size_t point = digits.find('.');
    const std::string_view whole = digits.substr(0, point);
    const std::string_view part =
        point == std::string_view::npos ? std::string_view{} : digits.substr(point + 1);
    if ((whole.empty() && part.empty()) || !all_digits(whole) || !all_digits(part)) {
        return std::unexpected(
            value_error(base_offset, std::format("'{}' is not an NM value", text)));
    }
    double value = 0.0;
    const auto result = std::from_chars(digits.data(), digits.data() + digits.size(), value);
    if (result.ec != std::errc{} || result.ptr != digits.data() + digits.size()) {
        return std::unexpected(value_error(base_offset, std::format("'{}' is out of range", text)));
    }
    return negative ? -value : value;
}

std::expected<Cx, ParseError> parse_cx(std::string_view repetition,
                                       const EncodingCharacters& encoding,
                                       std::size_t base_offset) {
    static constexpr std::array<ComponentSlot<Cx>, 4> kLayout{{{1, &Cx::id},
                                                               {2, &Cx::check_digit},
                                                               {4, &Cx::assigning_authority},
                                                               {5, &Cx::identifier_type}}};
    Cx value;
    if (auto read = read_components(repetition, encoding, base_offset, kLayout, value); !read) {
        return std::unexpected(std::move(read.error()));
    }
    if (value.id.empty()) {
        return std::unexpected(value_error(base_offset, "CX-1 identifier is empty"));
    }
    return value;
}

std::expected<Xpn, ParseError> parse_xpn(std::string_view repetition,
                                         const EncodingCharacters& encoding,
                                         std::size_t base_offset) {
    static constexpr std::array<ComponentSlot<Xpn>, 6> kLayout{{{1, &Xpn::family},
                                                                {2, &Xpn::given},
                                                                {3, &Xpn::middle},
                                                                {4, &Xpn::suffix},
                                                                {5, &Xpn::prefix},
                                                                {7, &Xpn::name_type}}};
    Xpn value;
    if (auto read = read_components(repetition, encoding, base_offset, kLayout, value); !read) {
        return std::unexpected(std::move(read.error()));
    }
    if (value.family.empty() && value.given.empty()) {
        return std::unexpected(value_error(base_offset, "XPN has neither family nor given name"));
    }
    return value;
}

std::expected<Cwe, ParseError> parse_cwe(std::string_view repetition,
                                         const EncodingCharacters& encoding,
                                         std::size_t base_offset) {
    static constexpr std::array<ComponentSlot<Cwe>, 6> kLayout{
        {{1, &Cwe::identifier},
         {2, &Cwe::text},
         {3, &Cwe::coding_system},
         {4, &Cwe::alternate_identifier},
         {5, &Cwe::alternate_text},
         {6, &Cwe::alternate_coding_system}}};
    Cwe value;
    if (auto read = read_components(repetition, encoding, base_offset, kLayout, value); !read) {
        return std::unexpected(std::move(read.error()));
    }
    if (value.identifier.empty() && value.text.empty()) {
        return std::unexpected(value_error(base_offset, "CWE has neither identifier nor text"));
    }
    return value;
}

}  // namespace wardwatch
