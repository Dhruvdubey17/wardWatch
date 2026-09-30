#include <atomic>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <thread>
#include <vector>

#include <benchmark/benchmark.h>

#include "wardwatch/message.hpp"
#include "wardwatch/mllp.hpp"
#include "wardwatch/payload.hpp"
#include "wardwatch/spsc_ring.hpp"
#include "wardwatch/validator.hpp"

namespace {

std::string read_sample() {
    std::ifstream input(std::string(WARDWATCH_CONTRACTS_DIR) + "/hl7/oru_r01.hl7",
                        std::ios::binary);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

const std::string& sample() {
    static const std::string text = read_sample();
    return text;
}

void set_throughput(benchmark::State& state) {
    state.SetItemsProcessed(state.iterations());
    state.SetBytesProcessed(state.iterations() * static_cast<std::int64_t>(sample().size()));
}

// Tokenizing one hourly ORU^R01 with 13 OBX segments, including the copy of
// the frame bytes into the message buffer that the server also makes.
void BM_ParseOru(benchmark::State& state) {
    const std::string& text = sample();
    for (auto _ : state) {
        std::vector<char> bytes(text.begin(), text.end());
        auto message = wardwatch::Message::parse(bytes);
        benchmark::DoNotOptimize(message);
    }
    set_throughput(state);
}
BENCHMARK(BM_ParseOru);

// Parse plus every validation rule. The duplicate window is zero so each
// iteration takes the accept path instead of the retransmission path.
void BM_ParseValidateOru(benchmark::State& state) {
    const std::string& text = sample();
    wardwatch::Validator validator(0);
    for (auto _ : state) {
        auto processed = validator.process(std::vector<char>(text.begin(), text.end()));
        benchmark::DoNotOptimize(processed);
    }
    set_throughput(state);
}
BENCHMARK(BM_ParseValidateOru);

// Everything the validation thread does before handing a record to the sink.
void BM_ParseValidateSerializeOru(benchmark::State& state) {
    const std::string& text = sample();
    wardwatch::Validator validator(0);
    const auto now = std::chrono::system_clock::now();
    for (auto _ : state) {
        auto processed = validator.process(std::vector<char>(text.begin(), text.end()));
        auto payload =
            wardwatch::build_validated_payload(*processed.message, processed.result, now);
        benchmark::DoNotOptimize(payload);
    }
    set_throughput(state);
}
BENCHMARK(BM_ParseValidateSerializeOru);

void BM_FrameStream(benchmark::State& state) {
    constexpr int kFramesPerChunk = 64;
    std::string stream;
    for (int i = 0; i < kFramesPerChunk; ++i) {
        const auto framed =
            wardwatch::mllp_wrap(std::span<const char>(sample().data(), sample().size()));
        stream.append(framed.begin(), framed.end());
    }
    for (auto _ : state) {
        wardwatch::MllpFramer framer(1U << 20U);
        std::size_t frames = 0;
        framer.feed(std::span<const char>(stream.data(), stream.size()),
                    [&](wardwatch::Frame&& frame) {
                        benchmark::DoNotOptimize(frame);
                        ++frames;
                    });
        benchmark::DoNotOptimize(frames);
    }
    state.SetItemsProcessed(state.iterations() * kFramesPerChunk);
    state.SetBytesProcessed(state.iterations() * static_cast<std::int64_t>(stream.size()));
}
BENCHMARK(BM_FrameStream);

// Items per second through the ring between two threads, at the capacity the
// server uses by default.
void BM_RingThroughput(benchmark::State& state) {
    constexpr std::uint64_t kItems = 1'000'000;
    for (auto _ : state) {
        wardwatch::SpscRing<std::uint64_t> ring(static_cast<std::size_t>(state.range(0)));
        std::thread producer([&] {
            for (std::uint64_t value = 0; value < kItems;) {
                if (ring.try_push(std::uint64_t{value})) {
                    ++value;
                }
            }
        });
        std::uint64_t received = 0;
        while (received < kItems) {
            if (auto value = ring.try_pop()) {
                benchmark::DoNotOptimize(*value);
                ++received;
            }
        }
        producer.join();
    }
    state.SetItemsProcessed(state.iterations() * static_cast<std::int64_t>(kItems));
}
BENCHMARK(BM_RingThroughput)->Arg(4096)->Arg(64)->UseRealTime()->Unit(benchmark::kMillisecond);

}  // namespace
