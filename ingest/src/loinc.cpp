#include "wardwatch/loinc.hpp"

#include <algorithm>

namespace wardwatch {

const LoincCode* find_loinc(std::string_view code) noexcept {
    // A linear scan over a dozen entries beats a hash lookup at this size.
    const auto* found = std::ranges::find(kLoincCodes, code, &LoincCode::code);
    return found == kLoincCodes.end() ? nullptr : found;
}

}  // namespace wardwatch
