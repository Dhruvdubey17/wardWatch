#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "wardwatch/ack.hpp"
#include "wardwatch/encoding.hpp"
#include "wardwatch/message.hpp"
#include "wardwatch/mllp.hpp"
#include "wardwatch/payload.hpp"
#include "wardwatch/validator.hpp"

// Entry points shared by the libFuzzer targets and the regression unit test,
// so every committed crash input runs through exactly the code that found it.
// Each checks invariants and traps when one breaks, which the fuzzer reports
// as a crash.
namespace wardwatch::fuzz {

inline void check(bool condition) {
    if (!condition) {
        __builtin_trap();
    }
}

inline void run_parser(std::span<const std::uint8_t> data) {
    const std::string_view text(reinterpret_cast<const char*>(data.data()), data.size());
    const MessageSummary sniffed = sniff_header(text);
    static_cast<void>(sniffed);

    Validator validator;
    auto processed = validator.process(std::vector<char>(text.begin(), text.end()));
    check(processed.raw() == text);
    const auto now = std::chrono::system_clock::time_point{};
    const std::string ack = build_ack(processed.result.summary, processed.result.ack_code,
                                      processed.result.error, "F1", now);
    check(ack.starts_with("MSH|^~\\&|"));
    if (!processed.message) {
        check(processed.result.error.has_value());
        return;
    }
    const Message& message = *processed.message;
    for (const auto& segment : message.segments()) {
        check(segment.id.size() == 3);
        check(segment.offset < text.size());
        for (const auto field : segment.fields) {
            check(message.offset_of(field) + field.size() <= text.size());
        }
    }
    // The ACK must itself parse, whatever the input put into its fields.
    check(Message::parse(ack).has_value());
    if (processed.result.disposition == Disposition::accepted) {
        static_cast<void>(build_validated_payload(message, processed.result, now));
    }
}

inline void run_framer(std::span<const std::uint8_t> data) {
    if (data.empty()) {
        return;
    }
    // The first byte picks the chunk size, so the fuzzer also explores how a
    // stream is split across reads.
    const std::size_t chunk = (data[0] % 32) + 1;
    const auto body = data.subspan(1);
    constexpr std::size_t kMaxFrame = 256;
    MllpFramer framer(kMaxFrame, 64);
    std::size_t delivered_bytes = 0;
    for (std::size_t offset = 0; offset < body.size(); offset += chunk) {
        const auto piece = body.subspan(offset, std::min(chunk, body.size() - offset));
        framer.feed(
            std::span<const char>(reinterpret_cast<const char*>(piece.data()), piece.size()),
            [&](Frame&& frame) {
                check(frame.bytes.size() <= kMaxFrame);
                check(frame.oversize == (frame.total_size > kMaxFrame));
                check(frame.oversize || frame.bytes.size() == frame.total_size);
                delivered_bytes += frame.total_size;
            });
    }
    const auto& stats = framer.stats();
    check(stats.oversize_frames <= stats.frames);
    check(delivered_bytes + stats.bytes_outside_frames <= body.size());
}

inline void run_escapes(std::span<const std::uint8_t> data) {
    const std::string_view text(reinterpret_cast<const char*>(data.data()), data.size());
    EncodingCharacters encoding;
    encoding.truncation = '#';
    const auto decoded = decode_escapes(text, encoding);
    if (!decoded) {
        check(decoded.error().offset < text.size() + 1);
        return;
    }
    // Encoding the decoded text and decoding again must give it back.
    const auto again = decode_escapes(encode_escapes(*decoded, encoding), encoding);
    check(again.has_value() && *again == *decoded);
    // Arbitrary input must survive an encode and decode unchanged too.
    const auto round_trip = decode_escapes(encode_escapes(text, encoding), encoding);
    check(round_trip.has_value() && *round_trip == text);
}

}  // namespace wardwatch::fuzz
