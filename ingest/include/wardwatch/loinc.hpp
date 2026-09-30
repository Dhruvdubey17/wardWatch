#pragma once

#include <string_view>

#include "wardwatch/loinc_table.hpp"

namespace wardwatch {

// Returns the table entry for a LOINC code, or nullptr when the code is not
// one the pipeline carries.
[[nodiscard]] const LoincCode* find_loinc(std::string_view code) noexcept;

}  // namespace wardwatch
