#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include <gtest/gtest.h>

#include "fuzz_entry.hpp"

namespace wardwatch {
namespace {

std::vector<std::uint8_t> read_bytes(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

// Runs every committed seed and regression input through the same entry
// point its fuzz target uses. A crash input found by fuzzing is copied into
// fuzz/regressions/<target>/ together with its fix, and from then on runs here.
std::size_t run_directory(const std::string& target, void (*entry)(std::span<const std::uint8_t>)) {
    std::size_t count = 0;
    for (const char* kind : {"corpus", "regressions"}) {
        const std::filesystem::path directory =
            std::filesystem::path(WARDWATCH_FUZZ_DIR) / kind / target;
        if (!std::filesystem::exists(directory)) {
            continue;
        }
        for (const auto& entry_path : std::filesystem::directory_iterator(directory)) {
            if (entry_path.path().filename().string().starts_with(".")) {
                continue;
            }
            SCOPED_TRACE(entry_path.path().string());
            const auto bytes = read_bytes(entry_path.path());
            entry(bytes);
            ++count;
        }
    }
    return count;
}

TEST(FuzzRegression, Parser) { EXPECT_GE(run_directory("parser", fuzz::run_parser), 10U); }
TEST(FuzzRegression, Framer) { EXPECT_GE(run_directory("framer", fuzz::run_framer), 5U); }
TEST(FuzzRegression, Escapes) { EXPECT_GE(run_directory("escapes", fuzz::run_escapes), 5U); }

}  // namespace
}  // namespace wardwatch
