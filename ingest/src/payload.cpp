#include "wardwatch/payload.hpp"

#include <array>
#include <format>

#include <nlohmann/json.hpp>

namespace wardwatch {
namespace {

using nlohmann::json;

// MSH-1 and MSH-2 are carried verbatim: splitting the encoding characters on
// themselves would be meaningless (HL7 v2.5.1 section 2.14.9.1 and .2).
constexpr std::size_t kVerbatimMshFields = 2;

constexpr std::string_view kReplacementCharacter = "\xEF\xBF\xBD";

// Length of the valid UTF-8 sequence starting at text[index], or 0 when the
// bytes there are not valid UTF-8 (RFC 3629 section 4: no overlong forms, no
// surrogates, nothing above U+10FFFF).
std::size_t utf8_sequence_length(std::string_view text, std::size_t index) noexcept {
    const auto byte = [&](std::size_t at) { return static_cast<unsigned char>(text[at]); };
    const unsigned char lead = byte(index);
    std::size_t length = 0;
    unsigned char low = 0x80;
    unsigned char high = 0xBF;
    if (lead >= 0xC2 && lead <= 0xDF) {
        length = 2;
    } else if (lead >= 0xE0 && lead <= 0xEF) {
        length = 3;
        low = lead == 0xE0 ? 0xA0 : 0x80;
        high = lead == 0xED ? 0x9F : 0xBF;
    } else if (lead >= 0xF0 && lead <= 0xF4) {
        length = 4;
        low = lead == 0xF0 ? 0x90 : 0x80;
        high = lead == 0xF4 ? 0x8F : 0xBF;
    } else {
        return 0;
    }
    if (index + length > text.size()) {
        return 0;
    }
    const unsigned char second = byte(index + 1);
    if (second < low || second > high) {
        return 0;
    }
    for (std::size_t offset = 2; offset < length; ++offset) {
        const unsigned char next = byte(index + offset);
        if (next < 0x80 || next > 0xBF) {
            return 0;
        }
    }
    return length;
}

// Appends text as a JSON string (RFC 8259 section 7). Invalid UTF-8 becomes
// U+FFFD, one replacement per bad byte, so the document stays valid while
// raw_base64 keeps the original bytes.
void append_json_string(std::string& out, std::string_view text) {
    static constexpr std::string_view kHex = "0123456789abcdef";
    out.push_back('"');
    std::size_t index = 0;
    while (index < text.size()) {
        // Copy the run of characters that need no escaping in one append;
        // nearly every HL7 field is one such run.
        std::size_t run = index;
        while (run < text.size()) {
            const auto next = static_cast<unsigned char>(text[run]);
            if (next < 0x20 || next >= 0x80 || next == '"' || next == '\\') {
                break;
            }
            ++run;
        }
        out.append(text.substr(index, run - index));
        index = run;
        if (index == text.size()) {
            break;
        }
        const auto c = static_cast<unsigned char>(text[index]);
        if (c >= 0x80) {
            const std::size_t length = utf8_sequence_length(text, index);
            if (length == 0) {
                out.append(kReplacementCharacter);
                ++index;
            } else {
                out.append(text.substr(index, length));
                index += length;
            }
            continue;
        }
        switch (c) {
            case '"':
                out.append("\\\"");
                break;
            case '\\':
                out.append("\\\\");
                break;
            case '\n':
                out.append("\\n");
                break;
            case '\r':
                out.append("\\r");
                break;
            case '\t':
                out.append("\\t");
                break;
            default:
                if (c < 0x20) {
                    out.append("\\u00");
                    out.push_back(kHex[c >> 4U]);
                    out.push_back(kHex[c & 0x0FU]);
                } else {
                    out.push_back(static_cast<char>(c));
                }
        }
        ++index;
    }
    out.push_back('"');
}

// Writes one subcomponent, decoding escapes only when the text has any, so
// the common case copies straight from the message buffer.
std::expected<void, ParseError> append_subcomponent(std::string& out, const Message& message,
                                                    std::string_view raw) {
    if (!has_escapes(raw, message.encoding())) {
        append_json_string(out, raw);
        return {};
    }
    auto decoded = decode_escapes(raw, message.encoding(), message.offset_of(raw));
    if (!decoded) {
        return std::unexpected(std::move(decoded.error()));
    }
    append_json_string(out, *decoded);
    return {};
}

// Calls visit(piece) for each piece of text split on separator, in one pass.
// An empty text still yields one empty piece.
template <typename Visit>
std::expected<void, ParseError> for_each_piece(std::string_view text, char separator, Visit visit) {
    while (true) {
        const std::size_t end = text.find(separator);
        if (auto visited = visit(text.substr(0, end)); !visited) {
            return visited;
        }
        if (end == std::string_view::npos) {
            return {};
        }
        text.remove_prefix(end + 1);
    }
}

// A field as repetitions of components of subcomponents. An empty field is
// []; within a present repetition every level has at least one element, so
// "A^^B" is [[["A"],[""],["B"]]].
std::expected<void, ParseError> append_field(std::string& out, const Message& message,
                                             std::string_view field) {
    const auto& encoding = message.encoding();
    out.push_back('[');
    if (!field.empty()) {
        bool first_repetition = true;
        auto written = for_each_piece(field, encoding.repetition, [&](std::string_view repetition) {
            out.append(first_repetition ? "[" : ",[");
            first_repetition = false;
            bool first_component = true;
            auto components =
                for_each_piece(repetition, encoding.component, [&](std::string_view component) {
                    out.append(first_component ? "[" : ",[");
                    first_component = false;
                    bool first_subcomponent = true;
                    auto subcomponents =
                        for_each_piece(component, encoding.subcomponent, [&](std::string_view raw) {
                            if (!first_subcomponent) {
                                out.push_back(',');
                            }
                            first_subcomponent = false;
                            return append_subcomponent(out, message, raw);
                        });
                    out.push_back(']');
                    return subcomponents;
                });
            out.push_back(']');
            return components;
        });
        if (!written) {
            return written;
        }
    }
    out.push_back(']');
    return {};
}

std::expected<void, ParseError> append_segments(std::string& out, const Message& message) {
    out.push_back('[');
    bool first_segment = true;
    for (const auto& segment : message.segments()) {
        if (!first_segment) {
            out.push_back(',');
        }
        first_segment = false;
        out.append(R"({"id":)");
        append_json_string(out, segment.id);
        out.append(R"(,"fields":[)");
        const bool is_header = segment.offset == 0 && segment.id == "MSH";
        for (std::size_t index = 0; index < segment.fields.size(); ++index) {
            if (index > 0) {
                out.push_back(',');
            }
            const std::string_view field = segment.fields[index];
            if (is_header && index < kVerbatimMshFields) {
                out.append("[[[");
                append_json_string(out, field);
                out.append("]]]");
                continue;
            }
            if (auto written = append_field(out, message, field); !written) {
                return written;
            }
        }
        out.append("]}");
    }
    out.push_back(']');
    return {};
}

void append_warnings(std::string& out, const std::vector<Warning>& warnings) {
    out.push_back('[');
    for (std::size_t i = 0; i < warnings.size(); ++i) {
        if (i > 0) {
            out.push_back(',');
        }
        out.append(R"({"code":)");
        append_json_string(out, to_string(warnings[i].code));
        out.append(R"(,"detail":)");
        append_json_string(out, warnings[i].detail);
        if (!warnings[i].location.empty()) {
            out.append(R"(,"location":)");
            append_json_string(out, warnings[i].location);
        }
        out.push_back('}');
    }
    out.push_back(']');
}

// Invalid UTF-8 in a field (a Latin-1 byte, say) is replaced with U+FFFD in
// the JSON rather than failing the dump. raw_base64 keeps the original bytes.
std::string dump(const json& document) {
    return document.dump(-1, ' ', false, json::error_handler_t::replace);
}

}  // namespace

std::string_view to_string(DeadLetterStage stage) noexcept {
    switch (stage) {
        case DeadLetterStage::frame:
            return "frame";
        case DeadLetterStage::parse:
            return "parse";
        case DeadLetterStage::validate:
            return "validate";
    }
    return "validate";
}

std::string base64_encode(std::span<const char> bytes) {
    static constexpr std::string_view kAlphabet =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    const auto byte = [&](std::size_t index) {
        return static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[index]));
    };
    std::string out(((bytes.size() + 2) / 3) * 4, '=');
    std::size_t in = 0;
    std::size_t at = 0;
    for (; in + 2 < bytes.size(); in += 3, at += 4) {
        const std::uint32_t triple = (byte(in) << 16U) | (byte(in + 1) << 8U) | byte(in + 2);
        out[at] = kAlphabet[(triple >> 18U) & 0x3fU];
        out[at + 1] = kAlphabet[(triple >> 12U) & 0x3fU];
        out[at + 2] = kAlphabet[(triple >> 6U) & 0x3fU];
        out[at + 3] = kAlphabet[triple & 0x3fU];
    }
    const std::size_t remaining = bytes.size() - in;
    if (remaining > 0) {
        std::uint32_t triple = byte(in) << 16U;
        if (remaining == 2) {
            triple |= byte(in + 1) << 8U;
        }
        out[at] = kAlphabet[(triple >> 18U) & 0x3fU];
        out[at + 1] = kAlphabet[(triple >> 12U) & 0x3fU];
        if (remaining == 2) {
            out[at + 2] = kAlphabet[(triple >> 6U) & 0x3fU];
        }
    }
    return out;
}

