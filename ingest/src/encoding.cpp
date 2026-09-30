#include "wardwatch/encoding.hpp"

#include <array>
#include <format>

namespace wardwatch {
namespace {

constexpr std::size_t kMshPrefixLength = 3;
constexpr std::size_t kMinEncodingLength = 4;
constexpr std::size_t kMaxEncodingLength = 5;

bool is_valid_delimiter(char c) noexcept {
    const auto byte = static_cast<unsigned char>(c);
    const bool printable = byte > 0x20 && byte < 0x7f;
    const bool alphanumeric =
        (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
    return printable && !alphanumeric;
}

int hex_value(char c) noexcept {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    return -1;
}

ParseError escape_error(std::size_t offset, std::string detail) {
    return ParseError{ErrorCode::escape_sequence_invalid, offset, std::move(detail)};
}

std::expected<void, ParseError> append_hex(std::string_view digits, std::size_t offset,
                                           std::string& out) {
    if (digits.empty() || digits.size() % 2 != 0) {
        return std::unexpected(escape_error(offset, "hex escape needs an even number of digits"));
    }
    for (std::size_t i = 0; i < digits.size(); i += 2) {
        const int high = hex_value(digits[i]);
        const int low = hex_value(digits[i + 1]);
        if (high < 0 || low < 0) {
            return std::unexpected(escape_error(offset, "hex escape has a non-hex digit"));
        }
        out.push_back(static_cast<char>((high << 4) | low));
    }
    return {};
}

}  // namespace

bool EncodingCharacters::is_delimiter(char c) const noexcept {
    return c == field || c == component || c == repetition || c == escape || c == subcomponent ||
           (truncation.has_value() && c == *truncation);
}

std::expected<EncodingCharacters, ParseError> read_encoding_characters(std::string_view message) {
    if (!message.starts_with("MSH")) {
        return std::unexpected(
            ParseError{ErrorCode::msh_missing, 0, "message does not start with MSH"});
    }
    if (message.size() <= kMshPrefixLength) {
        return std::unexpected(ParseError{ErrorCode::encoding_characters_invalid, kMshPrefixLength,
                                          "MSH-1 is missing"});
    }
    const char field = message[kMshPrefixLength];
    const std::size_t start = kMshPrefixLength + 1;
    std::size_t end = start;
    while (end < message.size() && message[end] != field && message[end] != '\r' &&
           message[end] != '\n') {
        ++end;
    }
    const std::string_view declared = message.substr(start, end - start);
    if (declared.size() < kMinEncodingLength || declared.size() > kMaxEncodingLength) {
        return std::unexpected(
            ParseError{ErrorCode::encoding_characters_invalid, start,
                       std::format("MSH-2 has {} characters, expected 4 or 5", declared.size())});
    }

    std::array<char, kMaxEncodingLength + 1> all{};
    all[0] = field;
    std::size_t count = 1;
    for (const char c : declared) {
        all.at(count++) = c;
    }
    for (std::size_t i = 0; i < count; ++i) {
        if (!is_valid_delimiter(all.at(i))) {
            return std::unexpected(ParseError{ErrorCode::encoding_characters_invalid,
                                              kMshPrefixLength + i,
                                              "delimiter is not printable non-alphanumeric ASCII"});
        }
        for (std::size_t j = 0; j < i; ++j) {
            if (all.at(i) == all.at(j)) {
                return std::unexpected(
                    ParseError{ErrorCode::encoding_characters_invalid, kMshPrefixLength + i,
                               "delimiters in MSH-1 and MSH-2 are not distinct"});
            }
        }
    }

    EncodingCharacters encoding{.field = field,
                                .component = declared[0],
                                .repetition = declared[1],
                                .escape = declared[2],
                                .subcomponent = declared[3],
                                .truncation = std::nullopt};
    if (declared.size() == kMaxEncodingLength) {
        encoding.truncation = declared[4];
    }
    return encoding;
}

bool has_escapes(std::string_view text, const EncodingCharacters& encoding) noexcept {
    return text.contains(encoding.escape);
}

std::expected<std::string, ParseError> decode_escapes(std::string_view text,
                                                      const EncodingCharacters& encoding,
                                                      std::size_t base_offset) {
    std::string out;
    out.reserve(text.size());
    std::size_t position = 0;
    while (position < text.size()) {
        const std::size_t open = text.find(encoding.escape, position);
        if (open == std::string_view::npos) {
            out.append(text.substr(position));
            break;
        }
        out.append(text.substr(position, open - position));
        const std::size_t close = text.find(encoding.escape, open + 1);
        const std::size_t offset = base_offset + open;
        if (close == std::string_view::npos) {
            return std::unexpected(escape_error(offset, "escape sequence is not terminated"));
        }
        const std::string_view body = text.substr(open + 1, close - open - 1);
        if (body.empty()) {
            return std::unexpected(escape_error(offset, "empty escape sequence"));
        }
        if (body.size() == 1) {
            switch (body[0]) {
                case 'F':
                    out.push_back(encoding.field);
                    break;
                case 'S':
                    out.push_back(encoding.component);
                    break;
                case 'T':
                    out.push_back(encoding.subcomponent);
                    break;
                case 'R':
                    out.push_back(encoding.repetition);
                    break;
                case 'E':
                    out.push_back(encoding.escape);
                    break;
                case 'P':
                    if (!encoding.truncation.has_value()) {
                        return std::unexpected(escape_error(
                            offset, "\\P\\ used without a declared truncation character"));
                    }
                    out.push_back(*encoding.truncation);
                    break;
                case 'H':
                case 'N':
                    break;
                default:
                    return std::unexpected(
                        escape_error(offset, std::format("unknown escape sequence \\{}\\", body)));
            }
        } else if (body[0] == 'X') {
            if (auto appended = append_hex(body.substr(1), offset, out); !appended) {
                return std::unexpected(std::move(appended.error()));
            }
        } else {
            return std::unexpected(
                escape_error(offset, std::format("unknown escape sequence \\{}\\", body)));
        }
        position = close + 1;
    }
    return out;
}

std::string encode_escapes(std::string_view text, const EncodingCharacters& encoding) {
    std::string out;
    out.reserve(text.size());
    const auto sequence = [&](char code) {
        out.push_back(encoding.escape);
        out.push_back(code);
        out.push_back(encoding.escape);
    };
    for (const char c : text) {
        if (c == encoding.escape) {
            sequence('E');
        } else if (c == encoding.field) {
            sequence('F');
        } else if (c == encoding.component) {
            sequence('S');
        } else if (c == encoding.subcomponent) {
            sequence('T');
        } else if (c == encoding.repetition) {
            sequence('R');
        } else if (encoding.truncation.has_value() && c == *encoding.truncation) {
            sequence('P');
        } else if (c == '\r') {
            out.append({encoding.escape, 'X', '0', 'D', encoding.escape});
        } else if (c == '\n') {
            out.append({encoding.escape, 'X', '0', 'A', encoding.escape});
        } else {
            out.push_back(c);
        }
    }
    return out;
}

}  // namespace wardwatch
