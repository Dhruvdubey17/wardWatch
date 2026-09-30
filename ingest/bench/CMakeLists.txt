add_executable(wardwatch_bench main.cpp pipeline_bench.cpp)
target_link_libraries(wardwatch_bench PRIVATE wardwatch_core wardwatch_warnings benchmark::benchmark)
target_compile_definitions(wardwatch_bench PRIVATE
  WARDWATCH_CONTRACTS_DIR="${WARDWATCH_CONTRACTS_DIR}"
  WARDWATCH_BUILD_TYPE="${CMAKE_BUILD_TYPE}")