std::string format_received_at(std::chrono::system_clock::time_point time) {
    // Formatted by hand: std::format's chrono path costs more than the rest
    // of the payload header put together.
    const auto micros = std::chrono::floor<std::chrono::microseconds>(time);
    const auto days = std::chrono::floor<std::chrono::days>(micros);
    const std::chrono::year_month_day date(days);
    const std::chrono::hh_mm_ss clock(micros - days);
    std::array<char, 27> out{};
    const auto put = [&](std::size_t at, std::uint64_t value, std::size_t width) {
        for (std::size_t i = width; i > 0; --i) {
            out.at(at + i - 1) = static_cast<char>('0' + (value % 10));
            value /= 10;
        }
    };
    put(0, static_cast<std::uint64_t>(static_cast<int>(date.year())), 4);
    out[4] = '-';
    put(5, static_cast<unsigned>(date.month()), 2);
    out[7] = '-';
    put(8, static_cast<unsigned>(date.day()), 2);
    out[10] = 'T';
    put(11, static_cast<std::uint64_t>(clock.hours().count()), 2);
    out[13] = ':';
    put(14, static_cast<std::uint64_t>(clock.minutes().count()), 2);
    out[16] = ':';
    put(17, static_cast<std::uint64_t>(clock.seconds().count()), 2);
    out[19] = '.';
    put(20, static_cast<std::uint64_t>(clock.subseconds().count()), 6);
    out[26] = 'Z';
    return {out.data(), out.size()};
}

