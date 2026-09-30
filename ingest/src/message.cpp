#include "wardwatch/message.hpp"

#include <algorithm>
#include <format>
#include <iterator>
#include <string>
#include <utility>

namespace wardwatch {
namespace {

constexpr std::size_t kSegmentIdLength = 3;
constexpr std::size_t kMshCharacterSetField = 18;

bool is_upper(char c) noexcept { return c >= 'A' && c <= 'Z'; }
bool is_digit(char c) noexcept { return c >= '0' && c <= '9'; }

bool is_valid_segment_id(std::string_view id) {
    return id.size() == kSegmentIdLength && is_upper(id[0]) &&
           std::ranges::all_of(id.substr(1), [](char c) { return is_upper(c) || is_digit(c); });
}

std::string location(std::string_view id, std::size_t occurrence) {
    return std::format("{}[{}]", id, occurrence);
}

// Where each segment's fields live in the flat field table while tokenizing.
// Spans are built only after the table stops growing, because growing it
// moves the storage.
struct PendingSegment {
    std::string_view id;
    std::size_t first_field;
    std::size_t field_count;
    std::size_t offset;
    std::size_t occurrence;
};

}  // namespace

std::string_view SegmentView::field(std::size_t number) const noexcept {
    if (number == 0 || number > fields.size()) {
        return {};
    }
    return fields[number - 1];
}

std::expected<Message, ParseError> Message::parse(std::vector<char>& bytes) {
    Message message;
    message.buffer_ = std::move(bytes);
    if (auto tokenized = message.tokenize(); !tokenized) {
        bytes = std::move(message.buffer_);
        return std::unexpected(std::move(tokenized.error()));
    }
    return message;
}

std::expected<Message, ParseError> Message::parse(std::string_view bytes) {
    std::vector<char> copy(bytes.begin(), bytes.end());
    return parse(copy);
}

std::expected<void, ParseError> Message::tokenize() {
    const std::string_view text = raw();
    auto encoding = read_encoding_characters(text);
    if (!encoding) {
        return std::unexpected(std::move(encoding.error()));
    }
    encoding_ = *encoding;

    std::vector<PendingSegment> pending;
    std::vector<std::pair<std::string_view, std::size_t>> occurrences;
    bool warned_terminator = false;
    bool warned_trailing = false;

    std::size_t position = 0;
    while (position < text.size()) {
        std::size_t end = position;
        while (end < text.size() && text[end] != '\r' && text[end] != '\n') {
            ++end;
        }
        const std::string_view segment_text = text.substr(position, end - position);
        const std::size_t segment_offset = position;

        std::size_t next = end;
        if (next < text.size()) {
            const bool crlf =
                text[next] == '\r' && next + 1 < text.size() && text[next + 1] == '\n';
            if (text[next] == '\n' || crlf) {
                if (!warned_terminator) {
                    warnings_.push_back(
                        Warning{WarningCode::segment_terminator_not_cr,
                                crlf ? "segments end with CRLF" : "segments end with LF", ""});
                    warned_terminator = true;
                }
            }
            next += crlf ? 2 : 1;
        }
        position = next;

        // Blank lines between segments are common when LF-terminated feeds pass
        // through text tools; they carry nothing and are skipped.
        if (segment_text.empty()) {
            continue;
        }

        const std::string_view id = segment_text.substr(0, kSegmentIdLength);
        if (!is_valid_segment_id(id) || (segment_text.size() > kSegmentIdLength &&
                                         segment_text[kSegmentIdLength] != encoding_.field)) {
            return std::unexpected(ParseError{
                ErrorCode::segment_id_invalid, segment_offset,
                std::format("segment starting at byte {} has no valid three-character id",
                            segment_offset)});
        }
        if (pending.empty() && id != "MSH") {
            return std::unexpected(
                ParseError{ErrorCode::msh_missing, segment_offset, "first segment is not MSH"});
        }

        auto counter =
            std::ranges::find(occurrences, id, &std::pair<std::string_view, std::size_t>::first);
        if (counter == occurrences.end()) {
            occurrences.emplace_back(id, 0);
            counter = std::prev(occurrences.end());
        }
        const std::size_t occurrence = ++counter->second;

        const std::size_t first_field = fields_.size();
        if (segment_text.size() > kSegmentIdLength) {
            std::string_view rest = segment_text.substr(kSegmentIdLength + 1);
            if (id == "MSH" && pending.empty()) {
                fields_.push_back(segment_text.substr(kSegmentIdLength, 1));
            }
            while (true) {
                const std::size_t separator = rest.find(encoding_.field);
                fields_.push_back(rest.substr(0, separator));
                if (separator == std::string_view::npos) {
                    break;
                }
                rest.remove_prefix(separator + 1);
            }
            if (segment_text.back() == encoding_.field && !warned_trailing) {
                warnings_.push_back(Warning{WarningCode::trailing_separator,
                                            "segment ends with an empty trailing field",
                                            location(id, occurrence)});
                warned_trailing = true;
            }
        }
        pending.push_back(PendingSegment{id, first_field, fields_.size() - first_field,
                                         segment_offset, occurrence});
    }

    if (pending.empty()) {
        return std::unexpected(ParseError{ErrorCode::msh_missing, 0, "message has no segments"});
    }

    segments_.reserve(pending.size());
    const std::span<const std::string_view> all_fields = fields_;
    for (const auto& segment : pending) {
        segments_.push_back(
            SegmentView{segment.id, all_fields.subspan(segment.first_field, segment.field_count),
                        segment.offset, segment.occurrence});
    }

    // Without MSH-18 the default character set is 7-bit ASCII (HL7 v2.5.1
    // section 2.15.9.18), so any high byte means the sender and receiver may
    // disagree about the text.
    const bool declares_charset = !segments_.front().field(kMshCharacterSetField).empty();
    const auto* const high_byte =
        std::ranges::find_if(text, [](char c) { return static_cast<unsigned char>(c) >= 0x80; });
    if (!declares_charset && high_byte != text.end()) {
        warnings_.push_back(Warning{
            WarningCode::non_ascii_bytes,
            std::format("byte {} is outside 7-bit ASCII and MSH-18 declares no character set",
                        std::distance(text.begin(), high_byte)),
            ""});
    }
    return {};
}

const SegmentView* Message::find(std::string_view id) const noexcept {
    const auto found = std::ranges::find(segments_, id, &SegmentView::id);
    return found == segments_.end() ? nullptr : &*found;
}

std::vector<const SegmentView*> Message::find_all(std::string_view id) const {
    std::vector<const SegmentView*> found;
    for (const auto& segment : segments_) {
        if (segment.id == id) {
            found.push_back(&segment);
        }
    }
    return found;
}

std::size_t Message::count(std::string_view id) const noexcept {
    return static_cast<std::size_t>(std::ranges::count(segments_, id, &SegmentView::id));
}

std::size_t Message::offset_of(std::string_view view) const noexcept {
    return static_cast<std::size_t>(view.data() - buffer_.data());
}

std::string_view nth_piece(std::string_view text, char separator, std::size_t number) {
    if (number == 0) {
        return {};
    }
    for (std::size_t index = 1; index < number; ++index) {
        const std::size_t found = text.find(separator);
        if (found == std::string_view::npos) {
            return {};
        }
        text.remove_prefix(found + 1);
    }
    return text.substr(0, text.find(separator));
}

std::size_t piece_count(std::string_view text, char separator) noexcept {
    if (text.empty()) {
        return 0;
    }
    return static_cast<std::size_t>(std::ranges::count(text, separator)) + 1;
}

std::string_view subcomponent(std::string_view field, const EncodingCharacters& encoding,
                              std::size_t repetition, std::size_t component,
                              std::size_t subcomponent_number) {
    const std::string_view repeated = nth_piece(field, encoding.repetition, repetition);
    const std::string_view composite = nth_piece(repeated, encoding.component, component);
    return nth_piece(composite, encoding.subcomponent, subcomponent_number);
}

}  // namespace wardwatch
