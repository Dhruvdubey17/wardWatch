#include "wardwatch/spsc_ring.hpp"

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <thread>

#include <gtest/gtest.h>

namespace wardwatch {
namespace {

TEST(SpscRing, RejectsCapacityThatIsNotAPowerOfTwo) {
    EXPECT_THROW(SpscRing<int>(0), std::invalid_argument);
    EXPECT_THROW(SpscRing<int>(3), std::invalid_argument);
    EXPECT_THROW(SpscRing<int>(100), std::invalid_argument);
    EXPECT_NO_THROW(SpscRing<int>(1));
    EXPECT_NO_THROW(SpscRing<int>(1024));
}

TEST(SpscRing, EmptyRingPopsNothing) {
    SpscRing<int> ring(4);
    EXPECT_FALSE(ring.try_pop().has_value());
    EXPECT_EQ(ring.size_approx(), 0U);
    EXPECT_EQ(ring.capacity(), 4U);
}

TEST(SpscRing, FullRingRejectsPushAndKeepsValue) {
    SpscRing<std::unique_ptr<int>> ring(2);
    EXPECT_TRUE(ring.try_push(std::make_unique<int>(1)));
    EXPECT_TRUE(ring.try_push(std::make_unique<int>(2)));
    auto third = std::make_unique<int>(3);
    EXPECT_FALSE(ring.try_push(std::move(third)));
    ASSERT_NE(third, nullptr);  // NOLINT(bugprone-use-after-move): a failed push must not consume
    EXPECT_EQ(*third, 3);
    EXPECT_EQ(ring.size_approx(), 2U);
    EXPECT_EQ(**ring.try_pop(), 1);
    EXPECT_TRUE(ring.try_push(std::move(third)));
}

TEST(SpscRing, PreservesOrderAcrossWraparound) {
    SpscRing<int> ring(4);
    int next_in = 0;
    int next_out = 0;
    for (int round = 0; round < 50; ++round) {
        for (int i = 0; i < 3; ++i) {
            ASSERT_TRUE(ring.try_push(int{next_in++}));
        }
        for (int i = 0; i < 3; ++i) {
            const auto value = ring.try_pop();
            ASSERT_TRUE(value.has_value());
            EXPECT_EQ(*value, next_out++);
        }
    }
    EXPECT_FALSE(ring.try_pop().has_value());
}

TEST(SpscRing, CapacityOneAlternates) {
    SpscRing<int> ring(1);
    EXPECT_TRUE(ring.try_push(7));
    EXPECT_FALSE(ring.try_push(8));
    EXPECT_EQ(*ring.try_pop(), 7);
    EXPECT_TRUE(ring.try_push(8));
    EXPECT_EQ(*ring.try_pop(), 8);
}

struct Counted {
    static inline int live = 0;
    Counted() { ++live; }
    Counted(Counted&& /*other*/) noexcept { ++live; }
    Counted(const Counted&) = delete;
    Counted& operator=(const Counted&) = delete;
    Counted& operator=(Counted&&) = delete;
    ~Counted() { --live; }
};

TEST(SpscRing, DestroysElementsLeftInRing) {
    Counted::live = 0;
    {
        SpscRing<Counted> ring(8);
        for (int i = 0; i < 5; ++i) {
            ASSERT_TRUE(ring.try_push(Counted{}));
        }
        EXPECT_EQ(Counted::live, 5);
        {
            auto popped = ring.try_pop();
        }
        EXPECT_EQ(Counted::live, 4);
    }
    EXPECT_EQ(Counted::live, 0);
}

// Runs under the tsan preset. One producer and one consumer move millions of
// items through a small ring so it wraps and fills constantly; the consumer
// checks that every value arrives exactly once and in order.
TEST(SpscRing, StressOneProducerOneConsumer) {
    constexpr std::uint64_t kItems = 2'000'000;
    SpscRing<std::uint64_t> ring(64);
    std::uint64_t full_count = 0;

    std::thread producer([&] {
        for (std::uint64_t value = 0; value < kItems;) {
            if (ring.try_push(std::uint64_t{value})) {
                ++value;
            } else {
                ++full_count;
                std::this_thread::yield();
            }
        }
    });

    std::uint64_t expected = 0;
    std::uint64_t sum = 0;
    bool in_order = true;
    while (expected < kItems) {
        if (const auto value = ring.try_pop()) {
            in_order = in_order && *value == expected;
            sum += *value;
            ++expected;
        }
    }
    producer.join();

    EXPECT_TRUE(in_order);
    EXPECT_EQ(expected, kItems);
    EXPECT_EQ(sum, kItems * (kItems - 1) / 2);
    EXPECT_FALSE(ring.try_pop().has_value());
    // The ring is far smaller than the item count, so the producer must have
    // hit the full path; otherwise the test never exercised it.
    EXPECT_GT(full_count, 0U);
}

TEST(SpscRing, StressWithOwningElements) {
    constexpr int kItems = 200'000;
    SpscRing<std::unique_ptr<int>> ring(16);
    std::thread producer([&] {
        for (int value = 0; value < kItems;) {
            auto element = std::make_unique<int>(value);
            while (!ring.try_push(std::move(element))) {
                std::this_thread::yield();
            }
            ++value;
        }
    });
    int expected = 0;
    bool in_order = true;
    while (expected < kItems) {
        if (auto element = ring.try_pop()) {
            in_order = in_order && **element == expected;
            ++expected;
        }
    }
    producer.join();
    EXPECT_TRUE(in_order);
}

}  // namespace
}  // namespace wardwatch
