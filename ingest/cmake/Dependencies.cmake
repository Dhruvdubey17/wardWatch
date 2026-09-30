include(FetchContent)

# Every dependency is pinned by URL and SHA-256 so builds are reproducible.
FetchContent_Declare(
  googletest
  URL https://github.com/google/googletest/releases/download/v1.15.2/googletest-1.15.2.tar.gz
  URL_HASH SHA256=7b42b4d6ed48810c5362c265a17faebe90dc2373c885e5216439d37927f02926
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  EXCLUDE_FROM_ALL SYSTEM)
FetchContent_Declare(
  nlohmann_json
  URL https://github.com/nlohmann/json/releases/download/v3.12.0/json.tar.xz
  URL_HASH SHA256=42f6e95cad6ec532fd372391373363b62a14af6d771056dbfc86160e6dfff7aa
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  EXCLUDE_FROM_ALL SYSTEM)

set(JSON_BuildTests OFF CACHE INTERNAL "")
FetchContent_MakeAvailable(nlohmann_json)

if(WARDWATCH_BUILD_TESTS)
  set(INSTALL_GTEST OFF CACHE INTERNAL "")
  set(BUILD_GMOCK OFF CACHE INTERNAL "")
  FetchContent_MakeAvailable(googletest)
  include(GoogleTest)
endif()

if(WARDWATCH_BUILD_BENCH)
  FetchContent_Declare(
    benchmark
    URL https://github.com/google/benchmark/archive/refs/tags/v1.9.1.tar.gz
    URL_HASH SHA256=32131c08ee31eeff2c8968d7e874f3cb648034377dfc32a4c377fa8796d84981
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    EXCLUDE_FROM_ALL SYSTEM)
  set(BENCHMARK_ENABLE_TESTING OFF CACHE INTERNAL "")
  set(BENCHMARK_ENABLE_INSTALL OFF CACHE INTERNAL "")
  set(BENCHMARK_ENABLE_WERROR OFF CACHE INTERNAL "")
  FetchContent_MakeAvailable(benchmark)
endif()

# librdkafka comes from the system. Without it the Kafka sink is left out and
# the server offers only the file and null sinks.
find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
  pkg_check_modules(RDKAFKA QUIET IMPORTED_TARGET rdkafka)
endif()
if(RDKAFKA_FOUND)
  message(STATUS "librdkafka ${RDKAFKA_VERSION} found, building the Kafka sink")
else()
  message(STATUS "librdkafka not found, building without the Kafka sink")
endif()
