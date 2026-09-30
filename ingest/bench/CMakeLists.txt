file(GLOB bench_sources CONFIGURE_DEPENDS *.cpp)
add_executable(wardwatch_bench ${bench_sources})
target_link_libraries(wardwatch_bench PRIVATE wardwatch_core wardwatch_warnings benchmark::benchmark_main)
