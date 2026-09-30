#include "wardwatch/error.hpp"

namespace wardwatch {

std::string_view to_string(ErrorCode code) noexcept {
    switch (code) {
        case ErrorCode::msh_missing:
            return "MSH_MISSING";
        case ErrorCode::encoding_characters_invalid:
            return "ENCODING_CHARACTERS_INVALID";
        case ErrorCode::segment_id_invalid:
            return "SEGMENT_ID_INVALID";
        case ErrorCode::escape_sequence_invalid:
            return "ESCAPE_SEQUENCE_INVALID";
        case ErrorCode::frame_oversize:
            return "FRAME_OVERSIZE";
        case ErrorCode::message_type_unsupported:
            return "MESSAGE_TYPE_UNSUPPORTED";
        case ErrorCode::required_segment_missing:
            return "REQUIRED_SEGMENT_MISSING";
        case ErrorCode::control_id_missing:
            return "CONTROL_ID_MISSING";
        case ErrorCode::control_id_duplicate:
            return "CONTROL_ID_DUPLICATE";
        case ErrorCode::timestamp_invalid:
            return "TIMESTAMP_INVALID";
        case ErrorCode::value_type_mismatch:
            return "VALUE_TYPE_MISMATCH";
        case ErrorCode::result_status_invalid:
            return "RESULT_STATUS_INVALID";
        case ErrorCode::patient_identifier_missing:
            return "PATIENT_IDENTIFIER_MISSING";
        case ErrorCode::field_value_invalid:
            return "FIELD_VALUE_INVALID";
    }
    return "UNKNOWN";
}

std::string_view to_string(WarningCode code) noexcept {
    switch (code) {
        case WarningCode::segment_terminator_not_cr:
            return "SEGMENT_TERMINATOR_NOT_CR";
        case WarningCode::trailing_separator:
            return "TRAILING_SEPARATOR";
        case WarningCode::non_ascii_bytes:
            return "NON_ASCII_BYTES";
        case WarningCode::observation_code_unknown:
            return "OBSERVATION_CODE_UNKNOWN";
        case WarningCode::value_implausible:
            return "VALUE_IMPLAUSIBLE";
    }
    return "UNKNOWN";
}

}  // namespace wardwatch
