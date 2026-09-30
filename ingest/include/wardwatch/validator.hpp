#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "wardwatch/error.hpp"
#include "wardwatch/message.hpp"
#include "wardwatch/types.hpp"

namespace wardwatch {

// MSA-1 acknowledgment codes in original acknowledgment mode (HL7 v2.5.1
// table 0008).
enum class AckCode : std::uint8_t { aa, ae, ar };

[[nodiscard]] std::string_view to_string(AckCode code) noexcept;

// AR for messages that cannot be read or are not a type we handle, AE for
// content errors in messages that are otherwise well formed.
[[nodiscard]] AckCode ack_code_for(ErrorCode code) noexcept;

enum class Disposition : std::uint8_t {
    accepted,
    // Same MSH-10 and same bytes as a message already accepted: the sender
    // retried after a lost ACK. It is acknowledged again but not republished.
    retransmission,
    rejected,
};

// The MSH and PID fields the rest of the pipeline and the ACK need, decoded.
struct MessageSummary {
    std::string sending_application;
    std::string sending_facility;
    std::string receiving_application;
    std::string receiving_facility;
    std::string control_id;
    std::string message_type;
    std::string trigger_event;
    std::string processing_id;
    std::string version;
    std::optional<DateTime> message_time;
    std::string mrn;
};

struct ValidationResult {
    Disposition disposition = Disposition::rejected;
    AckCode ack_code = AckCode::ar;
    std::optional<ParseError> error;
    // Tokenizer warnings followed by validation warnings.
    std::vector<Warning> warnings;
    MessageSummary summary;
};

struct ProcessedMessage {
    ValidationResult result;
    // Present whenever parsing succeeded, even if validation rejected it.
    std::optional<Message> message;
    // The original bytes when parsing failed, for the dead letter.
    std::vector<char> unparsed;

    [[nodiscard]] std::string_view raw() const noexcept {
        return message ? message->raw() : std::string_view(unparsed.data(), unparsed.size());
    }
};

// Best-effort read of MSH fields from bytes that failed to parse, so the ACK
// and dead letter can still name the control ID. Fields that cannot be found
// are left empty.
[[nodiscard]] MessageSummary sniff_header(std::string_view raw);

// FNV-1a over the message bytes, used to tell a retransmission from a
// different message that reuses a control ID.
[[nodiscard]] std::uint64_t content_hash(std::string_view raw) noexcept;

// Remembers the control IDs of the last `capacity` accepted messages.
class ControlIdRegistry {
  public:
    enum class Seen : std::uint8_t { new_id, same_content, different_content };

    explicit ControlIdRegistry(std::size_t capacity) : capacity_(capacity) {}

    [[nodiscard]] Seen check(std::string_view control_id, std::uint64_t hash) const;
    void remember(std::string_view control_id, std::uint64_t hash);
    [[nodiscard]] std::size_t size() const noexcept { return hashes_.size(); }

  private:
    std::size_t capacity_;
    std::unordered_map<std::string, std::uint64_t> hashes_;
    std::deque<std::string> order_;
};

// Applies the rule set in CLAUDE.md. Errors reject the message; warnings are
// attached and the message is accepted. Not thread-safe: the validation
// thread owns one instance.
class Validator {
  public:
    explicit Validator(std::size_t duplicate_window = 100'000) : registry_(duplicate_window) {}

    [[nodiscard]] ValidationResult validate(const Message& message);

    // Parses then validates. Parse failures are rejected with AR.
    [[nodiscard]] ProcessedMessage process(std::vector<char> bytes);

  private:
    ControlIdRegistry registry_;
};

}  // namespace wardwatch
