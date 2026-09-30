#include "wardwatch/wakeup.hpp"

#include <atomic>
#include <cstdint>
#include <thread>

#include <gtest/gtest.h>

#include "wardwatch/spsc_ring.hpp"

namespace wardwatch {
namespace {

TEST(Wakeup, NotifyWithoutWaitersIsANoOp) {
    Wakeup wakeup;
    wakeup.notify();
    const auto epoch = wakeup.prepare();
    wakeup.cancel();
    EXPECT_EQ(wakeup.prepare(), epoch);
    wakeup.cancel();
}

TEST(Wakeup, NotifyAdvancesEpochWhenSomeoneWaits) {
    Wakeup wakeup;
    const auto epoch = wakeup.prepare();
    wakeup.notify();
    wakeup.wait(epoch);  // returns at once because the epoch moved
    EXPECT_NE(wakeup.prepare(), epoch);
    wakeup.cancel();
}

// A consumer that sleeps whenever the ring is empty must still receive every
// item: a lost wake-up would hang this test. Runs under the tsan preset.
TEST(Wakeup, ConsumerNeverMissesAnItem) {
    constexpr std::uint64_t kItems = 200'000;
    SpscRing<std::uint64_t> ring(8);
    Wakeup wakeup;
    std::atomic<bool> done{false};

    std::thread producer([&] {
        for (std::uint64_t value = 0; value < kItems; ++value) {
            while (!ring.try_push(std::uint64_t{value})) {
                std::this_thread::yield();
            }
            wakeup.notify();
            if (value % 1000 == 0) {
                // Pauses give the consumer time to fall asleep, so the sleep
                // path is exercised and not only the busy path.
                std::this_thread::sleep_for(std::chrono::microseconds(50));
            }
        }
        done.store(true, std::memory_order_release);
        wakeup.notify();
    });

    std::uint64_t received = 0;
    bool in_order = true;
    while (true) {
        const auto epoch = wakeup.prepare();
        if (const auto value = ring.try_pop()) {
            wakeup.cancel();
            in_order = in_order && *value == received;
            ++received;
            continue;
        }
        if (done.load(std::memory_order_acquire)) {
            wakeup.cancel();
            if (const auto value = ring.try_pop()) {
                in_order = in_order && *value == received;
                ++received;
                continue;
            }
            break;
        }
        wakeup.wait(epoch);
    }
    producer.join();
    EXPECT_EQ(received, kItems);
    EXPECT_TRUE(in_order);
}

}  // namespace
}  // namespace wardwatch