std::expected<std::string, ParseError> build_validated_payload(
    const Message& message, const ValidationResult& result,
    std::chrono::system_clock::time_point received_at) {
    // Written directly rather than through a JSON DOM: building nested arrays
    // for every subcomponent cost ten times more than parsing the message.
    const auto& summary = result.summary;
    std::string out;
    out.reserve((message.raw().size() * 4) + 512);
    const auto key = [&](std::string_view name) {
        out.append(out.size() == 1 ? "\"" : ",\"");
        out.append(name);
        out.append("\":");
    };
    out.push_back('{');
    key("schema_version");
    out.push_back('1');
    key("control_id");
    append_json_string(out, summary.control_id);
    key("message_type");
    append_json_string(out, summary.message_type);
    key("trigger_event");
    append_json_string(out, summary.trigger_event);
    key("sending_facility");
    append_json_string(out, summary.sending_facility);
    key("mrn");
    append_json_string(out, summary.mrn);
    key("message_time");
    append_json_string(out, summary.message_time ? summary.message_time->to_iso8601() : "");
    key("received_at");
    append_json_string(out, format_received_at(received_at));
    key("segments");
    if (auto written = append_segments(out, message); !written) {
        return std::unexpected(std::move(written.error()));
    }
    key("warnings");
    append_warnings(out, result.warnings);
    key("raw_base64");
    out.push_back('"');
    out.append(base64_encode(std::span<const char>(message.raw().data(), message.raw().size())));
    out.append("\"}");
    return out;
}

std::string build_deadletter_payload(DeadLetterStage stage, const ParseError& error,
                                     std::string_view control_id, std::span<const char> raw,
                                     bool raw_truncated,
                                     std::chrono::system_clock::time_point received_at) {
    const json document{
        {"schema_version", 1},
        {"received_at", format_received_at(received_at)},
        {"stage", std::string(to_string(stage))},
        {"error_code", std::string(to_string(error.code))},
        {"error_detail", error.detail},
        {"control_id", control_id.empty() ? json(nullptr) : json(std::string(control_id))},
        {"raw_base64", base64_encode(raw)},
        {"raw_truncated", raw_truncated},
    };
    return dump(document);
}

}  // namespace wardwatch
