#pragma once

#include <chrono>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "wardwatch/sink.hpp"

namespace wardwatch::testing {

struct Record {
    Topic topic;
    std::string key;
    std::string payload;
};

// Keeps every record in memory. An optional delay per publish simulates a
// slow broker so tests can drive the server into backpressure.
class MemorySink final : public Sink {
  public:
    explicit MemorySink(std::chrono::microseconds delay = {}) : delay_(delay) {}

    bool publish(Topic topic, std::string_view key, std::string_view payload) override {
        if (delay_.count() > 0) {
            std::this_thread::sleep_for(delay_);
        }
        const std::scoped_lock lock(mutex_);
        records_.push_back(Record{topic, std::string(key), std::string(payload)});
        return true;
    }

    bool flush(std::chrono::milliseconds /*timeout*/) override {
        const std::scoped_lock lock(mutex_);
        ++flushes_;
        return true;
    }

    std::vector<Record> records() const {
        const std::scoped_lock lock(mutex_);
        return records_;
    }

    int flushes() const {
        const std::scoped_lock lock(mutex_);
        return flushes_;
    }

  private:
    std::chrono::microseconds delay_;
    mutable std::mutex mutex_;
    std::vector<Record> records_;
    int flushes_ = 0;
};

}  // namespace wardwatch::testing
