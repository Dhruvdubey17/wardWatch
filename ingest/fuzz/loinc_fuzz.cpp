#include <cstddef>
#include <cstdint>
#include <string_view>

#include "wardwatch/loinc.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    const std::string_view code(reinterpret_cast<const char*>(data), size);
    const auto* entry = wardwatch::find_loinc(code);
    if (entry != nullptr && entry->code != code) {
        __builtin_trap();
    }
    return 0;
}
