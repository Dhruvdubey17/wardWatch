#pragma once

#include <chrono>
#include <optional>
#include <string>
#include <string_view>

#include "wardwatch/error.hpp"
#include "wardwatch/validator.hpp"

namespace wardwatch {

// Used as MSH-3 and MSH-4 of an ACK when the original message named no
// receiving application or facility.
inline constexpr std::string_view kIngestApplication = "WARDWATCH_INGEST";
inline constexpr std::string_view kIngestFacility = "WARDWATCH";

// HL7 v2.5.1 table 0357 message error condition code for one of our codes.
struct Hl7ErrorCondition {
    std::string_view code;
    std::string_view text;
};

[[nodiscard]] Hl7ErrorCondition hl7_error_condition(ErrorCode code) noexcept;

// Formats a UTC time as an HL7 DTM with second precision and a +0000 offset.
[[nodiscard]] std::string format_hl7_timestamp(std::chrono::system_clock::time_point time);

// Builds an original-mode acknowledgment (HL7 v2.5.1 section 2.9.2): MSH and
// MSA, plus ERR when an error is given. The result is CR-terminated and not
// MLLP-framed.
[[nodiscard]] std::string build_ack(const MessageSummary& original, AckCode code,
                                    const std::optional<ParseError>& error,
                                    std::string_view ack_control_id,
                                    std::chrono::system_clock::time_point now);

}  // namespace wardwatch
