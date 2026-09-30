#include <cstddef>
#include <cstdint>
#include <span>

#include "fuzz_entry.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    wardwatch::fuzz::run_escapes(std::span<const std::uint8_t>(data, size));
    return 0;
}
