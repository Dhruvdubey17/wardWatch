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

std::expected<json, ParseError> field_json(const Message& message, std::string_view field) {
    const auto& encoding = message.encoding();
    json repetitions = json::array();
    if (field.empty()) {
        return repetitions;
    }
    const std::size_t repetition_count = piece_count(field, encoding.repetition);
    for (std::size_t r = 1; r <= repetition_count; ++r) {
        const std::string_view repetition = nth_piece(field, encoding.repetition, r);
        json components = json::array();
        const std::size_t component_count =
            std::max<std::size_t>(1, piece_count(repetition, encoding.component));
        for (std::size_t c = 1; c <= component_count; ++c) {
            const std::string_view component = nth_piece(repetition, encoding.component, c);
            json subcomponents = json::array();
            const std::size_t sub_count =
                std::max<std::size_t>(1, piece_count(component, encoding.subcomponent));
            for (std::size_t s = 1; s <= sub_count; ++s) {
                const std::string_view raw = nth_piece(component, encoding.subcomponent, s);
                auto decoded = decode_escapes(raw, encoding,
                                              raw.data() == nullptr ? 0 : message.offset_of(raw));
                if (!decoded) {
                    return std::unexpected(std::move(decoded.error()));
                }
                subcomponents.push_back(std::move(*decoded));
            }
            components.push_back(std::move(subcomponents));
        }
        repetitions.push_back(std::move(components));
    }
    return repetitions;
}

std::expected<json, ParseError> segments_json(const Message& message) {
    json segments = json::array();
    for (const auto& segment : message.segments()) {
        json fields = json::array();
        const bool is_header = segment.offset == 0 && segment.id == "MSH";
        for (std::size_t index = 0; index < segment.fields.size(); ++index) {
            const std::string_view field = segment.fields[index];
            if (is_header && index < kVerbatimMshFields) {
                fields.push_back(json::array({json::array({json::array({std::string(field)})})}));
                continue;
            }
            auto converted = field_json(message, field);
            if (!converted) {
                return std::unexpected(std::move(converted.error()));
            }
            fields.push_back(std::move(*converted));
        }
        segments.push_back(json{{"id", std::string(segment.id)}, {"fields", std::move(fields)}});
    }
    return segments;
}

json warnings_json(const std::vector<Warning>& warnings) {
    json out = json::array();
    for (const auto& warning : warnings) {
        json entry{{"code", std::string(to_string(warning.code))}, {"detail", warning.detail}};
        if (!warning.location.empty()) {
            entry["location"] = warning.location;
        }
        out.push_back(std::move(entry));
    }
    return out;
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
    std::string out;
    out.reserve(((bytes.size() + 2) / 3) * 4);
    std::size_t index = 0;
    for (; index + 2 < bytes.size(); index += 3) {
        const auto triple =
            (static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[index])) << 16U) |
            (static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[index + 1])) << 8U) |
            static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[index + 2]));
        out.push_back(kAlphabet[(triple >> 18U) & 0x3fU]);
        out.push_back(kAlphabet[(triple >> 12U) & 0x3fU]);
        out.push_back(kAlphabet[(triple >> 6U) & 0x3fU]);
        out.push_back(kAlphabet[triple & 0x3fU]);
    }
    const std::size_t remaining = bytes.size() - index;
    if (remaining > 0) {
        std::uint32_t triple = static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[index]))
                               << 16U;
        if (remaining == 2) {
            triple |= static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[index + 1]))
                      << 8U;
        }
        out.push_back(kAlphabet[(triple >> 18U) & 0x3fU]);
        out.push_back(kAlphabet[(triple >> 12U) & 0x3fU]);
        out.push_back(remaining == 2 ? kAlphabet[(triple >> 6U) & 0x3fU] : '=');
        out.push_back('=');
    }
    return out;
}

std::string format_received_at(std::chrono::system_clock::time_point time) {
    const auto micros = std::chrono::floor<std::chrono::microseconds>(time);
    return std::format("{:%Y-%m-%dT%H:%M:%S}Z", micros);
}

std::expected<std::string, ParseError> build_validated_payload(
    const Message& message, const ValidationResult& result,
    std::chrono::system_clock::time_point received_at) {
    auto segments = segments_json(message);
    if (!segments) {
        return std::unexpected(std::move(segments.error()));
    }
    const auto& summary = result.summary;
    const json document{
        {"schema_version", 1},
        {"control_id", summary.control_id},
        {"message_type", summary.message_type},
        {"trigger_event", summary.trigger_event},
        {"sending_facility", summary.sending_facility},
        {"mrn", summary.mrn},
        {"message_time", summary.message_time ? summary.message_time->to_iso8601() : ""},
        {"received_at", format_received_at(received_at)},
        {"segments", std::move(*segments)},
        {"warnings", warnings_json(result.warnings)},
        {"raw_base64",
         base64_encode(std::span<const char>(message.raw().data(), message.raw().size()))},
    };
    return dump(document);
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
