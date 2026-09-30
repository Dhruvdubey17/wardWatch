#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>
#include <utility>
#include <vector>

namespace wardwatch {

// MLLP block characters (HL7 v2.5.1 Appendix C, minimal lower layer protocol).
inline constexpr char kMllpStart = '\x0b';
inline constexpr char kMllpEnd = '\x1c';
inline constexpr char kMllpTrailer = '\r';

struct Frame {
    // The payload between the start and end blocks. For an oversize frame this
    // holds only the first bytes, kept for the dead letter and the control ID.
    std::vector<char> bytes;
    bool oversize = false;
    // Payload bytes seen, including any that were dropped from an oversize frame.
    std::size_t total_size = 0;
};

struct FramerStats {
    std::uint64_t frames = 0;
    std::uint64_t oversize_frames = 0;
    std::uint64_t bytes_outside_frames = 0;
    // A start block arrived before the previous frame ended; the partial frame
    // is dropped.
    std::uint64_t abandoned_frames = 0;
    // An end block was not followed by CR. The frame is still delivered.
    std::uint64_t missing_trailers = 0;
};

// Reassembles MLLP frames from a byte stream. Reads may split a frame at any
// byte, or carry several frames and stray bytes between them.
class MllpFramer {
  public:
    explicit MllpFramer(std::size_t max_frame_bytes, std::size_t oversize_keep_bytes = 4096)
        : max_frame_bytes_(max_frame_bytes),
          oversize_keep_bytes_(std::min(oversize_keep_bytes, max_frame_bytes)) {}

    // Calls on_frame(Frame&&) for every frame completed by this chunk.
    template <typename OnFrame>
    void feed(std::span<const char> data, OnFrame on_frame) {
        std::size_t position = 0;
        while (position < data.size()) {
            switch (state_) {
                case State::outside: {
                    const auto* begin = data.data() + position;
                    const auto* end = data.data() + data.size();
                    const auto* start = std::find(begin, end, kMllpStart);
                    stats_.bytes_outside_frames += static_cast<std::uint64_t>(start - begin);
                    position = static_cast<std::size_t>(start - data.data());
                    if (start != end) {
                        begin_frame();
                        ++position;
                    }
                    break;
                }
                case State::inside: {
                    const auto* begin = data.data() + position;
                    const auto* end = data.data() + data.size();
                    const auto* stop = std::find_if(
                        begin, end, [](char c) { return c == kMllpEnd || c == kMllpStart; });
                    append(std::span<const char>(begin, stop));
                    position = static_cast<std::size_t>(stop - data.data());
                    if (stop == end) {
                        break;
                    }
                    if (*stop == kMllpStart) {
                        ++stats_.abandoned_frames;
                        begin_frame();
                    } else {
                        state_ = State::after_end;
                    }
                    ++position;
                    break;
                }
                case State::after_end: {
                    // A missing CR is tolerated; the byte is then read as if
                    // outside a frame, so a following start block still works.
                    if (data[position] == kMllpTrailer) {
                        ++position;
                    } else {
                        ++stats_.missing_trailers;
                    }
                    finish_frame(on_frame);
                    state_ = State::outside;
                    break;
                }
            }
        }
    }

    [[nodiscard]] const FramerStats& stats() const noexcept { return stats_; }
    [[nodiscard]] bool in_frame() const noexcept { return state_ != State::outside; }

  private:
    enum class State : std::uint8_t { outside, inside, after_end };

    void begin_frame() {
        current_ = Frame{};
        state_ = State::inside;
    }

    void append(std::span<const char> bytes) {
        current_.total_size += bytes.size();
        if (current_.total_size > max_frame_bytes_) {
            current_.oversize = true;
        }
        const std::size_t limit = current_.oversize ? oversize_keep_bytes_ : max_frame_bytes_;
        const std::size_t room = limit > current_.bytes.size() ? limit - current_.bytes.size() : 0;
        const std::size_t kept = std::min(room, bytes.size());
        current_.bytes.insert(current_.bytes.end(), bytes.begin(),
                              bytes.begin() + static_cast<std::ptrdiff_t>(kept));
        if (current_.oversize && current_.bytes.size() > oversize_keep_bytes_) {
            current_.bytes.resize(oversize_keep_bytes_);
        }
    }

    template <typename OnFrame>
    void finish_frame(OnFrame& on_frame) {
        ++stats_.frames;
        if (current_.oversize) {
            ++stats_.oversize_frames;
        }
        on_frame(std::exchange(current_, Frame{}));
    }

    std::size_t max_frame_bytes_;
    std::size_t oversize_keep_bytes_;
    State state_ = State::outside;
    Frame current_;
    FramerStats stats_;
};

// Wraps a payload in MLLP start and end blocks.
[[nodiscard]] std::vector<char> mllp_wrap(std::span<const char> payload);

}  // namespace wardwatch
