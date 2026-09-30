#pragma once

#include <cstddef>
#include <expected>
#include <optional>
#include <string>
#include <string_view>

#include "wardwatch/error.hpp"

namespace wardwatch {

// Delimiters declared by MSH-1 and MSH-2 (HL7 v2.5.1 section 2.5.4). The
// truncation character is the optional fifth MSH-2 character added in v2.7.
struct EncodingCharacters {
    char field = '|';
    char component = '^';
    char repetition = '~';
    char escape = '\\';
    char subcomponent = '&';
    std::optional<char> truncation;

    [[nodiscard]] bool is_delimiter(char c) const noexcept;
};

// Reads the encoding characters from the start of a message, which must begin
// with "MSH". The delimiters must be distinct printable non-alphanumeric ASCII.
[[nodiscard]] std::expected<EncodingCharacters, ParseError> read_encoding_characters(
    std::string_view message);

[[nodiscard]] bool has_escapes(std::string_view text, const EncodingCharacters& encoding) noexcept;

// Decodes \F\ \S\ \T\ \R\ \E\ (and \P\ when a truncation character is
// declared) to delimiter characters and \Xhh..\ to raw bytes. The formatting
// sequences \H\ and \N\ carry no text and are dropped. Any other sequence is an
// error. base_offset is added to error offsets so they point into the message.
[[nodiscard]] std::expected<std::string, ParseError> decode_escapes(
    std::string_view text, const EncodingCharacters& encoding, std::size_t base_offset = 0);

// Escapes delimiters, and CR and LF as hex, so arbitrary text fits in a field.
[[nodiscard]] std::string encode_escapes(std::string_view text, const EncodingCharacters& encoding);

}  // namespace wardwatch
