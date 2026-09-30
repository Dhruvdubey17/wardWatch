#include "wardwatch/loinc.hpp"

#include <fstream>

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

namespace wardwatch {
namespace {

TEST(Loinc, GeneratedTableMatchesContract) {
    std::ifstream input(std::string(WARDWATCH_CONTRACTS_DIR) + "/loinc_codes.json");
    const auto contract = nlohmann::json::parse(input);
    ASSERT_EQ(contract.at("codes").size(), kLoincCodes.size());
    for (const auto& entry : contract.at("codes")) {
        const auto* code = find_loinc(entry.at("code").get<std::string>());
        ASSERT_NE(code, nullptr) << entry.at("code");
        EXPECT_EQ(code->display, entry.at("display").get<std::string>());
        EXPECT_EQ(code->ucum_unit, entry.at("ucum_unit").get<std::string>());
        EXPECT_DOUBLE_EQ(code->plausible_min, entry.at("plausible_min").get<double>());
        EXPECT_DOUBLE_EQ(code->plausible_max, entry.at("plausible_max").get<double>());
    }
}

TEST(Loinc, UnknownCodeIsNotFound) {
    EXPECT_EQ(find_loinc("0000-0"), nullptr);
    EXPECT_EQ(find_loinc(""), nullptr);
    EXPECT_NE(find_loinc("8867-4"), nullptr);
}

}  // namespace
}  // namespace wardwatch
