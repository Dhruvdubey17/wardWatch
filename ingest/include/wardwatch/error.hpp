#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace wardwatch {

// Error codes shared with contracts/fault_catalog.json. to_string returns the
// catalog spelling, which is what reaches the dead-letter topic and metrics.
enum class ErrorCode : std::uint8_t {
    msh_missing,
    encoding_characters_invalid,
    segment_id_invalid,
    escape_sequence_invalid,
    frame_oversize,
    message_type_unsupported,
    required_segment_missing,
    control_id_missing,
    control_id_duplicate,
    timestamp_invalid,
    value_type_mismatch,
    result_status_invalid,
    patient_identifier_missing,
    field_value_invalid,
};

inline constexpr std::size_t kErrorCodeCount = 14;

enum class WarningCode : std::uint8_t {
    segment_terminator_not_cr,
    trailing_separator,
    non_ascii_bytes,
    observation_code_unknown,
    value_implausible,
};

inline constexpr std::size_t kWarningCodeCount = 5;

[[nodiscard]] std::string_view to_string(ErrorCode code) noexcept;
[[nodiscard]] std::string_view to_string(WarningCode code) noexcept;

struct ParseError {
    ErrorCode code;
    // Byte offset into the message where the problem was found.
    std::size_t offset = 0;
    std::string detail;
};

struct Warning {
    WarningCode code;
    std::string detail;
    // Segment, occurrence and field, for example "OBX[3]-5". Empty when the
    // warning applies to the whole message.
    std::string location;
};

}  // namespace wardwatch
