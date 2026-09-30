#pragma once

#include <array>
#include <atomic>
#include <bit>
#include <cstddef>
#include <memory>
#include <new>
#include <optional>
#include <stdexcept>
#include <utility>
#include <vector>

namespace wardwatch {

// Apple silicon prefetches in 128-byte pairs, so 128 keeps the producer and
// consumer indices apart there as well as on 64-byte-line x86.
inline constexpr std::size_t kCacheLineSize = 128;

// A bounded single-producer single-consumer queue. Exactly one thread may call
// try_push and exactly one other thread may call try_pop.
//
// head_ and tail_ grow without bound and are masked into the slot array, so
// full and empty are told apart without a spare slot (tail - head == capacity
// means full). Each side caches the other side's index and only reloads it
// when the cached value says the ring is full or empty, which keeps the shared
// cache line from bouncing on every operation.
template <typename T>
class SpscRing {
  public:
    explicit SpscRing(std::size_t capacity)
        : capacity_(capacity), mask_(capacity - 1), slots_(capacity) {
        if (capacity == 0 || !std::has_single_bit(capacity)) {
            throw std::invalid_argument("SpscRing capacity must be a power of two");
        }
    }

    SpscRing(const SpscRing&) = delete;
    SpscRing& operator=(const SpscRing&) = delete;
    SpscRing(SpscRing&&) = delete;
    SpscRing& operator=(SpscRing&&) = delete;

    ~SpscRing() {
        while (try_pop().has_value()) {
        }
    }

    // Producer only. Returns false and leaves value untouched when full.
    [[nodiscard]] bool try_push(T&& value) {
        // Only this thread writes tail_, so it can read its own index relaxed.
        const std::size_t tail = tail_.load(std::memory_order_relaxed);
        if (tail - cached_head_ == capacity_) {
            // Acquire pairs with the consumer's release store of head_: once we
            // see the slot as free, the consumer's move-out of it has finished
            // and we may construct over it.
            cached_head_ = head_.load(std::memory_order_acquire);
            if (tail - cached_head_ == capacity_) {
                return false;
            }
        }
        std::construct_at(slot(tail), std::move(value));
        // Release publishes the constructed element before the new tail, so a
        // consumer that acquires this tail sees a fully built object.
        tail_.store(tail + 1, std::memory_order_release);
        return true;
    }

    // Consumer only. Returns nullopt when empty.
    [[nodiscard]] std::optional<T> try_pop() {
        // Only this thread writes head_, so relaxed is enough for its own index.
        const std::size_t head = head_.load(std::memory_order_relaxed);
        if (head == cached_tail_) {
            // Acquire pairs with the producer's release store of tail_, making
            // the element written before that store visible here.
            cached_tail_ = tail_.load(std::memory_order_acquire);
            if (head == cached_tail_) {
                return std::nullopt;
            }
        }
        T* element = slot(head);
        std::optional<T> value(std::move(*element));
        std::destroy_at(element);
        // Release orders the move-out and destruction before the producer can
        // observe the slot as free and reuse it.
        head_.store(head + 1, std::memory_order_release);
        return value;
    }

    // Safe from any thread, but only a snapshot: both indices may move while
    // it runs. Used for metrics, never for control flow. Relaxed is enough
    // because no data is read through the result.
    [[nodiscard]] std::size_t size_approx() const noexcept {
        const std::size_t head = head_.load(std::memory_order_relaxed);
        const std::size_t tail = tail_.load(std::memory_order_relaxed);
        return tail >= head ? tail - head : 0;
    }

    [[nodiscard]] std::size_t capacity() const noexcept { return capacity_; }

  private:
    struct Slot {
        alignas(T) std::array<std::byte, sizeof(T)> storage;
    };

    T* slot(std::size_t index) noexcept {
        return std::launder(reinterpret_cast<T*>(slots_[index & mask_].storage.data()));
    }

    const std::size_t capacity_;
    const std::size_t mask_;
    std::vector<Slot> slots_;

    // Written by the consumer, read by the producer.
    alignas(kCacheLineSize) std::atomic<std::size_t> head_{0};
    // The consumer's private copy of tail_, on the consumer's line.
    std::size_t cached_tail_ = 0;

    // Written by the producer, read by the consumer.
    alignas(kCacheLineSize) std::atomic<std::size_t> tail_{0};
    // The producer's private copy of head_, on the producer's line.
    std::size_t cached_head_ = 0;
};

}  // namespace wardwatch
