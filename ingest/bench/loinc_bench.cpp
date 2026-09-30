#include <benchmark/benchmark.h>

#include "wardwatch/loinc.hpp"

namespace {

void BM_FindLoinc(benchmark::State& state) {
    for (auto _ : state) {
        benchmark::DoNotOptimize(wardwatch::find_loinc("1975-2"));
    }
}
BENCHMARK(BM_FindLoinc);

}  // namespace
