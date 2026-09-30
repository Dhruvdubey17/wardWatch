#include "wardwatch/mllp.hpp"

#include <random>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include <gtest/gtest.h>

namespace wardwatch {
namespace {

std::string wrap(std::string_view payload) {
    const auto framed = mllp_wrap(std::span<const char>(payload.data(), payload.size()));
    return {framed.begin(), framed.end()};
}

std::string text(const Frame& frame) { return {frame.bytes.begin(), frame.bytes.end()}; }

struct Collector {
    std::vector<Frame> frames;
    void feed(MllpFramer& framer, std::string_view chunk) {
        framer.feed(std::span<const char>(chunk.data(), chunk.size()),
                    [this](Frame&& frame) { frames.push_back(std::move(frame)); });
    }
};

TEST(MllpFramer, SingleFrameInOneRead) {
    MllpFramer framer(1024);
    Collector out;
    out.feed(framer, wrap("MSH|^~\\&|A\r"));
    ASSERT_EQ(out.frames.size(), 1U);
    EXPECT_EQ(text(out.frames[0]), "MSH|^~\\&|A\r");
    EXPECT_FALSE(out.frames[0].oversize);
    EXPECT_EQ(out.frames[0].total_size, 11U);
    EXPECT_FALSE(framer.in_frame());
    EXPECT_EQ(framer.stats().frames, 1U);
}

TEST(MllpFramer, FrameSplitAcrossReads) {
    MllpFramer framer(1024);
    Collector out;
    const std::string stream = wrap("MSH|^~\\&|SPLIT\r");
    out.feed(framer, stream.substr(0, 5));
    EXPECT_TRUE(out.frames.empty());
    EXPECT_TRUE(framer.in_frame());
    out.feed(framer, stream.substr(5, stream.size() - 6));
    EXPECT_TRUE(out.frames.empty());
    out.feed(framer, stream.substr(stream.size() - 1));
    ASSERT_EQ(out.frames.size(), 1U);
    EXPECT_EQ(text(out.frames[0]), "MSH|^~\\&|SPLIT\r");
}

TEST(MllpFramer, ByteAtATime) {
    MllpFramer framer(1024);
    Collector out;
    const std::string stream = wrap("first") + wrap("second");
    for (const char c : stream) {
        out.feed(framer, std::string_view(&c, 1));
    }
    ASSERT_EQ(out.frames.size(), 2U);
    EXPECT_EQ(text(out.frames[0]), "first");
    EXPECT_EQ(text(out.frames[1]), "second");
}

TEST(MllpFramer, SeveralFramesInOneRead) {
    MllpFramer framer(1024);
    Collector out;
    out.feed(framer, wrap("a") + wrap("bb") + wrap("ccc"));
    ASSERT_EQ(out.frames.size(), 3U);
    EXPECT_EQ(text(out.frames[2]), "ccc");
    EXPECT_EQ(framer.stats().frames, 3U);
}

TEST(MllpFramer, GarbageBetweenFramesIsCountedAndDropped) {
    MllpFramer framer(1024);
    Collector out;
    out.feed(framer, "noise" + wrap("a") + "\r\n" + wrap("b") + "tail");
    ASSERT_EQ(out.frames.size(), 2U);
    EXPECT_EQ(text(out.frames[0]), "a");
    EXPECT_EQ(text(out.frames[1]), "b");
    EXPECT_EQ(framer.stats().bytes_outside_frames, 5U + 2U + 4U);
}

TEST(MllpFramer, EmptyFrameIsDelivered) {
    MllpFramer framer(1024);
    Collector out;
    out.feed(framer, wrap(""));
    ASSERT_EQ(out.frames.size(), 1U);
    EXPECT_TRUE(out.frames[0].bytes.empty());
}

TEST(MllpFramer, OversizeFrameKeepsPrefixAndReportsTotal) {
    MllpFramer framer(10, 4);
    Collector out;
    out.feed(framer, wrap("0123456789ABCDEF") + wrap("ok"));
    ASSERT_EQ(out.frames.size(), 2U);
    EXPECT_TRUE(out.frames[0].oversize);
    EXPECT_EQ(text(out.frames[0]), "0123");
    EXPECT_EQ(out.frames[0].total_size, 16U);
    EXPECT_FALSE(out.frames[1].oversize);
    EXPECT_EQ(text(out.frames[1]), "ok");
    EXPECT_EQ(framer.stats().oversize_frames, 1U);
}

TEST(MllpFramer, OversizeDetectedAcrossReads) {
    MllpFramer framer(8, 3);
    Collector out;
    out.feed(framer,
             "\x0b"
             "12345");
    out.feed(framer, "6789");
    out.feed(framer, "abc\x1c\r");
    ASSERT_EQ(out.frames.size(), 1U);
    EXPECT_TRUE(out.frames[0].oversize);
    EXPECT_EQ(text(out.frames[0]), "123");
    EXPECT_EQ(out.frames[0].total_size, 12U);
}

TEST(MllpFramer, FrameExactlyAtLimitIsNotOversize) {
    MllpFramer framer(4);
    Collector out;
    out.feed(framer, wrap("1234"));
    ASSERT_EQ(out.frames.size(), 1U);
    EXPECT_FALSE(out.frames[0].oversize);
    EXPECT_EQ(text(out.frames[0]), "1234");
}

TEST(MllpFramer, StartBlockInsideFrameAbandonsPartialFrame) {
    MllpFramer framer(1024);
    Collector out;
    out.feed(framer, "\x0bpartial" + wrap("complete"));
    ASSERT_EQ(out.frames.size(), 1U);
    EXPECT_EQ(text(out.frames[0]), "complete");
    EXPECT_EQ(framer.stats().abandoned_frames, 1U);
}

TEST(MllpFramer, MissingTrailerStillDeliversFrame) {
    MllpFramer framer(1024);
    Collector out;
    out.feed(framer,
             "\x0b"
             "a\x1c"
             "\x0b"
             "b\x1c\r");
    ASSERT_EQ(out.frames.size(), 2U);
    EXPECT_EQ(text(out.frames[0]), "a");
    EXPECT_EQ(text(out.frames[1]), "b");
    EXPECT_EQ(framer.stats().missing_trailers, 1U);
    EXPECT_EQ(framer.stats().bytes_outside_frames, 0U);
}

TEST(MllpFramer, EndBlockSplitFromTrailer) {
    MllpFramer framer(1024);
    Collector out;
    out.feed(framer,
             "\x0b"
             "abc\x1c");
    EXPECT_TRUE(out.frames.empty());
    EXPECT_TRUE(framer.in_frame());
    out.feed(framer, "\r");
    ASSERT_EQ(out.frames.size(), 1U);
    EXPECT_EQ(framer.stats().missing_trailers, 0U);
}

TEST(MllpFramer, RandomSplitsReassembleTheSameFrames) {
    std::string stream;
    std::vector<std::string> payloads;
    for (int i = 0; i < 200; ++i) {
        payloads.push_back("MSH|^~\\&|APP|" + std::to_string(i) +
                           std::string(static_cast<std::size_t>(i % 37), 'x'));
        stream += wrap(payloads.back());
    }
    std::mt19937 random(12345);
    for (int trial = 0; trial < 20; ++trial) {
        MllpFramer framer(4096);
        Collector out;
        std::size_t position = 0;
        while (position < stream.size()) {
            const std::size_t length = std::uniform_int_distribution<std::size_t>(1, 64)(random);
            out.feed(framer, std::string_view(stream).substr(position, length));
            position += length;
        }
        ASSERT_EQ(out.frames.size(), payloads.size());
        for (std::size_t i = 0; i < payloads.size(); ++i) {
            EXPECT_EQ(text(out.frames[i]), payloads[i]);
        }
    }
}

TEST(MllpWrap, AddsBlocks) { EXPECT_EQ(wrap("x"), std::string("\x0bx\x1c\r")); }

}  // namespace
}  // namespace wardwatch
