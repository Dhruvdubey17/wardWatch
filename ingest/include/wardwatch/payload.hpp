#pragma once

#include <chrono>
#include <expected>
#include <span>
#include <string>
#include <string_view>

#include "wardwatch/error.hpp"
#include "wardwatch/message.hpp"
#include "wardwatch/validator.hpp"

namespace wardwatch {

// Stages named in the hl7.deadletter contract.
enum class DeadLetterStage : std::uint8_t { frame, parse, validate };

[[nodiscard]] std::string_view to_string(DeadLetterStage stage) noexcept;

[[nodiscard]] std::string base64_encode(std::span<const char> bytes);

// ISO 8601 UTC with microseconds, for example 2024-03-15T13:00:05.012345Z.
[[nodiscard]] std::string format_received_at(std::chrono::system_clock::time_point time);

// The hl7.validated payload (contracts/schemas/hl7.validated.schema.json).
// Every field is decoded here, so an escape error in a field the validator did
// not read surfaces now and rejects the message.
[[nodiscard]] std::expected<std::string, ParseError> build_validated_payload(
    const Message& message, const ValidationResult& result,
    std::chrono::system_clock::time_point received_at);

// The hl7.deadletter payload (contracts/schemas/hl7.deadletter.schema.json).
[[nodiscard]] std::string build_deadletter_payload(
    DeadLetterStage stage, const ParseError& error, std::string_view control_id,
    std::span<const char> raw, bool raw_truncated,
    std::chrono::system_clock::time_point received_at);

}  // namespace wardwatch
