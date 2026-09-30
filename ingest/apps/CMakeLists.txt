add_executable(wardwatch-ingest main.cpp)
target_link_libraries(wardwatch-ingest PRIVATE wardwatch_core wardwatch_warnings)
