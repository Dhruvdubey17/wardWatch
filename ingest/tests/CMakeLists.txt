file(GLOB unit_test_sources CONFIGURE_DEPENDS unit/*.cpp)
add_executable(wardwatch_unit_tests ${unit_test_sources})
target_link_libraries(wardwatch_unit_tests PRIVATE wardwatch_core wardwatch_warnings GTest::gtest_main)
target_include_directories(wardwatch_unit_tests PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/support ${PROJECT_SOURCE_DIR}/fuzz)
target_compile_definitions(wardwatch_unit_tests PRIVATE
  WARDWATCH_CONTRACTS_DIR="${WARDWATCH_CONTRACTS_DIR}"
  WARDWATCH_FUZZ_DIR="${PROJECT_SOURCE_DIR}/fuzz")
gtest_discover_tests(wardwatch_unit_tests PROPERTIES LABELS unit DISCOVERY_TIMEOUT 60)

file(GLOB integration_test_sources CONFIGURE_DEPENDS integration/*.cpp)
add_executable(wardwatch_integration_tests ${integration_test_sources})
target_link_libraries(wardwatch_integration_tests PRIVATE wardwatch_core wardwatch_warnings GTest::gtest_main)
target_include_directories(wardwatch_integration_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/support)
target_compile_definitions(wardwatch_integration_tests PRIVATE
  WARDWATCH_CONTRACTS_DIR="${WARDWATCH_CONTRACTS_DIR}"
  WARDWATCH_INGEST_BINARY="$<TARGET_FILE:wardwatch-ingest>")
add_dependencies(wardwatch_integration_tests wardwatch-ingest)
gtest_discover_tests(wardwatch_integration_tests PROPERTIES LABELS integration DISCOVERY_TIMEOUT 60)

if(WARDWATCH_HAVE_KAFKA)
  add_executable(wardwatch_kafka_tests kafka/kafka_round_trip_test.cpp)
  target_link_libraries(wardwatch_kafka_tests PRIVATE
    wardwatch_core wardwatch_warnings wardwatch_rdkafka GTest::gtest_main)
  target_include_directories(wardwatch_kafka_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/support)
  target_compile_definitions(wardwatch_kafka_tests PRIVATE
    WARDWATCH_CONTRACTS_DIR="${WARDWATCH_CONTRACTS_DIR}"
    WARDWATCH_INGEST_BINARY="$<TARGET_FILE:wardwatch-ingest>")
  add_dependencies(wardwatch_kafka_tests wardwatch-ingest)
  # One label only: CMake 3.28's gtest_discover_tests keeps just the first value
  # of a list-valued LABELS, which left this test unlabelled as kafka on Ubuntu.
  gtest_discover_tests(wardwatch_kafka_tests PROPERTIES LABELS kafka DISCOVERY_TIMEOUT 60)
endif()
