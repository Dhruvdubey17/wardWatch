cmake_minimum_required(VERSION 3.28)
project(wardwatch_ingest LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# CMake 3.28+ scans C++20 and later sources for modules, which needs
# clang-scan-deps. Ubuntu's clang package does not ship it, and nothing here
# uses modules.
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)
set(CMAKE_CXX_EXTENSIONS OFF)

option(WARDWATCH_BUILD_TESTS "Build unit and integration tests" ON)
option(WARDWATCH_BUILD_BENCH "Build benchmarks" OFF)
option(WARDWATCH_FUZZ "Build libFuzzer targets" OFF)
option(WARDWATCH_COVERAGE "Instrument for source-based coverage" OFF)
option(WARDWATCH_FETCH_RDKAFKA "Build librdkafka from source when the system has none" ON)
set(WARDWATCH_SANITIZERS "" CACHE STRING "Sanitizers to enable, for example address;undefined or thread")

get_filename_component(WARDWATCH_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
set(WARDWATCH_CONTRACTS_DIR "${WARDWATCH_REPO_ROOT}/contracts")

include(cmake/Dependencies.cmake)
include(cmake/Toolchain.cmake)
include(cmake/LoincTable.cmake)

add_subdirectory(src)
add_subdirectory(apps)

if(WARDWATCH_BUILD_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
if(WARDWATCH_BUILD_BENCH)
  add_subdirectory(bench)
endif()
if(WARDWATCH_FUZZ)
  add_subdirectory(fuzz)
endif()
