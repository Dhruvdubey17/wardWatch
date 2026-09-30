#include "wardwatch/error.hpp"

#include <fstream>
#include <set>
#include <string>

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

namespace wardwatch {
namespace {

nlohmann::json load_catalog() {
    std::ifstream input(std::string(WARDWATCH_CONTRACTS_DIR) + "/fault_catalog.json");
    return nlohmann::json::parse(input);
}

TEST(ErrorCodes, MatchTheFaultCatalogExactly) {
    std::set<std::string> ours;
    for (std::size_t i = 0; i < kErrorCodeCount; ++i) {
        ours.emplace(to_string(static_cast<ErrorCode>(i)));
    }
    std::set<std::string> catalog;
    for (const auto& [code, description] : load_catalog().at("error_codes").items()) {
        catalog.insert(code);
    }
    EXPECT_EQ(ours, catalog);
    EXPECT_FALSE(ours.contains("UNKNOWN"));
}

TEST(WarningCodes, MatchTheFaultCatalogExactly) {
    std::set<std::string> ours;
    for (std::size_t i = 0; i < kWarningCodeCount; ++i) {
        ours.emplace(to_string(static_cast<WarningCode>(i)));
    }
    std::set<std::string> catalog;
    for (const auto& [code, description] : load_catalog().at("warning_codes").items()) {
        catalog.insert(code);
    }
    EXPECT_EQ(ours, catalog);
}

TEST(ErrorCodes, OutOfRangeValueIsUnknown) {
    EXPECT_EQ(to_string(static_cast<ErrorCode>(kErrorCodeCount)), "UNKNOWN");
    EXPECT_EQ(to_string(static_cast<WarningCode>(kWarningCodeCount)), "UNKNOWN");
}

}  // namespace
}  // namespace wardwatch
