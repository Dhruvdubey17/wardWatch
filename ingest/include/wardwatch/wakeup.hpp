#pragma once

#include <atomic>
#include <cstdint>

namespace wardwatch {

// Lets a consumer sleep while a lock-free queue is empty without making the
// producer pay for a wake-up call on every push. The consumer announces itself
// with prepare(), rechecks the queue, then waits on the epoch it saw. The
// producer bumps the epoch only when someone announced.
//
// Correctness rests on the store-buffering pattern with a seq_cst fence on
// each side: the producer stores to the queue, fences, then loads waiters_;
// the consumer increments waiters_, fences, then loads the queue. The fences
// forbid both loads from missing both stores, so either the producer sees the
// waiter and bumps the epoch, or the consumer sees the item.
class Wakeup {
  public:
    // Consumer: call before the final emptiness check. Returns the epoch to wait on.
    [[nodiscard]] std::uint32_t prepare() noexcept {
        waiters_.fetch_add(1, std::memory_order_seq_cst);
        std::atomic_thread_fence(std::memory_order_seq_cst);
        return epoch_.load(std::memory_order_seq_cst);
    }

    // Consumer: the recheck found work, so no wait is needed.
    void cancel() noexcept { waiters_.fetch_sub(1, std::memory_order_seq_cst); }

    // Consumer: sleeps until the epoch moves past `epoch`.
    void wait(std::uint32_t epoch) noexcept {
        epoch_.wait(epoch, std::memory_order_seq_cst);
        waiters_.fetch_sub(1, std::memory_order_seq_cst);
    }

    // Producer: call after publishing work.
    void notify() noexcept {
        std::atomic_thread_fence(std::memory_order_seq_cst);
        if (waiters_.load(std::memory_order_relaxed) > 0) {
            epoch_.fetch_add(1, std::memory_order_seq_cst);
            epoch_.notify_all();
        }
    }

  private:
    std::atomic<std::uint32_t> epoch_{0};
    std::atomic<std::uint32_t> waiters_{0};
};

}  // namespace wardwatch
