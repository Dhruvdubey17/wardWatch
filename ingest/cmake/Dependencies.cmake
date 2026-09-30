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
  message(STATUS "librdkafka ${RDKAFKA_VERSION} found on the system")
  add_library(wardwatch_rdkafka INTERFACE)
  target_link_libraries(wardwatch_rdkafka INTERFACE PkgConfig::RDKAFKA)
  set(WARDWATCH_HAVE_KAFKA ON)
elseif(WARDWATCH_FETCH_RDKAFKA)
  # A plain-text client with no compression or TLS is enough for a broker on
  # the local Docker network, and keeps the source build free of system deps.
  message(STATUS "librdkafka not on the system, building v2.6.1 from source")
  FetchContent_Declare(
    librdkafka
    URL https://github.com/confluentinc/librdkafka/archive/refs/tags/v2.6.1.tar.gz
    URL_HASH SHA256=0ddf205ad8d36af0bc72a2fec20639ea02e1d583e353163bf7f4683d949e901b
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    EXCLUDE_FROM_ALL SYSTEM)
  set(RDKAFKA_BUILD_STATIC ON CACHE INTERNAL "")
  set(RDKAFKA_BUILD_EXAMPLES OFF CACHE INTERNAL "")
  set(RDKAFKA_BUILD_TESTS OFF CACHE INTERNAL "")
  foreach(feature WITH_SSL WITH_SASL WITH_ZSTD WITH_ZLIB WITH_CURL WITH_PLUGINS ENABLE_LZ4_EXT)
    set(${feature} OFF CACHE INTERNAL "")
  endforeach()
  FetchContent_MakeAvailable(librdkafka)
  # Installed librdkafka puts the header under librdkafka/; the source tree
  # does not, so copy it into the same layout.
  configure_file("${librdkafka_SOURCE_DIR}/src/rdkafka.h"
    "${CMAKE_BINARY_DIR}/rdkafka-include/librdkafka/rdkafka.h" COPYONLY)
  add_library(wardwatch_rdkafka INTERFACE)
  target_link_libraries(wardwatch_rdkafka INTERFACE rdkafka)
  target_include_directories(wardwatch_rdkafka SYSTEM INTERFACE "${CMAKE_BINARY_DIR}/rdkafka-include")
  set(WARDWATCH_HAVE_KAFKA ON)
else()
  message(STATUS "librdkafka not found, building without the Kafka sink")
  set(WARDWATCH_HAVE_KAFKA OFF)
endif()
