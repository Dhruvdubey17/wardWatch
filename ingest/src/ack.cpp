#include "wardwatch/ack.hpp"

#include <format>

#include "wardwatch/encoding.hpp"

namespace wardwatch {
namespace {

std::string_view or_default(std::string_view value, std::string_view fallback) noexcept {
    return value.empty() ? fallback : value;
}

}  // namespace

Hl7ErrorCondition hl7_error_condition(ErrorCode code) noexcept {
    switch (code) {
        case ErrorCode::msh_missing:
        case ErrorCode::segment_id_invalid:
        case ErrorCode::required_segment_missing:
            return {"100", "Segment sequence error"};
        case ErrorCode::control_id_missing:
        case ErrorCode::patient_identifier_missing:
            return {"101", "Required field missing"};
        case ErrorCode::encoding_characters_invalid:
        case ErrorCode::escape_sequence_invalid:
        case ErrorCode::timestamp_invalid:
        case ErrorCode::value_type_mismatch:
        case ErrorCode::field_value_invalid:
            return {"102", "Data type error"};
        case ErrorCode::result_status_invalid:
            return {"103", "Table value not found"};
        case ErrorCode::message_type_unsupported:
            return {"200", "Unsupported message type"};
        case ErrorCode::control_id_duplicate:
            return {"205", "Duplicate key identifier"};
        case ErrorCode::frame_oversize:
            return {"207", "Application internal error"};
    }
    return {"207", "Application internal error"};
}

std::string format_hl7_timestamp(std::chrono::system_clock::time_point time) {
    const auto seconds = std::chrono::floor<std::chrono::seconds>(time);
    return std::format("{:%Y%m%d%H%M%S}+0000", seconds);
}

std::string build_ack(const MessageSummary& original, AckCode code,
                      const std::optional<ParseError>& error, std::string_view ack_control_id,
                      std::chrono::system_clock::time_point now) {
    // The ACK always uses the standard delimiters, whatever the original
    // declared, because the original's MSH-2 may be the reason it failed.
    const EncodingCharacters encoding;
    const auto escaped = [&](std::string_view text) { return encode_escapes(text, encoding); };

    std::string ack =
        std::format("MSH|^~\\&|{}|{}|{}|{}|{}||ACK^{}^ACK|{}|{}|{}\r",
                    escaped(or_default(original.receiving_application, kIngestApplication)),
                    escaped(or_default(original.receiving_facility, kIngestFacility)),
                    escaped(original.sending_application), escaped(original.sending_facility),
                    format_hl7_timestamp(now), escaped(original.trigger_event),
                    escaped(ack_control_id), escaped(or_default(original.processing_id, "P")),
                    escaped(or_default(original.version, "2.5.1")));
    ack += std::format("MSA|{}|{}\r", to_string(code), escaped(original.control_id));
    if (error.has_value()) {
        const auto condition = hl7_error_condition(error->code);
        // ERR-3 is the HL7 condition, ERR-4 the severity, ERR-5 our own code
        // in the local WWERR system, and ERR-8 the text for a person to read.
        ack += std::format("ERR|||{}^{}^HL70357|E|{}^^WWERR|||{}\r", condition.code, condition.text,
                           to_string(error->code), escaped(error->detail));
    }
    return ack;
}

}  // namespace wardwatch
