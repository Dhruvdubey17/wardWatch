#pragma once

#include <cstddef>
#include <expected>
#include <span>
#include <string_view>
#include <vector>

#include "wardwatch/encoding.hpp"
#include "wardwatch/error.hpp"

namespace wardwatch {

// One segment of a parsed message. Every view points into the buffer owned by
// the Message, so a SegmentView must not outlive it.
struct SegmentView {
    std::string_view id;
    // fields[0] is field 1. For MSH, field 1 is the field separator itself and
    // field 2 holds the encoding characters verbatim (HL7 v2.5.1 section 2.14.9).
    std::span<const std::string_view> fields;
    std::size_t offset = 0;
    // 1-based count among segments with the same id, used in locations such
    // as OBX[3]-5.
    std::size_t occurrence = 0;

    // Returns field `number` (1-based) or an empty view when it is absent.
    [[nodiscard]] std::string_view field(std::size_t number) const noexcept;
};

// A parsed HL7 v2 message that owns one buffer and exposes views into it.
// Components, repetitions and subcomponents are split on demand by the free
// functions below, so parsing allocates only the field and segment tables.
class Message {
  public:
    // Accepts CR, LF or CRLF segment terminators; anything other than CR adds
    // a SEGMENT_TERMINATOR_NOT_CR warning. Takes ownership of `bytes` only on
    // success, so a caller can still dead-letter the original bytes.
    [[nodiscard]] static std::expected<Message, ParseError> parse(std::vector<char>& bytes);
    // Copies the bytes first; for tests and tools.
    [[nodiscard]] static std::expected<Message, ParseError> parse(std::string_view bytes);

    Message(Message&&) noexcept = default;
    Message& operator=(Message&&) noexcept = default;
    Message(const Message&) = delete;
    Message& operator=(const Message&) = delete;
    ~Message() = default;

    [[nodiscard]] std::string_view raw() const noexcept { return {buffer_.data(), buffer_.size()}; }
    [[nodiscard]] const EncodingCharacters& encoding() const noexcept { return encoding_; }
    [[nodiscard]] std::span<const SegmentView> segments() const noexcept { return segments_; }
    [[nodiscard]] std::span<const Warning> warnings() const noexcept { return warnings_; }

    // First segment with this id, or nullptr.
    [[nodiscard]] const SegmentView* find(std::string_view id) const noexcept;
    [[nodiscard]] std::vector<const SegmentView*> find_all(std::string_view id) const;
    [[nodiscard]] std::size_t count(std::string_view id) const noexcept;

    // Byte offset of a view that points into this message's buffer.
    [[nodiscard]] std::size_t offset_of(std::string_view view) const noexcept;

  private:
    Message() = default;
    std::expected<void, ParseError> tokenize();

    // A vector keeps its heap buffer when moved, which the views rely on. A
    // std::string would not, because of the small string optimisation.
    std::vector<char> buffer_;
    EncodingCharacters encoding_;
    std::vector<std::string_view> fields_;
    std::vector<SegmentView> segments_;
    std::vector<Warning> warnings_;
};

// The n-th piece (1-based) of text split on a separator, or an empty view.
[[nodiscard]] std::string_view nth_piece(std::string_view text, char separator, std::size_t number);

// Number of pieces; an empty text has zero pieces.
[[nodiscard]] std::size_t piece_count(std::string_view text, char separator) noexcept;

// Addresses one subcomponent of a field value, all positions 1-based:
// repetition, then component, then subcomponent. The result is still escaped.
[[nodiscard]] std::string_view subcomponent(std::string_view field,
                                            const EncodingCharacters& encoding,
                                            std::size_t repetition, std::size_t component,
                                            std::size_t subcomponent_number = 1);

}  // namespace wardwatch
