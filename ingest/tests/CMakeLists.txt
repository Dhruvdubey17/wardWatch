file(GLOB unit_test_sources CONFIGURE_DEPENDS unit/*.cpp)
add_executable(wardwatch_unit_tests ${unit_test_sources})
target_link_libraries(wardwatch_unit_tests PRIVATE wardwatch_core wardwatch_warnings GTest::gtest_main)
target_compile_definitions(wardwatch_unit_tests PRIVATE
  WARDWATCH_CONTRACTS_DIR="${WARDWATCH_CONTRACTS_DIR}")
gtest_discover_tests(wardwatch_unit_tests PROPERTIES LABELS unit DISCOVERY_TIMEOUT 60)
