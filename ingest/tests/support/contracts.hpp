#pragma once

#include <fstream>
#include <iterator>
#include <string>
#include <string_view>

#include <nlohmann/json.hpp>

namespace wardwatch::testing {

inline std::string contract_path(std::string_view relative) {
    return std::string(WARDWATCH_CONTRACTS_DIR) + "/" + std::string(relative);
}

inline std::string read_contract(std::string_view relative) {
    std::ifstream input(contract_path(relative), std::ios::binary);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

inline nlohmann::json read_contract_json(std::string_view relative) {
    return nlohmann::json::parse(read_contract(relative));
}

}  // namespace wardwatch::testing
