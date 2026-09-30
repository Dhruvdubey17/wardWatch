add_library(wardwatch_core
  ack.cpp
  cli.cpp
  encoding.cpp
  error.cpp
  loinc.cpp
  message.cpp
  metrics.cpp
  mllp.cpp
  payload.cpp
  poller.cpp
  server.cpp
  types.cpp
  validator.cpp)
target_include_directories(wardwatch_core
  PUBLIC ${PROJECT_SOURCE_DIR}/include ${WARDWATCH_GENERATED_DIR})
target_link_libraries(wardwatch_core
  PUBLIC nlohmann_json::nlohmann_json
  PRIVATE wardwatch_warnings)
find_package(Threads REQUIRED)
target_link_libraries(wardwatch_core PUBLIC Threads::Threads)
